from dataclasses import replace

import numpy as np
import pandas as pd

from btcquant.config import load_config
from btcquant.service import TradingService


def signal_frame(entry: bool, exit_: bool, n: int = 320) -> pd.DataFrame:
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC")-pd.Timedelta(hours=9), periods=n, freq="4h")
    close = 100.0 + np.arange(n) * 0.01
    frame = pd.DataFrame(
        {
            "open_time": idx,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 10.0,
            "atr14": 1.0,
            "stop_atr": 3.0,
            "trail_atr": 0.0,
            "max_hold": 0,
            "entry_signal": False,
            "exit_signal": False,
            "strategy": "trend",
        }
    )
    frame.loc[n - 1, "entry_signal"] = entry
    frame.loc[n - 1, "exit_signal"] = exit_
    return frame


def test_auto_entry_then_exit_updates_paper_account(tmp_path, monkeypatch):
    base = load_config("config.toml")
    testnet = replace(
        base.testnet,
        paper_state_file=tmp_path / "paper.json",
        state_file=tmp_path / "testnet.json",
    )
    cfg = replace(base, testnet=testnet)
    service = TradingService(cfg, force_paper=True)
    service.events_file = tmp_path / "events.jsonl"
    frames = [signal_frame(True, False)]

    monkeypatch.setattr(service.broker, "price", lambda: 100.0)
    monkeypatch.setattr(service.broker, "klines", lambda interval, limit: frames[0])
    monkeypatch.setattr(service.models,"evaluate_nonblocking",lambda *args,**kwargs:frames[0])
    service.auto_enabled = True

    status = service.poll()
    assert status["account"]["position_qty"] > 0
    assert status["account"]["stop_price"] is None
    assert any(o["side"] == "BUY" for o in status["orders"])

    # Next closed bar: exit signal.  The service should sell the paper position.
    frames[0] = signal_frame(False, True)
    frames[0]["open_time"] = frames[0]["open_time"] + pd.Timedelta(hours=4)
    status = service.poll()
    assert status["account"]["position_qty"] == 0
    assert any(o["side"] == "SELL" for o in status["orders"])
    assert status["auto_enabled"] is True
