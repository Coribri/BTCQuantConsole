import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from btcquant.binance_spot import ExchangeRequestError
from btcquant.config import load_config
from btcquant.web import create_app


CONFIG = Path(__file__).resolve().parents[1] / "config.toml"
KEY = "fixture-key-12345678"
SECRET = "fixture-secret"
ACCOUNT = hashlib.sha256(KEY.encode()).hexdigest()
BASE = "http://127.0.0.1:8787"
SYMBOL = "BTCUSDT"


def _client(app):
    return TestClient(app, base_url=BASE, client=("127.0.0.1", 40000))


def _app(tmp_path, *, paper=False, exchange="binance", market="spot", configured=True):
    cfg = replace(load_config(CONFIG), root=tmp_path)
    app = create_app(cfg, force_paper=False, disable_live=paper)
    if configured:
        app.state.live_execution.connections[exchange, market].save(KEY, SECRET)
    return app


def _write_ledger(root, rows):
    path = root / "runtime" / "live_orders.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
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
    for row in rows:
        db.execute(
            "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
            (
                row["id"], row.get("account", ACCOUNT), row.get("exchange", "binance"),
                row.get("market", "spot"), row.get("symbol", SYMBOL), row.get("created", 1),
                row["status"], json.dumps(row["record"], ensure_ascii=False),
            ),
        )
    db.commit()
    db.close()
    return path


def _local(
    order_id,
    *,
    status="FILLED",
    venue_order_id=None,
    client_order_id=None,
    filled="0.0001",
    quote_filled="7.5",
    created_at="2026-11-06T10:00:00Z",
    account=None,
    market="spot",
    symbol=SYMBOL,
):
    record = {
        "id": order_id, "status": status, "exchange": "binance", "market": market,
        "symbol": symbol, "order_id": venue_order_id,
        "client_order_id": client_order_id, "filled": filled,
        "quote_filled": quote_filled, "created_at": created_at,
    }
    return {
        "id": order_id, "account": account or ACCOUNT, "exchange": "binance",
        "market": market, "symbol": symbol, "created": 1, "status": status,
        "record": record,
    }


def _venue(order_id=71, *, client="bqfixture", status="FILLED", executed="0.0001", cum="7.5"):
    return {
        "orderId": order_id, "clientOrderId": client, "status": status,
        "executedQty": executed, "cummulativeQuoteQty": cum,
        "time": 1762423200000,
    }


def _signed(*, orders=None, account_error=None, all_error=None):
    calls = []

    def handler(method, path, params=None):
        calls.append((method, path, params))
        if path == "/api/v3/account":
            if account_error is not None:
                raise account_error
            return {"balances": [{"asset": "USDT", "free": "100", "locked": "0"}],
                    "canTrade": True}
        if path == "/api/v3/openOrders":
            return []
        if path == "/api/v3/allOrders":
            if all_error is not None:
                raise all_error
            return orders or []
        raise AssertionError(f"unexpected path: {path}")

    handler.calls = calls
    return handler


def _app_with(tmp_path, monkeypatch, *, ledger_rows=None, signed=None):
    app = _app(tmp_path)
    connector = app.state.live_execution.connections["binance", "spot"]
    monkeypatch.setattr(connector, "signed", signed or _signed())
    if ledger_rows is not None:
        _write_ledger(tmp_path, ledger_rows)
    return app, connector


