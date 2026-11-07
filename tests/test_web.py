from btcquant.web import load_env_file


def test_env_loader_tolerates_extra_equals(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "BINANCE_TESTNET_API_KEY==key123\n"
        "BINANCE_TESTNET_API_SECRET==secret456\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_TESTNET_API_SECRET", raising=False)
    loaded = load_env_file(path)
    assert loaded == 2
    assert __import__("os").environ["BINANCE_TESTNET_API_KEY"] == "key123"
    assert __import__("os").environ["BINANCE_TESTNET_API_SECRET"] == "secret456"



def test_simulated_spot_and_futures_route_every_trade_to_its_symbol(tmp_path, monkeypatch):
    from dataclasses import replace
    from pathlib import Path
    from fastapi.testclient import TestClient

    from btcquant.broker import PaperBroker
    from btcquant.config import load_config
    from btcquant.futures_paper import FuturesPaperService
    from btcquant.service import TradingService
    from btcquant.web import create_app

    config_path = tmp_path / "config.toml"
    config_path.write_bytes(Path("config.toml").read_bytes())
    base = load_config(config_path)
    cfg = replace(base, market=replace(base.market, symbol="BNBUSDT"))
    prices = {"BTCUSDT": 80000, "ETHUSDT": 2200, "BNBUSDT": 695, "XRPUSDT": 1.2, "TRXUSDT": .3, "DOGEUSDT": .08}
    monkeypatch.setattr(TradingService, "start", lambda self: None)
    monkeypatch.setattr(TradingService, "poll", lambda self: None)
    monkeypatch.setattr(FuturesPaperService, "start", lambda self: None)
    monkeypatch.setattr(FuturesPaperService, "price", lambda self: prices[self.symbol])
    monkeypatch.setattr(PaperBroker, "price", lambda self: prices[self.symbol])
    client = TestClient(create_app(cfg, force_paper=True))
    spot = {"side": "BUY", "quote_amount": 20}
    futures = {"side": "LONG", "qty": 20 / prices["BNBUSDT"], "leverage": 5}

    assert client.post("/api/order?symbol=INVALID", json=spot).status_code == 422
    assert client.post("/api/futures/order?symbol=", json=futures).status_code == 422
    assert client.get("/api/status?symbol=BTCUSDT").json()["orders"] == []
    assert client.get("/api/futures/status?symbol=BTCUSDT").json()["orders"] == []
    for symbol, price in prices.items():
        base_asset = symbol.removesuffix("USDT")
        args = {"symbol": symbol.lower()}
        trade = client.post("/api/order", params=args, json=spot).json()
        assert trade["status"]["symbol"] == symbol
        assert 0.99 * price <= trade["order"]["price"] <= 1.01 * price
        assert f" {base_asset} @" in trade["status"]["events"][-1]["message"]
        position = client.post("/api/futures/order", params=args, json={**futures, "qty": 20 / price}).json()
        assert position["status"]["symbol"] == symbol
        assert 0.99 * price <= position["order"]["price"] <= 1.01 * price
        assert f" {base_asset} @" in position["status"]["events"][-1]["message"]

    closed = client.post("/api/futures/close", params={"symbol": "BNBUSDT"}).json()
    assert closed["status"]["positions"] == []
    assert client.get("/api/futures/status?symbol=BTCUSDT").json()["position"]["side"] == "LONG"
    sold = client.post("/api/order?symbol=BNBUSDT", json={"side": "SELL"}).json()
    assert sold["status"]["symbol"] == "BNBUSDT" and sold["order"]["side"] == "SELL"
    assert len(client.get("/api/status?symbol=BTCUSDT").json()["orders"]) == 1


def test_coinw_spot_account_isolated_and_read_only(tmp_path, monkeypatch):
    import hashlib
    from dataclasses import replace
    from types import SimpleNamespace
    from fastapi.testclient import TestClient

    from btcquant.config import load_config
    from btcquant.web import create_app

    cfg = replace(load_config("config.toml"), root=tmp_path)
    app = create_app(cfg, force_paper=True, disable_live=True)
    client = TestClient(app, base_url="http://127.0.0.1:8787", client=("127.0.0.1", 50000))
    spot = "/api/exchange/coinw-spot"
    key = {"api_key": "mock-api-key", "api_secret": "mock-api-secret"}
    called = []

    def read_balance(url, *, params, **kwargs):
        called.append(params)
        assert url == "https://api.coinw.com/api/v1/private"
        assert kwargs["allow_redirects"] is False
        signature = hashlib.md5(
            b"api_key=mock-api-key&secret_key=mock-api-secret", usedforsecurity=False
        ).hexdigest().upper()
        assert params == {"command": "returnBalances", "api_key": key["api_key"], "sign": signature}
        return SimpleNamespace(status_code=200, json=lambda: {"code": "200", "data": {"USDT": "125.5"}})

    monkeypatch.setattr("btcquant.coinw.requests.post", read_balance)
    blocked = client.post(spot + "/settings", json=key, headers={"Origin": "https://example.com"})
    assert blocked.status_code == 403
    assert client.get(spot).json()["configured"] is False
    saved = client.post(spot + "/settings", json=key)
    assert saved.json()["read_only"]
    assert saved.headers["Cache-Control"] == "no-store"
    assert key["api_secret"] not in saved.text
    assert client.get("/api/exchange/coinw").json()["configured"] is False
    result = client.post(spot + "/test").json()
    assert result["balances"] == [{"asset": "USDT", "free": "125.5"}]
    assert result["order_enabled"] is False
    assert len(called) == 1



