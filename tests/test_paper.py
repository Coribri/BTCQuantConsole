from dataclasses import replace

import pandas as pd
import pytest

from btcquant.config import load_config
from btcquant.paper import PaperTrader
from btcquant.strategies import strategy_snapshot


def two_bar_signals(entry_first=True, exit_second=True) -> pd.DataFrame:
    times = pd.date_range("2024-01-01", periods=2, freq="4h", tz="UTC")
    return pd.DataFrame(
        {
            "open_time": times,
            "open": [100.0, 100.0],
            "high": [101.0, 111.0],
            "low": [99.0, 99.0],
            "close": [100.0, 110.0],
            "atr14": [1.0, 1.0],
            "stop_atr": [3.0, 3.0],
            "trail_atr": [0.0, 0.0],
            "max_hold": [0, 0],
            "entry_signal": [entry_first, False],
            "exit_signal": [False, exit_second],
            "strategy": ["trend", "trend"],
        }
    )


def test_paper_round_trip_and_state_files(tmp_path, monkeypatch):
    base = load_config("config.toml")
    paper = replace(
        base.paper,
        state_file=tmp_path / "state.json",
        trades_file=tmp_path / "trades.csv",
        equity_file=tmp_path / "equity.csv",
        log_file=tmp_path / "events.jsonl",
        initial_cash=10_000.0,
        fee_rate=0.001,
        slippage_bps=2.0,
        sizing="risk",
        risk_per_trade=0.02,
    )
    cfg = replace(base, paper=paper)
    signals = two_bar_signals()
    current = [signals.iloc[:1].reset_index(drop=True)]
    prices = [100.0, 110.0]

    def fake_latest(self):
        frame = current[0]
        return frame, strategy_snapshot(frame, "trend")

    monkeypatch.setattr(PaperTrader, "_latest_signal", fake_latest)
    monkeypatch.setattr(
        "btcquant.paper.fetch_ticker",
        lambda *args, **kwargs: {"symbol": "BTCUSDT", "price": prices.pop(0)},
    )

    trader = PaperTrader(cfg)
    first = trader.step()
    assert first["in_position"] is True
    assert first["last_action"] == "BUY"
    assert 0 < first["qty"] < 100
    assert first["stop_price"] < first["entry_price"]

    current[0] = signals
    second = trader.step()
    assert second["in_position"] is False
    assert second["last_action"] == "SELL"
    assert second["trades"] == 1
    assert second["realized_pnl"] > 0
    assert paper.trades_file.exists()
    assert paper.equity_file.exists()
    assert paper.log_file.exists()

    trades = pd.read_csv(paper.trades_file)
    assert len(trades) == 1
    assert trades.iloc[0]["exit_reason"] == "signal"
    assert trades.iloc[0]["net_pnl"] > 0
    assert trades.iloc[0]["signal_close_at_entry"] == 100.0


def test_paper_strategy_mismatch_is_refused(tmp_path):
    base = load_config("config.toml")
    paper = replace(base.paper, state_file=tmp_path / "state.json")
    cfg = replace(base, paper=paper, strategy_name="donchian")
    trader = PaperTrader(cfg)
    trader.save()
    with pytest.raises(RuntimeError):
        PaperTrader(replace(cfg, strategy_name="trend"))
