from dataclasses import replace
from threading import Event
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest

from btcquant.binance_spot import BinanceSpotReadOnly, ExchangeRequestError
from btcquant.broker import LiveExecution, LivePreflight
from btcquant.config import load_config
from btcquant.web import create_app


INTENT = {
    "exchange": "binance", "market": "spot", "symbol": "BTCUSDT",
    "type": "LIMIT", "side": "BUY", "quantity": "0.0001", "price": "75000",
}


def test_recover_unused_confirmation_invalidates_it_and_respects_origin(tmp_path, monkeypatch):
    origin = "http://127.0.0.1:8787"
    cfg = replace(load_config("config.toml"), root=tmp_path)
    app = create_app(cfg, force_paper=True)
    app.state.live_execution.preflight = None
    client = TestClient(app, base_url=origin, client=("127.0.0.1", 40000))
    connector = app.state.live_execution.connections["binance", "spot"]
    connector.save("fixture", "fixture-secret")
    monkeypatch.setattr(connector, "signed", lambda *a: pytest.fail("recovery must not contact Binance"))
    headers = {"Origin": origin}
    token = client.post("/api/live/preview", json=INTENT, headers=headers).json()["confirmation"]
    params = {"confirmation": token}
    assert client.post("/api/live/order/recover", json=params).status_code == 403
    assert client.post("/api/live/order/recover", json=params, headers=headers).json() == {"state": "not_submitted"}
    assert client.post("/api/live/order", json=params, headers=headers).status_code == 409
    assert client.post("/api/live/order/recover", json=params, headers=headers).json() == {"state": "not_submitted"}


def test_recover_waits_for_inflight_send_and_reuses_receipt(tmp_path, monkeypatch):
    connector = BinanceSpotReadOnly(tmp_path)
    connector.save("fixture", "fixture-secret")
    execution = LiveExecution(tmp_path, {("binance", "spot"): connector})
    token = execution.preview(INTENT.copy())["confirmation"]
    entered, release = Event(), Event()
    sent = []

    def order(method, path, params):
        sent.append((method, path, params))
        entered.set()
        assert release.wait(5)
        raise ExchangeRequestError("timeout", uncertain=True, code=-1007)

    monkeypatch.setattr(connector, "signed", order)
    with ThreadPoolExecutor(3) as executor:
        submit = executor.submit(execution.submit, token)
        try:
            assert entered.wait(5)
            recover = executor.submit(execution.recover_manual, token)
            duplicate = executor.submit(execution.submit, token)
            assert not recover.done()
        finally:
            release.set()
        result = submit.result(timeout=5)
        assert recover.result(timeout=5) == {"state": "recorded", "order": result}
        assert duplicate.result(timeout=5) == result
    assert result["status"] == "UNKNOWN"
    assert len(sent) == 1
    assert sent[0][2]["newClientOrderId"] == token
    with pytest.raises(ValueError, match="待核对"):
        execution.preview(INTENT.copy())


def test_restart_queries_persisted_intent_without_sending_again(tmp_path, monkeypatch):
    connector = BinanceSpotReadOnly(tmp_path)
    connector.save("fixture", "fixture-secret")
    connections = {("binance", "spot"): connector}
    execution = LiveExecution(tmp_path, connections)
    token = execution.preview(INTENT.copy())["confirmation"]
    calls = []

    def send(method, path, params):
        calls.append((method, params))
        if method == "POST":
            # The venue may have received the order before the process stops.
            raise RuntimeError("simulated interruption before response is persisted")
        if len(calls) == 2:
            raise ExchangeRequestError("Binance -2013", code=-2013)
        return {"symbol": INTENT["symbol"], "orderId": 71, "clientOrderId": token,
                "status": "PARTIALLY_FILLED", "executedQty": "0.00005", "cumulativeQuoteQty": "3.75"}

    monkeypatch.setattr(connector, "signed", send)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        execution.submit(token)
    restarted = LiveExecution(tmp_path, connections)
    assert restarted.recover_manual(token)["order"]["status"] == "SUBMITTING"
    with pytest.raises(ExchangeRequestError, match="-2013"):
        restarted.manage(token, "query")
    assert restarted.recover_manual(token)["order"]["status"] == "SUBMITTING"
    receipt = restarted.manage(token, "query")
    assert (receipt["status"], receipt["filled"], receipt["quote_filled"]) == ("PARTIALLY_FILLED", "0.00005", "3.75")
    assert restarted.submit(token) == receipt
    assert [method for method, _ in calls] == ["POST", "GET", "GET"]
    assert calls[-1][1]["origClientOrderId"] == token


def test_ticket_rules_and_preflight_use_the_same_spot_limits(monkeypatch):
    rules = {"quote_market": True, "quote_precision": 2, "filters": {
        "LOT_SIZE": {"minQty":"0.00001", "maxQty":"5", "stepSize":"0.00001"},
        "MARKET_LOT_SIZE": {"minQty":"0.00002", "maxQty":"3", "stepSize":"0.00002"},
        "PRICE_FILTER": {"minPrice":"0.01", "maxPrice":"1000000", "tickSize":"0.01"},
        "MIN_NOTIONAL": {"minNotional":"5", "applyToMarket": True},
        "NOTIONAL": {"minNotional":"10", "maxNotional":"50", "applyMinToMarket":False, "applyMaxToMarket":True},
    }}
    check = LivePreflight()
    monkeypatch.setattr(check, "rules", lambda *_: rules)
    monkeypatch.setattr(check, "public", lambda *_: {"price":"76000"})
    info = check.binance_spot_ticket_rules(rules)
    assert (info["market_step"], info["market_min_notional"], info["limit_min_notional"]) == ("0.00002", "5", "10")
    assert info["market_max_notional"] == "50"
    market = {**INTENT, "type":"MARKET", "quantity":None, "price":None, "quote":"5"}
    check.validate(market, None)
    with pytest.raises(ValueError, match="步长"):
        check.validate({**market, "quote":"5.001"}, None)
    with pytest.raises(ValueError, match="名义价值"):
        check.validate({**market, "quote":"4.99"}, None)
    with pytest.raises(ValueError, match="步长"):
        check.validate({**market, "side":"SELL", "quote":None, "quantity":"0.00013"}, None)
    with pytest.raises(ValueError, match="名义价值"):
        check.validate(INTENT, None)