def test_live_preflight_checks_exchange_filters_and_key_permission(tmp_path, monkeypatch):
    from dataclasses import replace
    from fastapi.testclient import TestClient

    from btcquant.config import load_config
    from btcquant.web import create_app

    origin = "http://127.0.0.1:8787"
    app = create_app(replace(load_config("config.toml"), root=tmp_path), force_paper=True)
    client = TestClient(app, base_url=origin, client=("127.0.0.1", 40000), headers={"Origin": origin})
    execution = app.state.live_execution
    execution.preflight.risk = None
    connector = execution.connections["binance", "spot"]
    connector.save("fixture", "fixture-secret")
    filters = [
        {"filterType":"PRICE_FILTER", "minPrice":"0.01", "maxPrice":"1000000", "tickSize":"0.01"},
        {"filterType":"LOT_SIZE", "minQty":"0.00001", "maxQty":"1", "stepSize":"0.00001"},
        {"filterType":"MARKET_LOT_SIZE", "minQty":"0", "maxQty":"0.5", "stepSize":"0"},
        {"filterType":"NOTIONAL", "minNotional":"5", "maxNotional":"100000",
         "applyMinToMarket":True, "applyMaxToMarket":True},
    ]
    def public(url, params=None):
        assert params == {"symbol":"BTCUSDT"}
        if url.endswith("/exchangeInfo"):
            return {"symbols":[{"symbol":"BTCUSDT", "status":"TRADING", "filters":filters,
                                "quoteAssetPrecision":8, "quoteOrderQtyMarketAllowed":True}]}
        assert url.endswith("/avgPrice")
        return {"price":"80000"}
    monkeypatch.setattr(execution.preflight, "public", public)
    permission = {"allowed":False}
    monkeypatch.setattr(connector, "snapshot", lambda: {"configured":True, "can_trade":True, "balances":[]})
    monkeypatch.setattr(connector, "key_permissions", lambda: {"enableSpotAndMarginTrading":permission["allowed"]})
    writes = []
    monkeypatch.setattr(connector, "signed", lambda *args: writes.append(args))
    market = {"exchange":"binance", "market":"spot", "symbol":"BTCUSDT", "side":"BUY", "type":"MARKET", "quote":"1"}
    limit = {**market, "type":"LIMIT", "quote":None, "price":"80000", "quantity":"0.001"}
    path = "/api/live/preview"
    assert "名义价值" in client.post(path, json=market).json()["detail"]
    assert "步长" in client.post(path, json={**limit,"quantity":"0.001001"}).json()["detail"]
    assert "步长" in client.post(path, json={**limit,"price":"80000.001"}).json()["detail"]
    assert "上限" in client.post(path, json={**limit,"quantity":"1.01"}).json()["detail"]
    assert "交易权限" in client.post(path, json=limit).json()["detail"]
    assert client.get("/api/live/account?exchange=binance&market=spot&symbol=BTCUSDT").json()["trade_permission"] == "denied"
    permission["allowed"] = True
    preview = client.post(path, json=limit).json()
    permission["allowed"] = False
    assert client.post("/api/live/order", json={"confirmation":preview["confirmation"]}).status_code == 409
    assert writes == []


def test_coinw_contract_cap_uses_public_instrument_metadata(monkeypatch):
    import pytest
    from btcquant.broker import LivePreflight

    check = LivePreflight()
    def public(url, params):
        assert url.endswith("/v1/perpum/instruments") and params == {"name":"ETH"}
        return {"data":[{"base":"eth", "quote":"usdt", "status":"online", "minSize":1,
                         "oneMaxPosition":10000, "oneLotSize":"0.01", "pricePrecision":2, "maxLeverage":40}]}
    monkeypatch.setattr(check, "public", public)
    order = {"exchange":"coinw", "market":"futures", "symbol":"ETHUSDT", "type":"LIMIT",
             "quantity":"0.02", "price":"2300.01", "action":"OPEN", "leverage":40}
    assert check.leverage(None, "coinw", "ETHUSDT")["manual_max"] == 40
    check.validate(order, None)
    with pytest.raises(ValueError, match="杠杆.*40"):
        check.validate({**order,"leverage":41}, None)
    with pytest.raises(ValueError, match="合约张数.*步长"):
        check.validate({**order,"quantity":"0.021"}, None)


