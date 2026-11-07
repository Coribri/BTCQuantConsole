from dataclasses import replace

import pytest

from btcquant.broker import PaperBroker, TestnetBroker as BinanceTestnetBroker, build_broker
from btcquant.config import load_config
from btcquant.testnet import SymbolFilters, parse_order_fills, sign_query


def paper_cfg(tmp_path):
    base = load_config("config.toml")
    paper = replace(
        base.paper,
        initial_cash=10_000.0,
        fee_rate=0.001,
        slippage_bps=2.0,
    )
    testnet = replace(
        base.testnet,
        paper_state_file=tmp_path / "paper.json",
        state_file=tmp_path / "testnet.json",
    )
    return replace(base, paper=paper, testnet=testnet)


def test_paper_broker_round_trip_accounting(tmp_path, monkeypatch):
    cfg = paper_cfg(tmp_path)
    broker = PaperBroker(cfg)
    monkeypatch.setattr(PaperBroker, "price", lambda self: 100.0)

    order = broker.buy_quote(1_000.0, stop_price=90.0, reason="test")
    assert order["side"] == "BUY"
    assert order["price"] == pytest.approx(100.02)
    assert order["qty"] == pytest.approx(1000.0 / 100.02)
    acct = broker.account()
    assert acct["btc_total"] == pytest.approx(1000.0 / 100.02)
    assert acct["equity"] == pytest.approx(9998.8, abs=0.05)
    assert acct["stop_price"] == 90.0

    sell = broker.sell_all(reason="test")
    assert sell["side"] == "SELL"
    acct = broker.account()
    assert acct["btc_total"] == 0.0
    assert acct["realized_pnl"] < 0
    assert acct["equity"] == pytest.approx(9997.6, abs=0.1)
    assert acct["fees"] > 1.0

    broker.reset()
    acct = broker.account()
    assert acct["equity"] == pytest.approx(10_000.0)
    assert acct["btc_total"] == 0.0
    assert broker.recent_orders() == []


def test_symbol_filter_rounding_and_order_fill_parser():
    filters = SymbolFilters(step_size=0.00001, min_qty=0.00001, min_notional=5.0)
    assert filters.round_qty(0.123456789) == pytest.approx(0.12345)
    assert filters.fmt_qty(0.123456789) == "0.12345"
    qty, avg, fees = parse_order_fills(
        {
            "fills": [
                {"qty": "0.1", "price": "100", "commission": "0.01", "commissionAsset": "USDT"},
                {"qty": "0.2", "price": "110", "commission": "0.02", "commissionAsset": "USDT"},
            ]
        }
    )
    assert qty == pytest.approx(0.3)
    assert avg == pytest.approx((10 + 22) / 0.3)
    assert fees == pytest.approx(0.03)


def test_binance_hmac_matches_known_vector():
    assert sign_query("The quick brown fox jumps over the lazy dog", "key") == (
        "f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"
    )


def test_broker_factory_uses_testnet_only_when_keys_exist(tmp_path, monkeypatch):
    cfg = paper_cfg(tmp_path)
    monkeypatch.delenv(cfg.testnet.api_key_env, raising=False)
    monkeypatch.delenv(cfg.testnet.api_secret_env, raising=False)
    assert isinstance(build_broker(cfg), PaperBroker)

    monkeypatch.setenv(cfg.testnet.api_key_env, "key")
    monkeypatch.setenv(cfg.testnet.api_secret_env, "secret")
    broker = build_broker(cfg)
    assert isinstance(broker, BinanceTestnetBroker)
    assert broker.configured is True
