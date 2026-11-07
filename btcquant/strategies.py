"""Causal signal generation for the three research strategies.

The output contract is deliberately simple:
  entry_signal : bool at bar close -> may enter at next bar open
  exit_signal  : bool at bar close -> may exit at next bar open
  stop_atr     : stop distance in ATR units
  max_hold     : force exit after N bars (0 = disabled)

Execution, sizing, fees and stops live in ``engine.py`` so that strategies
cannot accidentally peek at future prices.
"""

from __future__ import annotations

from typing import Any
import json

import pandas as pd
import numpy as np

from . import indicators as ta
from .config import DEFAULT_PARAMS


class StrategyError(ValueError):
    pass


def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close", "volume", "open_time"}
    missing = required.difference(df.columns)
    if missing:
        raise StrategyError(f"missing columns: {sorted(missing)}")
    out = df.copy()
    out["atr14"] = ta.atr(out["high"], out["low"], out["close"], 14)
    return out


def _as_bool(series: pd.Series) -> pd.Series:
    return series.fillna(False).astype(bool)


def generate_signals(
    df: pd.DataFrame,
    strategy: str = "trend",
    params: dict[str, Any] | None = None,
) -> pd.DataFrame:
    if strategy not in DEFAULT_PARAMS:
        raise StrategyError(f"unknown strategy: {strategy}")
    p = dict(DEFAULT_PARAMS[strategy])
    if params:
        p.update(params)
    out = prepare_features(df)
    close, high, low = out["close"], out["high"], out["low"]

    if strategy in {"trend", "trend_v2"}:
        fast, slow, regime = int(p["fast"]), int(p["slow"]), int(p["regime"])
        if not fast < slow:
            raise StrategyError("trend requires fast < slow")
        out["ema_fast"] = ta.ema(close, fast)
        out["ema_slow"] = ta.ema(close, slow)
        out["ema_regime"] = ta.ema(close, regime)
        out["adx14"] = ta.adx(high, low, close, 14)
        valid = out[["ema_fast", "ema_slow", "ema_regime", "adx14", "atr14"]].notna().all(axis=1)
        entry = (
            (out["ema_fast"] > out["ema_slow"])
            & (close > out["ema_slow"])
            & (close > out["ema_regime"])
            & (out["adx14"] >= float(p["adx_min"]))
        )
        exit_ = (out["ema_fast"] < out["ema_slow"]) | (close < out["ema_regime"])
        out["entry_signal"] = _as_bool(entry & valid)
        out["exit_signal"] = _as_bool(exit_ & valid)
        out["short_entry_signal"] = _as_bool((out.ema_fast < out.ema_slow) & (close < out.ema_slow) & (close < out.ema_regime) & (out.adx14 >= float(p["adx_min"])) & valid)
        out["short_exit_signal"] = _as_bool(((out.ema_fast > out.ema_slow) | (close > out.ema_regime)) & valid)
        if strategy == "trend_v2":
            delta = pd.to_datetime(out.open_time, utc=True).diff().median()
            seconds = int(delta.total_seconds()) if pd.notna(delta) else 0
            factor = 4 if seconds <= 3600 else 6
            out["volume_ratio"] = out.volume / out.volume.rolling(24).mean().replace(0, np.nan)
            out["atr_pct"] = out.atr14 / close
            out["extension_atr"] = (close-out.ema_fast).abs() / out.atr14.replace(0, np.nan)
            out["trend_long_ok"] = _as_bool(entry & entry.shift(1).fillna(False) & (out.ema_fast > out.ema_fast.shift(3)))
            bear = out.short_entry_signal.copy()
            out["trend_short_ok"] = _as_bool(bear & bear.shift(1).fillna(False) & (out.ema_fast < out.ema_fast.shift(3)))
            out["volume_ok"] = _as_bool(out.volume_ratio >= float(p["min_volume_ratio"]))
            out["volatility_ok"] = _as_bool((out.atr_pct <= float(p["max_atr_pct"])) & (out.atr_pct >= float(p["roundtrip_cost"])*1.5))
            out["extension_ok"] = _as_bool(out.extension_atr <= float(p["max_extension_atr"]))
            out["higher_long_ok"], out["higher_short_ok"] = False, False
            if seconds > 0:
                # Aggregate only complete UTC-aligned higher candles. A partial
                # bucket never influences an earlier lower-timeframe decision.
                timestamps = pd.to_datetime(out.open_time, utc=True)
                values = pd.DataFrame({"close":close.to_numpy(),"time":timestamps.to_numpy()})
                values.index = pd.DatetimeIndex(timestamps)
                grouped = values.resample(pd.Timedelta(seconds=seconds*factor), origin="epoch")
                higher = grouped.agg(close=("close","last"),count=("close","count"),first=("time","first"),last=("time","last"))
                higher = higher[(higher["count"]==factor) & ((higher["last"]-higher["first"]).dt.total_seconds()==seconds*(factor-1))]
                if not higher.empty:
                    hfast = ta.ema(higher.close,20)
                    hslow = ta.ema(higher.close,50)
                    higher["long_ok"] = _as_bool((hfast>hslow)&(higher.close>hslow))
                    higher["short_ok"] = _as_bool((hfast<hslow)&(higher.close<hslow))
                    higher["available"] = higher.index + pd.Timedelta(seconds=seconds*factor)
                    lower = pd.DataFrame({"available":timestamps+pd.Timedelta(seconds=seconds),"row":np.arange(len(out))})
                    aligned = pd.merge_asof(lower.sort_values("available"),higher[["available","long_ok","short_ok"]].sort_values("available"),on="available",direction="backward").sort_values("row")
                    out["higher_long_ok"] = aligned.long_ok.fillna(False).astype(bool).to_numpy()
                    out["higher_short_ok"] = aligned.short_ok.fillna(False).astype(bool).to_numpy()
            filters = out.volume_ok & out.volatility_ok & out.extension_ok & valid
            out["entry_signal"] = _as_bool(out.trend_long_ok & out.higher_long_ok & filters)
            out["short_entry_signal"] = _as_bool(out.trend_short_ok & out.higher_short_ok & filters)
            out["higher_factor"] = factor

    elif strategy == "donchian":
        entry_n, exit_n, regime = int(p["entry"]), int(p["exit"]), int(p["regime"])
        if entry_n < 2 or exit_n < 2:
            raise StrategyError("donchian windows must be >= 2")
        out["ema_regime"] = ta.ema(close, regime)
        out["donchian_high"], out["donchian_low"] = ta.donchian_prior(high, low, entry_n)
        _, out["donchian_exit_low"] = ta.donchian_prior(high, low, exit_n)
        valid = out[["ema_regime", "donchian_high", "donchian_exit_low", "atr14"]].notna().all(axis=1)
        entry = (close > out["donchian_high"]) & (close > out["ema_regime"])
        exit_ = close < out["donchian_exit_low"]
        out["entry_signal"] = _as_bool(entry & valid)
        out["exit_signal"] = _as_bool(exit_ & valid)

    elif strategy == "meanrev":
        bb_n, regime = int(p["bb"]), int(p["regime"])
        out["ema_regime"] = ta.ema(close, regime)
        out["bb_lower"], out["bb_mid"], out["bb_upper"] = ta.bollinger(
            close, bb_n, float(p["bb_std"])
        )
        out["rsi_fast"] = ta.rsi(close, int(p["rsi_n"]))
        valid = out[["ema_regime", "bb_lower", "bb_mid", "rsi_fast", "atr14"]].notna().all(axis=1)
        # Only buy dips inside a longer-term uptrend.  This is the key filter
        # that separates "mean reversion" from "catching a falling knife".
        entry = (
            (close < out["bb_lower"])
            & (out["rsi_fast"] < float(p["rsi_entry"]))
            & (close > out["ema_regime"])
        )
        exit_ = (close > out["bb_mid"]) | (out["rsi_fast"] > float(p["rsi_exit"]))
        out["entry_signal"] = _as_bool(entry & valid)
        out["exit_signal"] = _as_bool(exit_ & valid)

    else:  # pragma: no cover - guarded by the check above
        raise StrategyError(strategy)

    out["stop_atr"] = float(p["atr_stop"])
    out["trail_atr"] = float(p["trail_atr"])
    out["max_hold"] = int(p["max_hold"])
    out["strategy"] = strategy
    return out