def test_live_risk_blocks_new_futures_exposure_and_flatten_stops_on_unknown(tmp_path, monkeypatch):
    import time
    import pytest
    from types import SimpleNamespace

    from btcquant.broker import LiveExecution, LivePreflight
    from btcquant.grid_paper import LiveStrategies

    current = {"margin":"100", "exposure":"25", "income":[]}
    position = {"symbol":"BTCUSDT", "notional":"25", "positionAmt":"0.002", "positionSide":"LONG"}
    short = {"symbol":"ETHUSDT", "notional":"10", "positionAmt":"-0.005", "positionSide":"BOTH"}
    def signed(method, path, params=None):
        if path == "/fapi/v3/account":
            return {"totalInitialMargin":current["margin"]}
        if path == "/fapi/v3/positionRisk":
            return [{**position,"notional":current["exposure"]}] if params else [position, short]
        if path == "/fapi/v1/income":
            return current["income"]
        assert path in {"/fapi/v1/openOrders","/fapi/v1/openAlgoOrders"}
        return []
    adapter = SimpleNamespace(signed=signed)
    preflight = LivePreflight()
    execution = LiveExecution(tmp_path, {("binance","futures"):adapter}, preflight=preflight)
    runner = LiveStrategies(tmp_path, execution, None)
    runner.risk_settings({"max_order":250,"max_symbol":500,"max_margin":500,"max_daily_loss":100,"max_losses":3})
    plan = {"market":"futures", "symbol":"BTCUSDT", "amount":"50"}
    runner._risk_check(plan, adapter, 5)
    with pytest.raises(ValueError, match="单笔"):
        runner._risk_check({**plan,"amount":"1251"}, adapter, 5)
    current["margin"] = "499"
    with pytest.raises(ValueError, match="保证金"):
        runner._risk_check(plan, adapter, 5)
    current["margin"] = "100"
    current["exposure"] = "475"
    with pytest.raises(ValueError, match="敞口"):
        runner._risk_check(plan, adapter, 5)
    current["exposure"] = "25"
    now = int(time.time() * 1000)
    current["income"] = [{"incomeType":"REALIZED_PNL","asset":"USDT","time":now,"income":"-101"}]
    with pytest.raises(ValueError, match="当日亏损"):
        runner._risk_check(plan, adapter, 5)
    current["income"] = [{"incomeType":"REALIZED_PNL","asset":"USDT","time":now,"income":"-1"}] * 3
    with pytest.raises(ValueError, match="连续亏损"):
        runner._risk_check(plan, adapter, 5)

    monkeypatch.setattr(execution, "_context", lambda *_: (adapter, "fixture-account"))
    monkeypatch.setattr(preflight, "ensure_trade", lambda *_: None)
    sent = []
    def submit(row, _order_id, _account):
        sent.append(row)
        return {"status":"UNKNOWN"}
    monkeypatch.setattr(execution, "strategy_order", submit)
    review = runner.preview_flatten()
    assert review["positions"] == [
        {"symbol":"BTCUSDT","position":"LONG","quantity":"0.002"},
        {"symbol":"ETHUSDT","position":"SHORT","quantity":"0.005"},
    ]
    assert sent == []
    result = runner.flatten(review["confirmation"])
    assert len(sent) == 1 and sent[0]["action"] == "CLOSE" and result["remaining"] == 1
    with pytest.raises(ValueError, match="过期"):
        runner.flatten(review["confirmation"])


def test_live_order_confirmation_and_unknown_reconciliation(tmp_path, monkeypatch):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from btcquant.binance_spot import BinanceSpotReadOnly, ExchangeRequestError
    from btcquant.config import load_config
    from btcquant.web import create_app

    cfg = replace(load_config("config.toml"), root=tmp_path)
    origin = "http://127.0.0.1:8787"
    app = create_app(cfg, force_paper=True)
    app.state.live_execution.preflight = None
    client = TestClient(app, base_url=origin, client=("127.0.0.1", 40000))
    assert client.post("/api/exchange/binance/settings", json={"api_key":"fixture", "api_secret":"fixture-secret"}).is_success
    calls = []
    def exchange(self, method, path, params=None):
        calls.append((method, path, params))
        if method == "POST":
            raise ExchangeRequestError("timeout", uncertain=True)
        status = "NEW" if method == "GET" else "CANCELED"
        id_key = "clientOrderId" if method == "GET" else "origClientOrderId"
        return {"symbol":params["symbol"], "orderId":71, id_key:params["origClientOrderId"], "status":status}

    monkeypatch.setattr(BinanceSpotReadOnly, "signed", exchange)
    headers = {"Origin":origin}
    intent = {"exchange":"binance","market":"spot","symbol":"BTCUSDT","side":"BUY",
              "type":"LIMIT","quantity":"0.001","price":"12345"}
    assert client.post("/api/live/preview", json=intent).status_code == 403
    result = client.post("/api/live/preview", json=intent, headers=headers).json()
    token = result["confirmation"]
    assert calls == []
    first = client.post("/api/live/order", json={"confirmation":token}, headers=headers).json()
    assert first["status"] == "UNKNOWN"
    assert client.post("/api/live/order", json={"confirmation":token}, headers=headers).json() == first
    assert len(calls) == 1
    assert calls[0][2]["newClientOrderId"] == token
    assert calls[0][2]["quantity"] == "0.001"
    assert client.post("/api/live/order/cancel", json={"id":token}, headers=headers).status_code == 409
    assert client.post("/api/live/order/query", json={"id":token}, headers=headers).json()["status"] == "NEW"
    assert client.post("/api/live/order/cancel", json={"id":token}, headers=headers).json()["status"] == "CANCELED"
    assert [x[0] for x in calls] == ["POST","GET","DELETE"]

    restarted = TestClient(create_app(cfg, force_paper=True), base_url=origin, client=("127.0.0.1", 40000))
    order = restarted.post("/api/live/order", json={"confirmation":token}, headers=headers).json()
    assert order["status"] == "CANCELED"
    assert len(calls) == 3


