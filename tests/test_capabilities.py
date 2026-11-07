import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from btcquant import capabilities
from btcquant.config import load_config
from btcquant.web import create_app


CONFIG = Path(__file__).resolve().parents[1] / "config.toml"
KEY = "fixture-key-12345678"
SECRET = "fixture-secret"
BASE = "http://127.0.0.1:8787"


def _client(app):
    return TestClient(app, base_url=BASE, client=("127.0.0.1", 40000))


def _app(tmp_path, *, paper=False, configured=False):
    cfg = replace(load_config(CONFIG), root=tmp_path)
    app = create_app(cfg, force_paper=False, disable_live=paper)
    if configured:
        app.state.live_execution.connections["binance", "spot"].save(KEY, SECRET)
    return app


def _fake_public(url, params=None, *, timeout=5.0):
    if url.endswith("/premiumIndex"):
        return {"markPrice": "76000", "indexPrice": "75990", "lastFundingRate": "0.0001"}
    if url.endswith("/ticker/price"):
        return {"price": "76000"}
    if "coinw.com/api/v1/public" in url:
        return {"data": {"BTC_USDT": {"last": "76000"}}}
    if "perpum/instruments" in url:
        return {"data": [{"status": "online"}]}
    raise AssertionError(f"unexpected public url: {url}")


def _fake_signed(counter):
    def signed(method, path, params=None):
        counter["n"] += 1
        if path == "/api/v3/account":
            return {"balances": [], "canTrade": True, "accountType": "SPOT"}
        if path == "/fapi/v2/account":
            return {"assets": [], "positions": [], "canTrade": True}
        if path == "/fapi/v2/positionRisk":
            return []
        if path == "/sapi/v1/account/apiRestrictions":
            return {"enableSpotAndMarginTrading": True, "enableFutures": True}
        raise AssertionError(f"unexpected signed path: {path}")
    return signed


def test_cap_01_contract_shape(tmp_path):
    app = _app(tmp_path, configured=True)
    response = _client(app).get("/api/capabilities")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"schema_version", "generated_at", "env", "refresh", "capabilities"}
    assert data["schema_version"] == 1
    assert data["env"] == "live"
    assert data["refresh"] is False
    expected_scopes = {
        ("binance", "spot"), ("binance", "futures"),
        ("coinw", "spot"), ("coinw", "futures"), ("hyperliquid", "futures"),
    }
    seen = set()
    for exchange, markets in data["capabilities"].items():
        for market, node_map in markets.items():
            seen.add((exchange, market))
            for capability in capabilities.CAPABILITY_IDS:
                node = node_map[capability]
                assert {"state", "reason", "hint", "retryable", "support",
                        "evidence", "code", "checked_at"} <= set(node)
                assert node["state"] in {"READY", "PARTIAL", "BLOCKED", "UNKNOWN"}
    assert seen == expected_scopes
    assert sum(len([key for key in node_map if key != "quote"])
               for markets in data["capabilities"].values()
               for node_map in markets.values()) == 25


@pytest.mark.parametrize("reason", sorted(capabilities.REASONS))
def test_cap_02_reason_enum_parity(reason):
    state, retryable = capabilities.REASONS[reason]
    node = capabilities.make_node(reason, capability="manual_order")
    assert (node["state"], node["retryable"]) == (state, retryable)
    assert node["hint"]
    with pytest.raises(KeyError):
        capabilities.make_node("NOT_A_REASON")


