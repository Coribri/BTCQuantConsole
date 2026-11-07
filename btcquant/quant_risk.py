"""Execution guards and monotonic profit protection shared by paper models."""
from __future__ import annotations
import math
import pandas as pd
from .data import INTERVAL_MS


def data_guard(frame, interval, now=None):
    if frame.empty:
        return "没有已收盘数据"
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    opens = pd.to_datetime(frame.open_time,utc=True)
    step = pd.Timedelta(milliseconds=INTERVAL_MS[interval])
    end = opens.iloc[-1]+step
    if end > now:
        return "最新 K 线尚未收盘"
    if now-end > step*1.5:
        return "行情过期，暂停新开仓"
    if (opens.tail(220).diff().dropna()!=step).any():
        return "近期 K 线有缺口，暂停新开仓"
    vals = frame.iloc[-1][["open","high","low","close"]]
    if not all(math.isfinite(float(v)) and float(v)>0 for v in vals):
        return "行情数值无效"
    return None


def update_trailing(pos, price, fee, slip):
    """Update a trailing *observed-price* trigger, never loosen it.

    Long/short break-even includes both fees and exit slippage. Entry slippage
    is already represented by the position's actual entry_price.
    """
    if not pos.get("trailing_enabled"):
        return
    entry = float(pos["entry_price"])
    long = pos.get("side","LONG")=="LONG"
    best = float(pos.get("trailing_best") or entry)
    best = max(best,price) if long else min(best,price)
    pos["trailing_best"] = best
    activation = float(pos.get("trail_activation_pct",.01))
    distance = float(pos.get("trail_distance_pct",.005))
    favorable = (best/entry-1) if long else (1-best/entry)
    if favorable < activation and not pos.get("trailing_active"):
        return
    breakeven = entry*(1+fee)/(1-fee)/(1-slip) if long else entry*(1-fee)/(1+fee)/(1+slip)
    # If activation is smaller than costs, do not activate a stop that would
    # sit beyond the current price and close a still losing position at once.
    if (long and best<=breakeven) or (not long and best>=breakeven):
        return
    distance_price=entry*distance
    candidate = max(best-distance_price,breakeven) if long else min(best+distance_price,breakeven)
    previous = pos.get("trailing_stop")
    if previous is not None:
        candidate = max(float(previous),candidate) if long else min(float(previous),candidate)
    pos["trailing_stop"] = candidate
    pos["trailing_active"] = True