def test_hyperliquid_signed_testnet_limit_order_and_durable_cloid(tmp_path, monkeypatch):
    from dataclasses import replace
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    from fastapi.testclient import TestClient

    from btcquant.config import load_config
    from btcquant.web import create_app

    origin = "http://127.0.0.1:8787"
    cfg = replace(load_config("config.toml"), root=tmp_path)
    app = create_app(cfg, force_paper=True)
    client = TestClient(app, base_url=origin, client=("127.0.0.1", 40000), headers={"Origin": origin})
    venue = app.state.live_execution.connections["hyperliquid", "futures"]
    owner, other = Account.create(), Account.create()
    base = "/api/live/dex/hyperliquid"
    writes = []

    def transport(action, nonce, signature, expires=None):
        writes.append((action, nonce, signature, expires))
        if action["type"] == "approveAgent":
            assert action["hyperliquidChain"] == "Testnet"
            return {"type": "default"}
        if action["type"] == "order":
            return {"type": "order", "data": {"statuses": [{"resting": {"oid": 47}}]}}
        return {"type": "cancel", "data": {"statuses": ["success"]}}

    def info(network, kind, **fields):
        assert network == "testnet"
        if kind == "metaAndAssetCtxs":
            return [{"universe": [{"name": "BTC", "szDecimals": 5}]}, [{"markPx": "80000"}]]
        if kind == "clearinghouseState":
            return {"assetPositions": []}
        assert kind == "orderStatus"
        return {"status": "order", "order": {"status": "open", "order": {
            "oid": 47, "coin": "BTC", "cloid": fields["oid"], "origSz": "0.001", "sz": "0.001",
        }}}

    monkeypatch.setattr(venue, "_exchange", transport)
    monkeypatch.setattr(venue, "_info", info)

    def approve(signer):
        grant = client.post(base + "/agent/prepare", json={"address": owner.address, "chain_id": 1}).json()
        message = encode_typed_data(full_message=grant["typed_data"])
        signature = "0x" + signer.sign_message(message).signature.hex()
        return client.post(base + "/agent/enable", json={"token": grant["token"], "signature": signature})

    assert approve(other).status_code == 409 and not writes
    status = approve(owner).json()
    assert status["address"] == owner.address.lower() and status["agent"] != owner.address
    intent = {"network": "testnet", "address": owner.address, "symbol": "BTCUSDT", "position": "SHORT",
              "action": "OPEN", "type": "LIMIT", "quantity": "0.001", "price": "84000"}
    assert client.post(base + "/preview", json={**intent, "network": "mainnet"}).status_code == 422
    assert client.post(base + "/preview", json={**intent, "address": other.address}).status_code == 409
    preview = client.post(base + "/preview", json=intent).json()
    token = preview["confirmation"]
    assert len(writes) == 1
    response = client.post("/api/live/order", json={"confirmation": token}).json()
    assert response["status"] == "NEW"
    assert client.post("/api/live/order", json={"confirmation": token}).json() == response
    order, nonce, _, expires = writes[-1]
    assert order["orders"][0]["c"] == "0x" + token[2:]
    assert order["orders"][0]["b"] is False and order["orders"][0]["r"] is False and expires > nonce
    assert client.post("/api/live/order/query", json={"id": token}).json()["status"] == "NEW"
    assert client.post("/api/live/order/cancel", json={"id": token}).json()["status"] == "CANCELED"
    assert writes[-1][0] == {"type": "cancelByCloid", "cancels": [{"asset": 0, "cloid": "0x" + token[2:]}]}
    assert client.post(base + "/agent/disable", json={}).json()["agent"] is None
    assert client.post(base + "/preview", json=intent).status_code == 409