def test_cap_03_coverage_matrix(tmp_path, monkeypatch):
    app = _app(tmp_path, configured=True)
    calls = {"n": 0}
    connector = app.state.live_execution.connections["binance", "spot"]
    monkeypatch.setattr(connector, "signed", _fake_signed(calls))
    monkeypatch.setattr(capabilities, "public_json", _fake_public)
    monkeypatch.setattr(capabilities, "_hyperliquid_public_info", lambda: [])
    registry = app.state.capabilities

    client = _client(app)
    assert client.get("/api/capabilities").status_code == 200
    client.get("/api/capabilities", params={"refresh": 1})

    assert registry.probe_counts[("binance", "futures", "grid")] == 0
    assert registry.probe_counts[("coinw", "spot", "auto_strategy")] == 0
    assert registry.probe_counts[("coinw", "futures", "grid")] == 0
    assert registry.probe_counts[("hyperliquid", "futures", "grid")] == 0
    assert registry.probe_counts[("binance", "spot", "grid")] > 0

    data = client.get("/api/capabilities", params={"refresh": 1}).json()
    for scope, support_map in capabilities.SUPPORT.items():
        exchange, market = scope
        for capability, support in support_map.items():
            node = data["capabilities"][exchange][market][capability]
            assert node["support"] == support
            if support == "unsupported":
                assert node["state"] == "BLOCKED"
                assert node["reason"] == "NOT_IMPLEMENTED"
                assert node["retryable"] is False
                assert node["evidence"] == "config"
                assert node["checked_at"] is None


