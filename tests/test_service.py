from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from btcquant.config import load_config
from btcquant.service import TradingService
from btcquant.testnet import TestnetError as BinanceTestnetError


def synthetic_frame(n=320):
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = 100 * np.exp(np.linspace(0, 0.25, n))
    return pd.DataFrame(
        {
            "open_time": idx,
            "open": close * 0.999,
            "high": close * 1.004,
            "low": close * 0.996,
            "close": close,
            "volume": 10.0,
        }
    )


def test_account_error_keeps_chart_and_signal(tmp_path):
    base = load_config("config.toml")
    testnet = replace(
        base.testnet,
        paper_state_file=tmp_path / "paper.json",
        state_file=tmp_path / "testnet.json",
    )
    cfg = replace(base, testnet=testnet)
    service = TradingService(cfg, force_paper=True)
    service.events_file = tmp_path / "events.jsonl"
    frame = synthetic_frame()

    service.broker.price = lambda: 100.0
    service.broker.klines = lambda interval, limit: frame

    def fail_account(price=None):
        raise BinanceTestnetError("bad testnet key")

    service.broker.account = fail_account
    status = service.poll()

    assert status["error"] == "bad testnet key"
    assert status["price"] == 100.0
    assert status["signal"]
    assert status["account"]["connected"] is False
    assert status["account"]["equity"] is None


def test_candles_include_ema_warmup(tmp_path):
    base = load_config("config.toml")
    testnet = replace(base.testnet, paper_state_file=tmp_path / "paper.json")
    cfg = replace(base, testnet=testnet)
    service = TradingService(cfg, force_paper=True)
    service.broker.klines = lambda interval, limit: synthetic_frame(320)
    out = service.candles("4h", limit=120)
    assert len(out["candles"]) == 120
    assert len(out["ema20"]) == 120
    assert len(out["ema100"]) == 120
    assert len(out["ema200"]) == 120
    assert len(out["ma20"]) == 120
    assert len(out["ma60"]) == 120
