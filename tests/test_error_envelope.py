"""S2 / M2: non-200 errors keep the flat envelope; probe failures stay 200."""
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
REASONS = set(capabilities.REASONS)


def _client(app):
    return TestClient(app, base_url=BASE, client=("127.0.0.1", 40000))


def _app(tmp_path, *, paper=False, configured=False):
    cfg = replace(load_config(CONFIG), root=tmp_path)
    app = create_app(cfg, force_paper=False, disable_live=paper)
    if configured:
        app.state.live_execution.connections["binance", "spot"].save(KEY, SECRET)
    return app


def _assert_flat_envelope(response, *, status_code):
    """D6: ``detail`` is a non-empty string, never a FastAPI validation list."""
    assert response.status_code == status_code
    body = response.json()
    assert isinstance(body, dict)
    assert {"detail", "code", "reason", "hint", "retryable"} <= set(body)
    assert isinstance(body["detail"], str)
    assert body["detail"].strip()
    assert not isinstance(body["detail"], (list, dict))
    assert body["code"] is None or (isinstance(body["code"], int) and not isinstance(body["code"], bool))
    assert body["reason"] in REASONS
    assert isinstance(body["hint"], str) and body["hint"].strip()
    assert isinstance(body["retryable"], bool)
    return body


CASES = [
    ("refresh_type", "/api/capabilities", {"refresh": "abc"}, 422),
    (
        "paper_403",
        "/api/live/account/audit",
        {"exchange": "binance", "market": "spot", "symbol": "BTCUSDT"},
        403,
    ),
    ("empty_symbol", "/api/futures/market", {"symbol": ""}, 422),
]


@pytest.mark.parametrize(
    "case,path,params,status_code", CASES, ids=[case[0] for case in CASES]
)
def test_reg_04_non_200_flat_envelope(tmp_path, case, path, params, status_code):
    """[REG-04] framework/guard 422 and 403 must match the 200+body.error shape."""
    app = _app(tmp_path, paper=(case == "paper_403"))
    body = _assert_flat_envelope(_client(app).get(path, params=params), status_code=status_code)
    if case == "paper_403":
        assert body["detail"] == "PAPER 模式已禁用实盘接口"
        assert body["reason"] == "PAPER_MODE"
        assert body["retryable"] is False
    else:
        assert "请求参数无效" in body["detail"]
        assert body["reason"] == "OUT_OF_SCOPE"
        assert body["retryable"] is False


def _public(url, params=None, *, timeout=5.0):
    return {
        "price": "76000",
        "markPrice": "76000",
        "data": {"BTC_USDT": {"last": "76000"}},
    }


def _failing_signed(error):
    def handler(method, path, params=None):
        raise error

    return handler


@pytest.mark.parametrize(
    "code,reason,state,retryable",
    [
        (-2015, "AUTH_REJECTED", "BLOCKED", True),
        (None, "PROBE_TIMEOUT", "UNKNOWN", True),
    ],
    ids=["auth_rejected", "probe_timeout"],
)
def test_reg_05_probe_failures_stay_200(tmp_path, monkeypatch, code, reason, state, retryable):
    """[REG-05] probe failures are data in a 200 body, never a non-200 envelope."""
    app = _app(tmp_path, configured=True)
    connector = app.state.live_execution.connections["binance", "spot"]
    monkeypatch.setattr(capabilities, "public_json", _public)
    monkeypatch.setattr(
        connector,
        "signed",
        _failing_signed(ExchangeRequestError(f"Binance {code}", code=code)),
    )

    response = _client(app).get(
        "/api/exchange/health", params={"exchange": "binance", "market": "spot"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["error"]["reason"] == reason
    assert body["error"]["retryable"] is retryable
    node = body["capabilities"]["account_read"]
    assert (node["state"], node["retryable"]) == (state, retryable)
    assert node["reason"] == reason
    # A 200 probe body is not the error envelope.
    assert "code" not in body