def test_cap_04_no_unprobed_ready(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("config-only response must not probe")

    app = _app(tmp_path)
    monkeypatch.setattr(capabilities, "public_json", forbidden)
    monkeypatch.setattr(capabilities, "_hyperliquid_public_info", forbidden)
    client = _client(app)
    data = client.get("/api/capabilities").json()
    for exchange, markets in data["capabilities"].items():
        for market, nodes in markets.items():
            for capability, node in nodes.items():
                if capability == "quote":
                    assert node == "USDT"
                    continue
                assert node["evidence"] == "config"
                assert node["checked_at"] is None
                assert node["state"] in {"UNKNOWN", "BLOCKED"}
                assert node["state"] != "READY"
                assert node["state"] != "PARTIAL"
                if node["support"] == "unsupported":
                    assert (node["state"], node["reason"]) == ("BLOCKED", "NOT_IMPLEMENTED")
                elif exchange == "hyperliquid" and capability in {"manual_order", "auto_strategy"}:
                    assert (node["state"], node["reason"]) == ("BLOCKED", "NOT_IMPLEMENTED")
                elif capability == "market_data":
                    assert (node["state"], node["reason"]) == ("UNKNOWN", "UNVERIFIED")
                elif exchange == "hyperliquid" and capability == "account_read":
                    assert (node["state"], node["reason"]) == ("UNKNOWN", "UNVERIFIED")
                else:
                    assert (node["state"], node["reason"]) == ("BLOCKED", "NOT_CONFIGURED")
    assert all(count == 0 for count in app.state.capabilities.probe_counts.values())

    paper = _app(tmp_path / "paper", paper=True)
    paper_nodes = _client(paper).get("/api/capabilities").json()["capabilities"]
    for markets in paper_nodes.values():
        for nodes in markets.values():
            for capability, node in nodes.items():
                if capability == "quote":
                    continue
                assert node["state"] in {"UNKNOWN", "BLOCKED"}
                assert node["evidence"] == "config"
                assert node["checked_at"] is None
            # PAPER: supported account capabilities are BLOCKED/PAPER_MODE.
            if nodes["account_read"]["support"] == "supported":
                assert nodes["account_read"]["reason"] == "PAPER_MODE"
                assert nodes["manual_order"]["reason"] == "PAPER_MODE"


def test_cap_05_cache_ttl_and_refresh(tmp_path, monkeypatch):
    app = _app(tmp_path, configured=True)
    registry = app.state.capabilities
    connector = app.state.live_execution.connections["binance", "spot"]
    calls = {"n": 0}
    monkeypatch.setattr(connector, "signed", _fake_signed(calls))
    monkeypatch.setattr(
        capabilities, "public_json",
        lambda *args, **kwargs: (calls.__setitem__("n", calls["n"] + 1) or {"price": "76000",
                                                                         "markPrice": "76000"}),
    )
    tick = {"n": 0}

    def fake_now():
        tick["n"] += 1
        return f"2026-11-07T00:00:{tick['n']:02d}Z"

    monkeypatch.setattr(capabilities, "_now_iso", fake_now)
    monkeypatch.setattr(registry, "_clock", fake_now)
    client = _client(app)
    params = {"exchange": "binance", "market": "spot"}

    first = client.get("/api/capabilities", params={**params, "refresh": 1}).json()
    first_calls = calls["n"]
    first_checked = first["capabilities"]["binance"]["spot"]["market_data"]["checked_at"]
    assert first_calls > 0

    second = client.get("/api/capabilities", params=params).json()
    assert calls["n"] == first_calls
    assert second["capabilities"]["binance"]["spot"]["market_data"]["checked_at"] == first_checked

    third = client.get("/api/capabilities", params={**params, "refresh": 1}).json()
    assert calls["n"] > first_calls
    assert third["capabilities"]["binance"]["spot"]["market_data"]["checked_at"] != first_checked

    # Failures are cached too: the second call must not touch the venue again.
    def failing_signed(method, path, params=None):
        calls["n"] += 1
        raise capabilities_error()

    def capabilities_error():
        from btcquant.binance_spot import ExchangeRequestError
        return ExchangeRequestError("Binance -2015", code=-2015)

    monkeypatch.setattr(connector, "signed", failing_signed)
    failed = client.get("/api/capabilities", params={**params, "refresh": 1}).json()
    failed_calls = calls["n"]
    node = failed["capabilities"]["binance"]["spot"]["account_read"]
    assert node["reason"] == "AUTH_REJECTED"
    assert node["state"] == "BLOCKED"
    cached = client.get("/api/capabilities", params=params).json()
    assert calls["n"] == failed_calls
    assert cached["capabilities"]["binance"]["spot"]["account_read"]["reason"] == "AUTH_REJECTED"
    assert registry.probe_counts[("binance", "spot", "account_read")] > 0


def test_cap_06_features_compat(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("features must not probe")

    app = _app(tmp_path, configured=True)
    monkeypatch.setattr(capabilities, "public_json", forbidden)
    monkeypatch.setattr(capabilities, "_hyperliquid_public_info", forbidden)
    data = _client(app).get("/api/live/features").json()
    assert {"manual", "grid", "auto"} <= set(data)
    assert data["manual"] is True
    assert data["grid"] is True
    assert data["auto"] == ["binance"]
    assert "capabilities" in data
    assert all(count == 0 for count in app.state.capabilities.probe_counts.values())

    paper = _app(tmp_path / "paper", paper=True)
    paper_data = _client(paper).get("/api/live/features").json()
    assert paper_data["manual"] is False
    assert paper_data["grid"] is False
    assert paper_data["auto"] == []
    assert paper_data["env"] == "paper"
    assert all(count == 0 for count in paper.state.capabilities.probe_counts.values())


def _write_ledger(root, symbols, *, status="UNKNOWN"):
    """Fixture ledger under ``tmp_path``; the product itself never writes runtime/."""
    path = root / "runtime" / "live_orders.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    account = hashlib.sha256(KEY.encode()).hexdigest()
    db = sqlite3.connect(path)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY, account TEXT NOT NULL, exchange TEXT NOT NULL,
            market TEXT NOT NULL, symbol TEXT NOT NULL, created INTEGER NOT NULL,
            status TEXT NOT NULL, record TEXT NOT NULL
        )
        """
    )
    for index, symbol in enumerate(symbols, start=1):
        order_id = f"bq{index:032d}"
        record = {
            "id": order_id, "status": status, "exchange": "binance",
            "market": "spot", "symbol": symbol,
        }
        db.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
            (order_id, account, "binance", "spot", symbol, index, status,
             json.dumps(record, ensure_ascii=False)),
        )
    db.commit()
    db.close()
    return path


PROJECT_RUNTIME_LEDGER = Path(__file__).resolve().parents[1] / "runtime" / "live_orders.sqlite3"


def test_cap_07_pending_nodes_are_local_evidence(tmp_path, monkeypatch):
    """[CAP-07] ledger-derived PENDING_RECONCILE nodes must not claim a probe.

    Config-only path (``refresh=0``): the console reads the local ledger, so
    ``evidence`` is ``config``, ``checked_at`` is null and no venue call happens.
    """
    app = _app(tmp_path, configured=True)
    connector = app.state.live_execution.connections["binance", "spot"]
    calls = {"venue": 0}

    def forbidden(*args, **kwargs):
        calls["venue"] += 1
        raise AssertionError("config-only capabilities must not touch the venue")

    monkeypatch.setattr(connector, "signed", forbidden)
    monkeypatch.setattr(capabilities, "public_json", forbidden)

    # Two distinct symbols, inserted out of order, plus a duplicate row to prove
    # deduplication as well as sorting.
    ledger = _write_ledger(tmp_path, ["ETHUSDT", "BTCUSDT", "BTCUSDT"])
    digest = hashlib.sha256(ledger.read_bytes()).hexdigest()

    response = _client(app).get(
        "/api/capabilities", params={"exchange": "binance", "market": "spot"}
    )
    assert response.status_code == 200
    nodes = response.json()["capabilities"]["binance"]["spot"]
    for capability in ("manual_order", "auto_strategy", "grid"):
        node = nodes[capability]
        assert (node["state"], node["reason"]) == ("PARTIAL", "PENDING_RECONCILE")
        assert node["evidence"] == "config"
        assert node["checked_at"] is None
        assert node["blocked_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert nodes["account_read"]["reason"] == "UNVERIFIED"
    assert nodes["account_read"]["evidence"] == "config"

    assert calls["venue"] == 0
    assert all(count == 0 for count in app.state.capabilities.probe_counts.values())
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == digest
    assert not PROJECT_RUNTIME_LEDGER.exists()


def test_cap_08_probe_pending_nodes_stay_config(tmp_path, monkeypatch):
    """[CAP-08] even after a probe, PENDING_RECONCILE nodes stay local evidence."""
    app = _app(tmp_path, configured=True)
    connector = app.state.live_execution.connections["binance", "spot"]
    calls = {"n": 0}
    monkeypatch.setattr(connector, "signed", _fake_signed(calls))
    monkeypatch.setattr(capabilities, "public_json", _fake_public)
    _write_ledger(tmp_path, ["BTCUSDT", "ETHUSDT"])

    response = _client(app).get(
        "/api/capabilities",
        params={"exchange": "binance", "market": "spot", "refresh": 1},
    )
    assert response.status_code == 200
    nodes = response.json()["capabilities"]["binance"]["spot"]
    assert nodes["account_read"]["reason"] == "OK"
    assert nodes["account_read"]["evidence"] == "probe"
    assert nodes["market_data"]["evidence"] == "probe"
    for capability in ("manual_order", "auto_strategy", "grid"):
        node = nodes[capability]
        assert (node["state"], node["reason"]) == ("PARTIAL", "PENDING_RECONCILE")
        assert node["evidence"] == "config"
        assert node["checked_at"] is None
        assert node["blocked_symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert calls["n"] > 0
    assert not PROJECT_RUNTIME_LEDGER.exists()


def test_cap_09_unverified_is_never_ready(tmp_path):
    """[CAP-09] configured-but-unprobed nodes are UNKNOWN/UNVERIFIED, never READY."""
    app = _app(tmp_path, configured=True)
    data = _client(app).get(
        "/api/capabilities", params={"exchange": "binance", "market": "spot"}
    ).json()
    node = data["capabilities"]["binance"]["spot"]["account_read"]
    assert (node["state"], node["reason"]) == ("UNKNOWN", "UNVERIFIED")
    assert node["evidence"] == "config"
    assert node["checked_at"] is None
    assert node["state"] != "READY"
    assert capabilities.state_for("UNVERIFIED") == ("UNKNOWN", True)
