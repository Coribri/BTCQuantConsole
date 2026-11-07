from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from btcquant import capabilities
from btcquant.binance_spot import ExchangeRequestError
from btcquant.config import load_config
from btcquant.web import create_app


CONFIG = Path(__file__).resolve().parents[1] / "config.toml"
KEY = "fixture-key-12345678"
SECRET = "fixture-secret"
BASE = "http://127.0.0.1:8787"


def _client(app):
    return TestClient(app, base_url=BASE, client=("127.0.0.1", 40000))


def _app(tmp_path, *, paper=False, configured=True, exchange="binance", market="spot"):
    cfg = replace(load_config(CONFIG), root=tmp_path)
    app = create_app(cfg, force_paper=False, disable_live=paper)
    if configured:
        app.state.live_execution.connections[exchange, market].save(KEY, SECRET)
    return app


def _fake_public(url, params=None, *, timeout=5.0):
    return {"price": "76000", "markPrice": "76000", "data": {"BTC_USDT": {"last": "76000"}}}


def _signed(account=None, permissions=None, error=None):
    def handler(method, path, params=None):
        if path in {"/api/v3/account", "/fapi/v2/account"}:
            if error is not None:
                raise error
            return account if account is not None else {"balances": [], "canTrade": True}
        if path == "/fapi/v2/positionRisk":
            return []
        if path == "/sapi/v1/account/apiRestrictions":
            return permissions if permissions is not None else {
                "enableSpotAndMarginTrading": True, "enableFutures": True,
            }
        raise AssertionError(f"unexpected signed path: {path}")
    return handler


CASES = [
    ("auth_rejected", -2015, "AUTH_REJECTED", "BLOCKED", True, "Binance 拒绝认证"),
    ("auth_invalid_key", -2014, "AUTH_INVALID_KEY", "BLOCKED", True, "Binance API Key 无效"),
    ("signature_invalid", -1022, "SIGNATURE_INVALID", "BLOCKED", True, "交易所签名校验失败"),
    ("signature_invalid_6001", 6001, "SIGNATURE_INVALID", "BLOCKED", True, "交易所签名校验失败"),
    ("time_skew", -1021, "TIME_SKEW", "BLOCKED", True, "Binance 拒绝请求时间戳"),
    ("permission_6003", 6003, "PERMISSION_DENIED", "BLOCKED", True,
     "读取成功，但该 API Key 或账户没有当前市场的交易权限"),
    ("permission_2010", -2010, "PERMISSION_DENIED", "BLOCKED", True,
     "读取成功，但该 API Key 或账户没有当前市场的交易权限"),
    ("venue_unavailable", -1007, "VENUE_UNAVAILABLE", "UNKNOWN", True,
     "交易所暂时不可用或限流"),
    ("probe_timeout", None, "PROBE_TIMEOUT", "UNKNOWN", True, "读取失败或超时"),
    ("not_configured", "nokey", "NOT_CONFIGURED", "BLOCKED", True, "尚未配置该市场"),
    ("permission_denied_snapshot", "denied", "PERMISSION_DENIED", "BLOCKED", True,
     "读取成功，但该 API Key 或账户没有当前市场的交易权限"),
    ("unknown_permission", "unknown", "UNVERIFIED", "UNKNOWN", True, "尚未核验该能力"),
    ("paper", "paper", "PAPER_MODE", "BLOCKED", False, "当前为 PAPER 模式"),
]