def test_hyperliquid_auto_only_opens_on_closed_signal_and_checks_exchange_stop(tmp_path, monkeypatch):
    from eth_account import Account
    import pandas as pd

    from btcquant.broker import HyperliquidTrading, LiveExecution
    from btcquant.grid_paper import HyperliquidAuto
    from btcquant.models import ModelRegistry

    venue = HyperliquidTrading()
    venue.owner, venue.agent = Account.create().address.lower(), Account.create()
    execution = LiveExecution(tmp_path, {("hyperliquid", "futures"): venue})
    models = ModelRegistry(tmp_path, 8787)
    auto = HyperliquidAuto(venue, execution, models)
    bar = pd.Timestamp.now(tz="UTC").floor("1h")
    writes = []

    def info(network, kind, **fields):
        assert network == "testnet"
        if kind == "metaAndAssetCtxs":
            return [{"universe": [{"name": "BTC", "szDecimals": 5}]}, [{"markPx": "80000"}]]
        if kind == "clearinghouseState":
            positions = [{"position": {"coin": "BTC", "szi": writes[0]["orders"][0]["s"]}}] if writes else []
            return {"assetPositions": positions, "marginSummary": {"accountValue": "10000"}, "withdrawable": "10000"}
        if kind == "activeAssetData":
            return {"leverage": {"type": "isolated", "value": 5}}
        if kind == "frontendOpenOrders":
            return []
        assert kind == "orderStatus"
        wire = writes[0]["orders"][1]
        assert fields["oid"] == wire["c"]
        return {"status": "order", "order": {"status": "open", "order": {
            "coin": "BTC", "cloid": wire["c"], "reduceOnly": True, "isTrigger": True,
        }}}

    def trade(action):
        writes.append(action)
        return {"type": "order", "data": {"statuses": [
            {"filled": {"oid": 9, "totalSz": action["orders"][0]["s"], "avgPx": "80000"}},
            "waitingForTrigger",
        ]}}

    def model(frame, *_args, **_kwargs):
        assert frame.iloc[-1].open_time == bar - pd.Timedelta(hours=1)
        return pd.DataFrame([{"entry_signal": True, "short_entry_signal": False,
                              "exit_signal": False, "short_exit_signal": False, "atr14": 100}])

    monkeypatch.setattr(venue, "_info", info)
    monkeypatch.setattr(venue, "trade", trade)
    monkeypatch.setattr(venue, "candles", lambda *_: [
        {"time": int(stamp.timestamp()), "open": 80000, "high": 81000, "low": 79000, "close": 80000, "volume": 1}
        for stamp in pd.date_range(end=bar, periods=400, freq="1h")
    ])
    monkeypatch.setattr(models, "evaluate_nonblocking", model)
    monkeypatch.setattr(auto, "_loop", lambda: None)

    preview = auto.prepare({"address": venue.owner, "symbol": "BTCUSDT", "interval": "1h",
                            "amount": "25", "model_id": "trend-v2"})
    auto.start(preview["confirmation"])
    auto._tick(auto.plan)
    assert len(writes) == 1 and writes[0]["grouping"] == "normalTpsl"
    assert writes[0]["orders"][0]["b"] is True
    assert writes[0]["orders"][1]["r"] is True and writes[0]["orders"][1]["t"]["trigger"]["tpsl"] == "sl"
    assert auto.plan["message"] == "已核对测试网仓位和交易所保护止损"
    auto._tick(auto.plan)
    assert len(writes) == 1
    auto.disable_agent()
    assert auto.plan["status"] == "paused" and venue.agent is None


