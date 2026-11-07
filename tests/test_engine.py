from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from btcquant.config import BacktestConfig
from btcquant.engine import entry_budget, run_backtest


def make_signals(
    opens,
    highs,
    lows,
    closes,
    entries,
    exits,
    *,
    atr=1.0,
    stop_atr=10.0,
    trail_atr=0.0,
    max_hold=0,
) -> pd.DataFrame:
    n = len(opens)
    assert all(len(x) == n for x in (highs, lows, closes, entries, exits))
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "atr14": atr if np.isscalar(atr) else atr,
            "stop_atr": stop_atr,
            "trail_atr": trail_atr,
            "max_hold": max_hold,
            "entry_signal": entries,
            "exit_signal": exits,
        }
    )


def cfg(**kwargs) -> BacktestConfig:
    base = BacktestConfig(
        initial_cash=1000.0,
        fee_rate=0.0,
        slippage_bps=0.0,
        max_fraction=0.95,
        sizing="fixed_fraction",
    )
    return replace(base, **kwargs)


def test_signal_is_filled_on_next_open_not_current_close():
    sig = make_signals(
        opens=[100, 100, 102, 104, 105],
        highs=[101, 101, 103, 105, 106],
        lows=[99, 99, 101, 103, 104],
        closes=[100, 100.5, 102.5, 104.5, 105.5],
        entries=[False, True, True, False, False],
        exits=[False, False, True, False, False],
    )
    result = run_backtest(sig, sig, cfg(), strategy="test")
    trade = result.trades.iloc[0]
    assert trade["entry_time"] == sig.loc[2, "open_time"]
    assert trade["exit_time"] == sig.loc[3, "open_time"]
    assert trade["entry_price"] == pytest.approx(102.0)
    assert trade["exit_price"] == pytest.approx(104.0)
    assert trade["net_pnl"] == pytest.approx((950.0 / 102.0) * 2.0)
    assert result.metrics["trades"] == 1


def test_intrabar_stop_uses_stop_price():
    sig = make_signals(
        opens=[100, 100, 100, 100],
        highs=[101, 101, 101, 101],
        lows=[99, 99, 90, 99],
        closes=[100, 100, 95, 100],
        entries=[False, True, False, False],
        exits=[False, False, False, False],
        atr=3.0,
        stop_atr=2.0,
    )
    result = run_backtest(sig, sig, cfg(), strategy="test")
    trade = result.trades.iloc[0]
    assert trade["entry_time"] == sig.loc[2, "open_time"]
    assert trade["exit_time"] == sig.loc[2, "open_time"]
    assert trade["entry_price"] == pytest.approx(100.0)
    assert trade["exit_price"] == pytest.approx(94.0)
    assert trade["exit_reason"] == "stop"
    assert result.metrics["final_equity"] == pytest.approx(943.0)


def test_gap_through_stop_fills_at_open():
    sig = make_signals(
        opens=[100, 100, 100, 90, 91],
        highs=[101, 101, 101, 92, 93],
        lows=[99, 99, 99, 89, 90],
        closes=[100, 100, 100, 91, 92],
        entries=[False, True, False, False, False],
        exits=[False, False, False, False, False],
        atr=3.0,
        stop_atr=2.0,
    )
    result = run_backtest(sig, sig, cfg(), strategy="test")
    trade = result.trades.iloc[0]
    assert trade["exit_time"] == sig.loc[3, "open_time"]
    assert trade["exit_price"] == pytest.approx(90.0)
    assert trade["exit_reason"] == "gap_stop"


def test_time_stop_exits_on_next_open():
    sig = make_signals(
        opens=[100, 100, 100, 101, 102, 103],
        highs=[101] * 6,
        lows=[99] * 6,
        closes=[100, 100, 100.5, 101.5, 102.5, 103.5],
        entries=[False, True, False, False, False, False],
        exits=[False, False, False, False, False, False],
        max_hold=2,
    )
    result = run_backtest(sig, sig, cfg(), strategy="test")
    trade = result.trades.iloc[0]
    assert trade["exit_time"] == sig.loc[4, "open_time"]
    assert trade["exit_reason"] == "time"


def test_fees_and_slippage_leave_cash_non_negative():
    sig = make_signals(
        opens=[100, 100, 100, 101, 102],
        highs=[101] * 5,
        lows=[99] * 5,
        closes=[100, 100, 100.5, 101.5, 102.5],
        entries=[False, True, False, False, False],
        exits=[False, False, False, True, False],
    )
    result = run_backtest(sig, sig, cfg(fee_rate=0.001, slippage_bps=2.0), strategy="test")
    assert (result.equity["cash"] >= -1e-9).all()
    trade = result.trades.iloc[0]
    assert trade["fees"] > 0
    assert trade["entry_price"] > 100.0
    assert trade["exit_price"] < 102.0


def test_risk_budget_formula():
    # 2% of 10k risk / 5% stop distance = 4k notional, below the 95% cap.
    assert entry_budget(
        10_000, 100, 5, max_fraction=0.95, sizing="risk", risk_per_trade=0.02
    ) == pytest.approx(4_000.0)
    assert entry_budget(
        10_000, 100, 5, max_fraction=0.95, sizing="fixed_fraction", risk_per_trade=0.02
    ) == pytest.approx(9_500.0)
    # A very wide stop cannot make the position larger than the cap.
    assert entry_budget(
        10_000, 100, 1, max_fraction=0.95, sizing="risk", risk_per_trade=0.02
    ) == pytest.approx(9_500.0)


def test_benchmark_tracks_buy_and_hold():
    sig = make_signals(
        opens=[100, 101, 102, 103, 104],
        highs=[101, 102, 103, 104, 105],
        lows=[99, 100, 101, 102, 103],
        closes=[100, 101, 102, 103, 104],
        entries=[False] * 5,
        exits=[False] * 5,
    )
    result = run_backtest(sig, sig, cfg(), strategy="test")
    assert result.metrics["trades"] == 0
    assert result.benchmark_metrics["final_equity"] > 1000.0
    assert result.benchmark_metrics["max_drawdown"] == 0.0
    assert result.equity["equity"].iloc[-1] == pytest.approx(1000.0)