@pytest.mark.parametrize(
    "case,code,reason,state,retryable,hint_prefix", CASES, ids=[case[0] for case in CASES]
)
def test_hlt_01_reason_mapping(
    tmp_path, monkeypatch, case, code, reason, state, retryable, hint_prefix
):
    paper = case == "paper"
    configured = case not in {"not_configured", "paper"}
    app = _app(tmp_path, paper=paper, configured=configured)
    connector = app.state.live_execution.connections["binance", "spot"]
    monkeypatch.setattr(capabilities, "public_json", _fake_public)

    if case not in {"not_configured", "paper"}:
        if case == "probe_timeout":
            monkeypatch.setattr(
                connector, "signed",
                _signed(error=ExchangeRequestError("Binance 读取超时", code=None)),
            )
        elif case == "permission_denied_snapshot":
            monkeypatch.setattr(
                connector, "signed", _signed(account={"balances": [], "canTrade": False})
            )
        elif case == "unknown_permission":
            monkeypatch.setattr(connector, "signed", _signed(permissions={}))
        else:
            monkeypatch.setattr(connector, "signed", _signed(error=ExchangeRequestError(
                f"Binance {code}", code=code
            )))

    response = _client(app).get(
        "/api/exchange/health", params={"exchange": "binance", "market": "spot"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["error"]["reason"] == reason
    assert data["error"]["retryable"] is retryable
    assert data["error"]["hint"].startswith(hint_prefix)
    node = data["capabilities"]["account_read"]
    assert (node["state"], node["retryable"]) == (state, retryable)
    assert node["reason"] == reason
    if paper:
        assert data["venue_calls"] == []
        assert data["read_ok"] is False


def test_hlt_02_secret_and_venue_calls(tmp_path, monkeypatch):
    app = _app(tmp_path)
    connector = app.state.live_execution.connections["binance", "spot"]
    monkeypatch.setattr(capabilities, "public_json", _fake_public)
    monkeypatch.setattr(connector, "signed", _signed())
    response = _client(app).get(
        "/api/exchange/health", params={"exchange": "binance", "market": "spot"}
    )
    assert response.status_code == 200
    text = response.text
    assert SECRET not in text
    assert "fixture-key-12345678" not in text
    assert "api_secret" not in text
    assert "signature" not in text
    data = response.json()
    masked = data["credential_masked"]
    assert masked != KEY
    assert KEY not in masked
    for call in data["venue_calls"]:
        assert set(call) == {"method", "path"}
        assert "?" not in call["path"]
        assert "=" not in call["path"]
    assert data["venue_calls"]
    assert all(call["method"] == "GET" for call in data["venue_calls"])

    # CoinW's private read endpoint is POST by venue design; record it honestly.
    coinw_app = _app(tmp_path / "coinw", exchange="coinw", market="spot")
    coinw = coinw_app.state.live_execution.connections["coinw", "spot"]
    monkeypatch.setattr(capabilities, "public_json", _fake_public)
    monkeypatch.setattr(coinw, "signed", lambda command, params=None: {"USDT": "10"})
    coinw_response = _client(coinw_app).get(
        "/api/exchange/health", params={"exchange": "coinw", "market": "spot"}
    )
    assert coinw_response.status_code == 200
    calls = coinw_response.json()["venue_calls"]
    assert calls == [{"method": "POST", "path": "/api/v1/private"}]

    # PAPER: zero venue calls and zero signing.
    paper = _app(tmp_path / "paper", paper=True)
    paper_connector = paper.state.live_execution.connections["binance", "spot"]
    sent = {"n": 0}

    def forbidden(*args, **kwargs):
        sent["n"] += 1
        raise AssertionError("PAPER must not call the venue")

    monkeypatch.setattr(paper_connector, "signed", forbidden)
    paper_response = _client(paper).get(
        "/api/exchange/health", params={"exchange": "binance", "market": "spot"}
    )
    assert paper_response.status_code == 200
    assert paper_response.json()["venue_calls"] == []
    assert sent["n"] == 0


def test_hlt_03_attribution(tmp_path, monkeypatch):
    monkeypatch.setattr(capabilities, "public_json", _fake_public)
    app = _app(tmp_path)
    connector = app.state.live_execution.connections["binance", "spot"]
    monkeypatch.setattr(connector, "signed", _signed(error=ExchangeRequestError(
        "Binance -2015", code=-2015
    )))
    data = _client(app).get(
        "/api/exchange/health", params={"exchange": "binance", "market": "spot"}
    ).json()
    attribution = data["attribution"]
    assert attribution["confidence"] == "undetermined"
    assert [item["id"] for item in attribution["candidates"]] == [
        "api_key_invalid", "ip_not_whitelisted", "permission_missing",
    ]
    assert all(item["likelihood"] == "equal" for item in attribution["candidates"])

    skew_app = _app(tmp_path / "skew")
    skew = skew_app.state.live_execution.connections["binance", "spot"]
    monkeypatch.setattr(skew, "signed", _signed(error=ExchangeRequestError(
        "Binance -1021", code=-1021
    )))
    skew_data = _client(skew_app).get(
        "/api/exchange/health", params={"exchange": "binance", "market": "spot"}
    ).json()
    assert skew_data["attribution"]["confidence"] == "determined"
    assert [item["id"] for item in skew_data["attribution"]["candidates"]] == ["clock_skew"]