SCENARIOS = [
    "account_unreadable", "query_failed", "uncertain", "match",
    "status_mismatch", "local_only", "rejected", "exchange_only",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_aud_01_diff_rules(tmp_path, monkeypatch, scenario):
    if scenario == "account_unreadable":
        app, _ = _app_with(
            tmp_path, monkeypatch,
            ledger_rows=[_local("bq1", status="FILLED", venue_order_id="71")],
            signed=_signed(account_error=ExchangeRequestError("Binance -2015", code=-2015)),
        )
        expected = ("pending", "EXCHANGE_UNREADABLE")
    elif scenario == "query_failed":
        app, _ = _app_with(
            tmp_path, monkeypatch,
            ledger_rows=[_local("bq1", status="FILLED", venue_order_id="71")],
            signed=_signed(all_error=ExchangeRequestError("Binance -2013", code=-2013)),
        )
        expected = ("pending", "EXCHANGE_QUERY_FAILED")
    elif scenario == "uncertain":
        app, _ = _app_with(
            tmp_path, monkeypatch,
            ledger_rows=[_local("bq1", status="UNKNOWN")],
            signed=_signed(orders=[]),
        )
        expected = ("pending", "PENDING_RECONCILE")
    elif scenario == "match":
        app, _ = _app_with(
            tmp_path, monkeypatch,
            ledger_rows=[
                _local("bq1", status="FILLED", venue_order_id="71"),
                _local("bq2", status="ACKNOWLEDGED", venue_order_id="72"),
            ],
            signed=_signed(orders=[_venue(71), _venue(72)]),
        )
        expected = ("match", "OK")
    elif scenario == "status_mismatch":
        app, _ = _app_with(
            tmp_path, monkeypatch,
            ledger_rows=[_local("bq1", status="FILLED", venue_order_id="71")],
            signed=_signed(orders=[_venue(71, status="CANCELED")]),
        )
        expected = ("pending", "STATUS_MISMATCH")
    elif scenario == "local_only":
        app, _ = _app_with(
            tmp_path, monkeypatch,
            ledger_rows=[_local("bq1", status="FILLED", venue_order_id="71")],
            signed=_signed(orders=[]),
        )
        expected = ("local_only", "LOCAL_ORDER_NOT_ON_VENUE")
    elif scenario == "rejected":
        app, _ = _app_with(
            tmp_path, monkeypatch,
            ledger_rows=[_local("bq1", status="REJECTED")],
            signed=_signed(orders=[]),
        )
        expected = ("match", "OK")
    else:
        app, _ = _app_with(
            tmp_path, monkeypatch,
            ledger_rows=[],
            signed=_signed(orders=[_venue(71)]),
        )
        expected = ("exchange_only", "VENUE_ORDER_WITHOUT_LOCAL")

    data = _client(app).get(
        "/api/live/account/audit",
        params={"exchange": "binance", "market": "spot", "symbol": SYMBOL},
    ).json()
    assert data["summary"]["total"] == len(data["diffs"])
    assert data["summary"]["match"] + data["summary"]["local_only"] + \
        data["summary"]["exchange_only"] + data["summary"]["pending"] == data["summary"]["total"]
    assert data["diffs"]
    assert (data["diffs"][0]["kind"], data["diffs"][0]["reason"]) == expected
    if scenario == "match":
        assert all((diff["kind"], diff["reason"]) == ("match", "OK") for diff in data["diffs"])


def _table_snapshot(path):
    db = sqlite3.connect(path)
    rows = db.execute("SELECT id,status,record FROM orders ORDER BY id").fetchall()
    db.close()
    return rows


def test_aud_02_readonly_idempotent(tmp_path, monkeypatch):
    app, connector = _app_with(
        tmp_path, monkeypatch,
        ledger_rows=[
            _local("bq1", status="FILLED", venue_order_id="71"),
            _local("bq2", status="UNKNOWN"),
        ],
        signed=_signed(orders=[_venue(71)]),
    )
    path = tmp_path / "runtime" / "live_orders.sqlite3"
    before = _table_snapshot(path)
    for name in ("preview", "submit", "manage"):
        monkeypatch.setattr(
            app.state.live_execution, name,
            lambda *args, _name=name, **kwargs: (_ for _ in ()).throw(
                AssertionError(f"audit must not call {_name}")
            ),
        )
    client = _client(app)
    first = client.get("/api/live/account/audit",
                       params={"exchange": "binance", "market": "spot", "symbol": SYMBOL})
    second = client.get("/api/live/account/audit",
                        params={"exchange": "binance", "market": "spot", "symbol": SYMBOL})
    assert first.status_code == second.status_code == 200
    first_data, second_data = first.json(), second.json()
    for data in (first_data, second_data):
        assert data["venue_calls"]
        for call in data["venue_calls"]:
            assert call["method"] == "GET"
            assert "?" not in call["path"]
            assert "=" not in call["path"]
    assert _table_snapshot(path) == before
    for key in ("summary", "diffs", "read_ok", "local_facts", "exchange_facts", "venue_calls"):
        assert first_data[key] == second_data[key]

    empty_root = tmp_path / "fresh"
    empty_root.mkdir()
    empty_app = _app(empty_root, configured=False)
    empty = _client(empty_app).get(
        "/api/live/account/audit",
        params={"exchange": "binance", "market": "spot", "symbol": SYMBOL},
    )
    assert empty.status_code == 200
    assert not (empty_root / "runtime" / "live_orders.sqlite3").exists()


def test_aud_03_scope_isolation(tmp_path, monkeypatch):
    other_account = "f" * 64
    rows = [
        _local("bqmatch", status="FILLED", venue_order_id="71"),
        _local("bqaccount", status="FILLED", account=other_account),
        _local("bqmarket", status="FILLED", market="futures"),
        _local("bqsymbol", status="FILLED", symbol="ETHUSDT"),
    ]
    app, _ = _app_with(
        tmp_path, monkeypatch, ledger_rows=rows,
        signed=_signed(orders=[_venue(71)]),
    )
    response = _client(app).get(
        "/api/live/account/audit",
        params={"exchange": "binance", "market": "spot", "symbol": SYMBOL},
    )
    data = response.json()
    assert len(data["diffs"]) == 1
    assert data["diffs"][0]["ref"] == "bqmatch"
    assert data["account_ref"] == ACCOUNT[:8]
    assert len(data["account_ref"]) == 8
    assert data["account_ref"] != KEY
    assert KEY not in response.text


def test_aud_04_envelope_conservation(tmp_path, monkeypatch):
    app, _ = _app_with(
        tmp_path, monkeypatch,
        ledger_rows=[_local("bq1", status="FILLED", venue_order_id="71")],
        signed=_signed(orders=[_venue(71)]),
    )
    data = _client(app).get(
        "/api/live/account/audit",
        params={"exchange": "binance", "market": "spot", "symbol": SYMBOL},
    ).json()
    assert {"checked_at", "fetched_at", "read_ok", "partial", "read_error",
            "local_facts", "exchange_facts", "summary", "venue_calls"} <= set(data)
    # fetched_at lives inside the facts blocks in this contract version.
    assert data["local_facts"]["fetched_at"]
    assert data["exchange_facts"]["fetched_at"]
    summary = data["summary"]
    assert summary["total"] == len(data["diffs"])
    assert summary["match"] + summary["local_only"] + summary["exchange_only"] + \
        summary["pending"] == summary["total"]
    assert data["exchange_facts"]["basis"] == "venue_only"
    assert data["exchange_facts"]["balances"]
    for diff in data["diffs"]:
        assert diff["entity"] == "order"
        assert diff["kind"] in {"match", "local_only", "exchange_only", "pending"}


def test_aud_05_non_binance_and_paper(tmp_path, monkeypatch):
    coinw_app = _app(tmp_path / "coinw", exchange="coinw", market="spot")
    data = _client(coinw_app).get(
        "/api/live/account/audit",
        params={"exchange": "coinw", "market": "spot", "symbol": SYMBOL},
    ).json()
    assert data["read_ok"] is False
    assert data["read_error"]["reason"] == "NOT_IMPLEMENTED"
    assert data["venue_calls"] == []

    paper = _app(tmp_path / "paper", paper=True)
    response = _client(paper).get(
        "/api/live/account/audit",
        params={"exchange": "binance", "market": "spot", "symbol": SYMBOL},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["detail"] == "PAPER 模式已禁用实盘接口"
    assert body["code"] is None
    assert body["reason"] == "PAPER_MODE"
    assert body["retryable"] is False
    assert body["hint"]