def strategy_snapshot(signals: pd.DataFrame, strategy: str) -> dict[str, Any]:
    """Human-readable values from the latest *closed* bar."""
    last = signals.iloc[-1]
    snap: dict[str, Any] = {
        "strategy": strategy,
        "bar_time": pd.Timestamp(last["open_time"]).isoformat(),
        "close": float(last["close"]),
        "entry_signal": bool(last["entry_signal"]),
        "exit_signal": bool(last["exit_signal"]),
        "atr14": float(last["atr14"]),
        "stop_atr": float(last["stop_atr"]),
        "trail_atr": float(last["trail_atr"]),
    }
    for key in ("ema_fast", "ema_slow", "ema_regime", "adx14", "donchian_high", "bb_lower", "bb_mid", "rsi_fast", "volume_ratio", "atr_pct", "extension_atr"):
        if key in signals.columns and pd.notna(last[key]):
            snap[key] = float(last[key])
    snap["short_entry_signal"] = bool(last.get("short_entry_signal",False))
    snap["short_exit_signal"] = bool(last.get("short_exit_signal",False))
    for key in ("model_id","model_name","model_version","model_reason","model_error"):
        if key in signals.columns:
            snap[key]=str(last[key])
    for key, column in (("optional_context","model_context_json"),("ai_report","ai_report_json")):
        if column in signals.columns:
            snap[key] = json.loads(last[column])
    if strategy == "trend_v2":
        snap["version"] = "Trend V2"
        snap["conditions"] = [
            {"label":label,"long":bool(last.get(l,False)),"short":bool(last.get(s,False))}
            for label,l,s in [("趋势连续确认","trend_long_ok","trend_short_ok"),("更高周期同向","higher_long_ok","higher_short_ok"),
                              ("成交量有效","volume_ok","volume_ok"),("波动覆盖成本","volatility_ok","volatility_ok"),("避免过度追价","extension_ok","extension_ok")]]
        snap["higher_factor"] = int(last.get("higher_factor",4))
    return snap
