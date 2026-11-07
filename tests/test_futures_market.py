from dataclasses import replace
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from btcquant import capabilities
from btcquant.binance_spot import ExchangeRequestError
from btcquant.config import load_config
from btcquant.web import create_app


CONFIG = Path(__file__).resolve().parents[1] / "config.toml"
BASE = "http://127.0.0.1:8787"
NEXT_FUNDING_MS = 1762521600000


def _client(app):
    return TestClient(app, base_url=BASE, client=("127.0.0.1", 40000))


def _app(tmp_path, *, paper=False):
    cfg = replace(load_config(CONFIG), root=tmp_path)
    return create_app(cfg, force_paper=False, disable_live=paper)


def _public(url, params=None, *, timeout=5.0):
    if url.endswith("/premiumIndex"):
        return {
            "markPrice": "76011.2", "indexPrice": "76005.1",
            "lastFundingRate": "0.0001", "nextFundingTime": NEXT_FUNDING_MS,
        }
    if url.endswith("/ticker/price"):
        return {"price": "76012.0"}
    raise AssertionError(f"unexpected public url: {url}")


def _utc(value: str) -> datetime:
    assert value.endswith("Z")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def test_fut_01_native_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(capabilities, "public_json", _public)
    data = _client(_app(tmp_path)).get("/api/futures/market").json()
    assert data["schema_version"] == 1
    assert data["exchange"] == "binance"
    assert data["market"] == "futures"
    assert data["symbol"] == "BTCUSDT"
    assert data["source"] == "binance_futures_public"
    assert data["source_label"] == "Binance Futures"
    assert data["native"] is True
    assert data["account_scoped"] is False
    assert data["mark_price"] > 0
    assert data["index_price"] == 76005.1
    assert data["last_price"] == 76012.0
    assert data["funding_rate"] == pytest.approx(0.0001)
    for key in ("mark_price", "index_price", "last_price", "funding_rate",
                "next_funding_at", "fetched_at", "latency_ms"):
        assert key in data
    assert _utc(data["next_funding_at"])
    assert _utc(data["fetched_at"])
    assert data["margin_mode"] is None
    assert data["position_mode"] is None
    assert {"margin_mode_unavailable", "position_mode_unavailable"} <= set(data["degradations"])


def test_fut_02_degradation(tmp_path, monkeypatch):
    monkeypatch.setattr(capabilities, "public_json", _public)

    # Configured account but venue read failure: still 200, fields downgraded.
    app = _app(tmp_path / "configured")
    connector = app.state.live_execution.connections["binance", "futures"]
    connector.save("fixture-key-12345678", "fixture-secret")
    monkeypatch.setattr(connector, "signed", lambda *args, **kwargs: (
        (_ for _ in ()).throw(ExchangeRequestError("Binance -2015", code=-2015))
    ))
    data = _client(app).get("/api/futures/market").json()
    assert data["margin_mode"] is None
    assert data["position_mode"] is None
    assert {"margin_mode_unavailable", "position_mode_unavailable"} <= set(data["degradations"])

    # PAPER: paper_mode degradation and zero signed requests.
    paper = _app(tmp_path / "paper", paper=True)
    paper_connector = paper.state.live_execution.connections["binance", "futures"]
    sent = {"n": 0}

    def forbidden(*args, **kwargs):
        sent["n"] += 1
        raise AssertionError("PAPER must not sign futures requests")

    monkeypatch.setattr(paper_connector, "signed", forbidden)
    paper_data = _client(paper).get("/api/futures/market").json()
    assert "paper_mode" in paper_data["degradations"]
    assert paper_data["margin_mode"] is None
    assert paper_data["position_mode"] is None
    assert sent["n"] == 0

    # Non-Binance exchange: explicit 422.
    response = _client(paper).get("/api/futures/market", params={"exchange": "coinw"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "NOT_IMPLEMENTED"
    assert body["reason"] == "NOT_IMPLEMENTED"

    # Public data failure: 502 with a flat error envelope.
    monkeypatch.setattr(
        capabilities, "public_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("boom")),
    )
    failed = _client(paper).get("/api/futures/market")
    assert failed.status_code == 502
    failed_body = failed.json()
    assert failed_body["code"] == "MARKET_DATA_UNAVAILABLE"
    assert failed_body["reason"] == "MARKET_DATA_UNAVAILABLE"
    assert isinstance(failed_body["detail"], str)
