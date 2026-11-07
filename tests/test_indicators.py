import numpy as np
import pandas as pd

from btcquant import indicators as ta


def test_ema_and_sma_are_causal_and_ordered():
    s = pd.Series(np.arange(1, 21, dtype=float))
    fast = ta.ema(s, 5)
    slow = ta.ema(s, 10)
    assert np.isnan(fast.iloc[3])
    assert not np.isnan(fast.iloc[4])
    assert fast.iloc[-1] > slow.iloc[-1]
    assert ta.sma(s, 5).iloc[-1] == 18.0


def test_rsi_extremes():
    up = pd.Series(np.arange(1, 50, dtype=float))
    down = pd.Series(np.arange(50, 1, -1, dtype=float))
    assert ta.rsi(up, 14).iloc[-1] == 100.0
    assert ta.rsi(down, 14).iloc[-1] == 0.0


def test_atr_positive_and_bollinger_order():
    rng = np.random.default_rng(7)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 200)))
    high = close + rng.uniform(0.5, 2.0, len(close))
    low = close - rng.uniform(0.5, 2.0, len(close))
    a = ta.atr(high, low, close, 14)
    assert a.dropna().gt(0).all()
    lower, mid, upper = ta.bollinger(close, 20, 2.0)
    valid = lower.notna() & mid.notna() & upper.notna()
    assert (lower[valid] < mid[valid]).all()
    assert (mid[valid] < upper[valid]).all()


def test_donchian_prior_excludes_current_bar():
    high = pd.Series([1.0, 2.0, 100.0, 4.0])
    low = pd.Series([0.0, 1.0, 0.5, 3.0])
    upper, lower = ta.donchian_prior(high, low, 2)
    # At index 2 the channel must use bars 0..1, not the 100 spike in bar 2.
    assert upper.iloc[2] == 2.0
    assert lower.iloc[2] == 0.0
