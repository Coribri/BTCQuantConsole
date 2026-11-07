import numpy as np
import pandas as pd
import pytest

from btcquant.strategies import StrategyError, generate_signals


def synthetic_ohlcv(n: int = 500, trend: float = 0.15) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    rng = np.random.default_rng(42)
    steps = np.full(n, trend / 100.0) + rng.normal(0.0, 0.012, n)
    close = 100 * np.exp(np.cumsum(steps))
    open_ = close * (1 + np.sin(np.arange(n)) * 0.001)
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    return pd.DataFrame(
        {
            "open_time": idx,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 100.0,
        }
    )


def test_trend_signals_are_complete_and_causal():
    df = synthetic_ohlcv()
    sig = generate_signals(df, "trend", {"fast": 20, "slow": 100, "regime": 200, "adx_min": 0})
    for col in ("entry_signal", "exit_signal", "atr14", "stop_atr", "trail_atr", "max_hold"):
        assert col in sig.columns
    assert sig["entry_signal"].dtype == bool
    assert sig["exit_signal"].dtype == bool
    # The synthetic series trends up, so a trend strategy must eventually be long.
    assert sig["entry_signal"].sum() > 0
    # The first regime-EMA value is unavailable; signals must be False there.
    assert not bool(sig.loc[:198, "entry_signal"].any())


def test_donchian_and_meanrev_produce_signals():
    df = synthetic_ohlcv(600)
    d = generate_signals(df, "donchian")
    m = generate_signals(df, "meanrev")
    assert d["entry_signal"].sum() > 0
    assert {"bb_lower", "bb_mid", "rsi_fast"}.issubset(m.columns)
    assert m["entry_signal"].dtype == bool


def test_unknown_strategy_and_bad_params_raise():
    df = synthetic_ohlcv(50)
    with pytest.raises(StrategyError):
        generate_signals(df, "no_such_strategy")
    with pytest.raises(StrategyError):
        generate_signals(df, "trend", {"fast": 100, "slow": 20})