def test_hyperliquid_perpetuals_are_read_only_and_keep_networks_separate(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from fastapi.testclient import TestClient

    from btcquant.config import load_config
    from btcquant.web import create_app

    calls = []

    def read_info(url, *, json, timeout, allow_redirects):
        calls.append((url, json))
        assert url.endswith("/info") and timeout > 0 and not allow_redirects
        kind = json["type"]
        if kind == "metaAndAssetCtxs":
            payload = [{"universe": [{"name": "BTC"}, {"name": "TRX", "isDelisted": True}]},
                       [{"markPx": "100", "funding": "-0.0001", "openInterest": "20", "dayNtlVlm": "1000"}, {}]]
        elif kind == "candleSnapshot":
            req = json["req"]
            assert req["coin"] == "BTC" and req["interval"] == "4h"
            row = dict(t=req["startTime"] + 14_400_000, s="BTC", i="4h", o="100", h="110", l="99", c="105")
            payload = [dict(row, c="NaN"), row, dict(row, s="ETH"), row]
        elif kind == "clearinghouseState":
            payload = {"marginSummary": {"accountValue": "100", "totalMarginUsed": "12"},
                       "withdrawable": "88", "assetPositions": [{"position": {"coin": "BTC", "szi": "-0.2"}}]}
        else:
            assert kind == "frontendOpenOrders"
            payload = [{"coin": "BTC", "oid": 100, "side": "B", "reduceOnly": True},
                       {"coin": "@1", "oid": 101}, {"coin": "PURR/USDC", "oid": 102}]
        return SimpleNamespace(status_code=200, json=lambda: payload)

    monkeypatch.setattr("btcquant.broker.requests.post", read_info)
    origin = "http://127.0.0.1:8787"
    cfg = replace(load_config("config.toml"), root=tmp_path)
    client = TestClient(create_app(cfg, force_paper=True), base_url=origin, client=("127.0.0.1", 40000))
    base = "/api/live/dex/hyperliquid"
    market = client.get(base + "/market", params={"coin": "BTC", "network": "testnet", "interval": "4h"}).json()
    assert market["network"] == "testnet" and market["mark"] == "100"
    assert len(market["candles"]) == 1 and market["candles"][0]["close"] == 105
    assert calls[0][0] == calls[1][0] == "https://api.hyperliquid-testnet.xyz/info"
    assert client.get(base + "/market", params={"coin": "TRX"}).status_code == 422
    assert client.get(base + "/market", params={"coin": "BTC", "network": "https://example.com"}).status_code == 422
    assert [payload["type"] for _, payload in calls] == ["metaAndAssetCtxs", "candleSnapshot", "metaAndAssetCtxs"]

    wallet = {"network": "mainnet", "address": "0x" + "AB" * 20}
    assert client.post(base + "/wallet", json=wallet).status_code == 403
    assert client.post(base + "/wallet", json=wallet, headers={"Origin": "https://example.com"}).status_code == 403
    assert client.post(base + "/wallet", json={**wallet, "address": "secret"}, headers={"Origin": origin}).status_code == 422
    response = client.post(base + "/wallet", json=wallet, headers={"Origin": origin})
    assert response.headers["Cache-Control"] == "no-store"
    assert wallet["address"].lower() not in response.text
    assert response.json()["positions"][0]["szi"] == "-0.2"
    assert [row["oid"] for row in response.json()["orders"]] == [100]
    assert calls[-2:] == [
        ("https://api.hyperliquid.xyz/info", {"type": "clearinghouseState", "user": wallet["address"].lower()}),
        ("https://api.hyperliquid.xyz/info", {"type": "frontendOpenOrders", "user": wallet["address"].lower()}),
    ]






def test_live_binance_close_is_bound_to_the_requested_position(tmp_path, monkeypatch):
    from decimal import Decimal
    from btcquant.binance_futures import BinanceFuturesReadOnly
    from btcquant.broker import LiveExecution

    futures = BinanceFuturesReadOnly(tmp_path)
    futures.save("fixture", "secret")
    sent = []
    def fake(method, path, params=None):
        if path.endswith("/dual"):
            return {"dualSidePosition":True}
        if path.endswith("/positionRisk"):
            return [{"symbol":"BTCUSDT","positionSide":"SHORT","positionAmt":"-0.002"}]
        sent.append(params)
        return {"orderId":42,"symbol":"BTCUSDT","clientOrderId":params["newClientOrderId"],
                "status":"FILLED","executedQty":params["quantity"],"cumQuote":"100"}

    monkeypatch.setattr(futures, "signed", fake)
    service = LiveExecution(tmp_path, {("binance", "futures"):futures})
    preview = service.preview({"exchange":"binance","market":"futures","symbol":"BTCUSDT",
                               "position":"SHORT","action":"CLOSE","quantity":Decimal("0.001"),"type":"MARKET"})
    assert sent == []
    assert service.submit(preview["confirmation"])["status"] == "FILLED"
    assert sent[0]["side"] == "BUY"
    assert sent[0]["positionSide"] == "SHORT"
    assert "reduceOnly" not in sent[0]



def test_live_grid_reconciles_before_replenishing_and_stops_own_orders(tmp_path, monkeypatch):
    from decimal import Decimal
    from btcquant.grid_paper import LiveStrategies
    from btcquant.broker import LiveExecution
    from btcquant.binance_spot import BinanceSpotReadOnly

    adapter = BinanceSpotReadOnly(tmp_path)
    adapter.save("mock-grid", "mock-secret")
    execution = LiveExecution(tmp_path, {("binance","spot"):adapter})
    runner = LiveStrategies(tmp_path, execution, None)
    monkeypatch.setattr(runner, "start_worker", lambda: None)
    monkeypatch.setattr(runner, "_price", lambda *args: Decimal(105))
    monkeypatch.setattr(runner, "_balance", lambda *args: Decimal(10000))
    monkeypatch.setattr(runner, "_rules", lambda *args: {"tick":"0.01","step":"0.001","min_qty":"0.001","min_quote":"5"})
    orders = {}
    called = []
    def signed(method, path, args=None):
        called.append((method, path, args))
        if path.endswith("/myTrades"):
            row = orders[args["orderId"]]
            return [{"orderId":row["orderId"],"qty":row["executedQty"],"quoteQty":row["cummulativeQuoteQty"],
                     "commission":"0.001","commissionAsset":"BTC"}]
        if method == "POST":
            number = len(orders)+1
            orders[str(number)] = {"symbol":args["symbol"],"orderId":number,"status":"NEW",
                                   "clientOrderId":args["newClientOrderId"],"executedQty":"0",
                                   "origQty":args["quantity"],"cummulativeQuoteQty":"0","price":args["price"]}
            return orders[str(number)].copy()
        row = next(v for v in orders.values() if v["clientOrderId"]==args.get("origClientOrderId") or str(v["orderId"])==args.get("orderId"))
        if method == "DELETE":
            row["status"] = "CANCELED"
        return row.copy()
    monkeypatch.setattr(adapter, "signed", signed)
    preview = runner.preview({"kind":"grid","exchange":"binance","market":"spot","symbol":"BTCUSDT",
                              "amount":"100","lower":"100","upper":"120","levels":2})
    assert not called
    public = runner.start(preview["confirmation"])
    plan = runner.plans[public["id"]]
    runner._tick(plan)
    assert len(called) == 1 and called[0][2]["side"]=="BUY"
    row = orders["1"]
    row.update(status="FILLED", executedQty=row["origQty"],
               cummulativeQuoteQty=str(Decimal(row["origQty"])*Decimal(row["price"])))
    runner._tick(plan)
    assert len([x for x in called if x[0]=="POST"]) == 1
    runner._tick(plan)
    sell = [x[2] for x in called if x[0]=="POST"][-1]
    assert sell["side"] == "SELL" and sell["price"] == "110"
    assert Decimal(sell["quantity"]) == Decimal(row["origQty"])-Decimal("0.001")
    restarted = LiveStrategies(tmp_path, execution, None)
    assert restarted.plans[plan["id"]]["status"] == "paused"
    monkeypatch.setattr(runner, "start_worker", lambda: None)
    runner.control(plan["id"], "stop")
    runner._tick(plan)
    runner._tick(plan)
    assert plan["status"] == "stopped"
    cancellations = [x[2]["origClientOrderId"] for x in called if x[0]=="DELETE"]
    assert cancellations == [orders["2"]["clientOrderId"]]


def test_live_auto_preserves_user_quote_and_notional(tmp_path, monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace
    import pandas as pd
    from btcquant.binance_spot import BinanceSpotReadOnly
    from btcquant.binance_futures import BinanceFuturesReadOnly
    from btcquant.broker import LiveExecution
    from btcquant.grid_paper import LiveStrategies
    from btcquant.models import BUILTIN, BUILTIN_SPOT

    spot = BinanceSpotReadOnly(tmp_path)
    futures = BinanceFuturesReadOnly(tmp_path)
    spot.save("mock-spot", "mock-secret")
    futures.save("mock-futures", "mock-secret")
    execution = LiveExecution(tmp_path, {("binance","spot"):spot,("binance","futures"):futures})
    signals = pd.DataFrame([{"open_time":pd.Timestamp("2024-01-01T01:00Z"),"entry_signal":True,
                            "short_entry_signal":False,"atr14":0.5}])
    models = SimpleNamespace(get=lambda model_id: BUILTIN_SPOT if model_id==BUILTIN_SPOT.id else BUILTIN,
                             evaluate_nonblocking=lambda *args, **kwargs: signals)
    runner = LiveStrategies(tmp_path, execution, models)
    monkeypatch.setattr(runner, "start_worker", lambda: None)
    monkeypatch.setattr(runner, "_price", lambda *args: Decimal("100"))
    monkeypatch.setattr(runner, "_rules", lambda *args: {"tick":"0.01","step":"0.001","min_qty":"0.001","min_quote":"5"})
    monkeypatch.setattr(runner, "_balance", lambda *args: Decimal("10000"))
    monkeypatch.setattr(runner, "_public", lambda *args: None)
    monkeypatch.setattr("btcquant.grid_paper._rows_to_frame", lambda *args: signals.copy())
    monkeypatch.setattr("btcquant.grid_paper._clean_klines", lambda frame, interval: frame)
    monkeypatch.setattr("btcquant.grid_paper.data_guard", lambda *args: None)
    calls = []
    def signed(method, path, args=None):
        calls.append((method,path,args))
        if path.endswith("/symbolConfig"):
            return [{"symbol":"BTCUSDT","marginType":"ISOLATED","leverage":5}]
        if path.endswith("/dual"):
            return {"dualSidePosition":True}
        if path.endswith("/account"):
            return {"availableBalance":"10000","totalWalletBalance":"10000"}
        if path.endswith("/positionRisk") or path.endswith("/openOrders") or path.endswith("/openAlgoOrders"):
            return []
        if path.endswith("/order"):
            return {"symbol":args["symbol"],"orderId":10 if path.startswith("/api/") else 20,
                    "clientOrderId":args["newClientOrderId"],"status":"NEW"}
        raise AssertionError(path)
    monkeypatch.setattr(spot, "signed", signed)
    monkeypatch.setattr(futures, "signed", signed)
    for market, amount, model in (("spot","73.25",BUILTIN_SPOT),("futures","241.27",BUILTIN)):
        preview = runner.preview({"kind":"auto","market":market,"exchange":"binance","symbol":"BTCUSDT",
                                  "amount":amount,"interval":"1h","model_id":model.id})
        assert preview["plan"]["amount"] == amount
        active = runner.start(preview["confirmation"])
        plan = runner.plans[active["id"]]
        runner._tick(plan)
        assert plan["pending"]
        runner._tick(plan)
    trades = [args for method,path,args in calls if method=="POST"]
    assert trades[0]["quoteOrderQty"] == "73.25"
    assert trades[1]["quantity"] == "2.412"
    assert trades[1]["positionSide"] == "LONG"



def test_coinw_futures_close_uses_position_endpoint_and_exact_id(tmp_path, monkeypatch):
    from btcquant.coinw import CoinWReadOnly
    from btcquant.broker import LiveExecution
    from decimal import Decimal

    adapter = CoinWReadOnly(tmp_path)
    adapter.save("mock-coinw", "mock-secret")
    position_id = "2435521222632023294"
    monkeypatch.setattr(adapter, "positions", lambda instruments: [{"id":position_id,"side":"LONG","qty":"2"}])
    requests = []
    def send(method, path, params):
        requests.append((method,path,params))
        return {"value":"33308751556999718"}
    monkeypatch.setattr(adapter, "_request", send)
    execution = LiveExecution(tmp_path, {("coinw","futures"):adapter})
    preview = execution.preview({"exchange":"coinw","market":"futures","symbol":"BTCUSDT",
        "type":"MARKET","action":"CLOSE","position":"LONG","position_id":position_id,"quantity":Decimal("1")})
    assert execution.submit(preview["confirmation"])["status"] == "ACCEPTED"
    assert requests == [("DELETE","/v1/perpum/positions",{"id":position_id,"closeNum":"1","positionType":"execute"})]


def test_live_futures_protection_posts_once_and_queries_same_id(tmp_path, monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace
    from btcquant.grid_paper import LiveStrategies

    runner = LiveStrategies(tmp_path, None, None)
    monkeypatch.setattr(runner, "_position", lambda *args: [{"positionSide":"LONG","positionAmt":"0.01"}])
    monkeypatch.setattr(runner, "_price", lambda *args: Decimal("100"))
    client_id = "sl" + "f" * 32
    plan = {"symbol":"BTCUSDT","position":{"side":"LONG","qty":"0.01","stop":"98",
                                         "protection":client_id,"protection_state":"ready"}}
    calls = []
    def signed(method, path, params):
        if method == "POST":
            assert plan["position"]["protection_state"] == "pending"
        calls.append((method,path,params))
        return {"clientAlgoId":client_id,"algoStatus":"NEW"}
    connector = SimpleNamespace(signed=signed)
    assert runner._protect_futures(plan, connector)
    assert runner._protect_futures(plan, connector)
    assert [call[0] for call in calls] == ["POST","GET"]
    assert calls[0][2]["type"] == "STOP_MARKET"
    assert calls[0][2]["positionSide"] == "LONG"
    assert calls[0][2]["quantity"] == "0.01"
    assert calls[1][2]["clientAlgoId"] == client_id



def test_coinw_spot_grid_queries_fills_before_sell(tmp_path, monkeypatch):
    from decimal import Decimal
    from btcquant.broker import LiveExecution
    from btcquant.coinw import CoinWSpotReadOnly
    from btcquant.grid_paper import LiveStrategies

    adapter = CoinWSpotReadOnly(tmp_path)
    adapter.save("mock-coinw", "mock-secret")
    execution = LiveExecution(tmp_path, {("coinw","spot"):adapter})
    runner = LiveStrategies(tmp_path, execution, None)
    monkeypatch.setattr(runner, "start_worker", lambda: None)
    monkeypatch.setattr(runner, "_price", lambda *args: Decimal("105"))
    monkeypatch.setattr(runner, "_balance", lambda *args: Decimal("500"))
    monkeypatch.setattr(runner, "_rules", lambda *args: {"tick":"0.01","step":"0.001","min_qty":"0.001","min_quote":"5"})
    placed = []
    def signed(command, params=None):
        if command == "doTrade":
            placed.append(params)
            return {"orderNumber":str(123+len(placed))}
        if command == "returnOrderStatus":
            return {"status":3,"currencyPair":"BTC_USDT"}
        assert command == "returnOrderTrades" and params["orderNumber"] == "124"
        return {"tradeID":124, "success_total":"0.990", "success_amount":"99.00","fee":"0.099"}
    monkeypatch.setattr(adapter, "signed", signed)
    preview = runner.preview({"kind":"grid","exchange":"coinw","market":"spot","symbol":"BTCUSDT",
                              "amount":"100","lower":"100","upper":"120","levels":2})
    public = runner.start(preview["confirmation"])
    plan = runner.plans[public["id"]]
    runner._tick(plan)
    assert len(placed) == 1 and placed[0]["type"] == "0"
    runner._tick(plan)
    assert len(placed) == 1
    runner._tick(plan)
    assert placed[1]["type"] == "1" and placed[1]["amount"] == "0.989"
    assert placed[1]["out_trade_no"] != placed[0]["out_trade_no"]
