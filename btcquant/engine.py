"""Event-driven, long-only spot backtester.

Execution model
---------------
1. Signals are evaluated on a closed bar *t*.
2. Orders are filled at the open of bar *t+1* with slippage.
3. Protective stops are checked intrabar using the bar's low.
4. Fees are charged on both entries and exits.

This is still a simplification of real execution, but it removes the two most
common backtest lies: same-bar signal fills and zero-cost trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .config import BacktestConfig


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    qty: float
    notional: float
    gross_pnl: float
    fees: float
    net_pnl: float
    return_pct: float
    bars_held: int
    exit_reason: str


@dataclass
class BacktestResult:
    strategy: str
    params: dict[str, Any]
    equity: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict[str, Any]
    benchmark_equity: pd.DataFrame
    benchmark_metrics: dict[str, Any]
    config: BacktestConfig
    signals: pd.DataFrame = field(repr=False)


TRADE_COLUMNS = [
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "qty",
    "notional",
    "gross_pnl",
    "fees",
    "net_pnl",
    "return_pct",
    "bars_held",
    "exit_reason",
]


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_COLUMNS)


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = equity / peak - 1.0
    return float(dd.min())


def compute_metrics(
    equity: pd.DataFrame,
    trades: pd.DataFrame,
    initial_cash: float,
    *,
    periods_per_year: float | None = None,
) -> dict[str, Any]:
    if equity.empty:
        return {}
    eq = equity["equity"].astype(float)
    final = float(eq.iloc[-1])
    total_return = final / initial_cash - 1.0
    start = pd.Timestamp(equity["open_time"].iloc[0])
    end = pd.Timestamp(equity["open_time"].iloc[-1])
    days = max((end - start).total_seconds() / 86_400.0, 1.0 / 24.0)
    years = days / 365.25
    if periods_per_year is None:
        periods_per_year = len(eq) / years if years > 0 else 365.0
    rets = eq.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    vol = float(rets.std(ddof=1) * np.sqrt(periods_per_year)) if len(rets) > 2 else 0.0
    sharpe = (
        float(rets.mean() / rets.std(ddof=1) * np.sqrt(periods_per_year))
        if len(rets) > 2 and rets.std(ddof=1) > 0
        else 0.0
    )
    downside = np.minimum(rets.to_numpy(), 0.0)
    downside_dev = float(np.sqrt(np.mean(downside**2))) if len(downside) else 0.0
    sortino = (
        float(rets.mean() / downside_dev * np.sqrt(periods_per_year))
        if downside_dev > 0
        else 0.0
    )
    mdd = _max_drawdown(eq)
    cagr = (final / initial_cash) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    exposure = float(equity["in_position"].mean()) if "in_position" in equity else 0.0

    out: dict[str, Any] = {
        "initial_cash": round(initial_cash, 2),
        "final_equity": round(final, 2),
        "total_return": total_return,
        "cagr": cagr,
        "volatility": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "calmar": (cagr / abs(mdd)) if mdd < 0 else 0.0,
        "exposure": exposure,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "periods_per_year": periods_per_year,
    }

    if trades is not None and not trades.empty:
        pnl = trades["net_pnl"].astype(float)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        gross_win = float(wins.sum())
        gross_loss = float(-losses.sum())
        out.update(
            {
                "trades": int(len(trades)),
                "win_rate": float((pnl > 0).mean()),
                "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
                "avg_trade_pnl": float(pnl.mean()),
                "avg_trade_return": float(trades["return_pct"].mean()),
                "avg_win": float(wins.mean()) if len(wins) else 0.0,
                "avg_loss": float(losses.mean()) if len(losses) else 0.0,
                "expectancy": float(pnl.mean()),
                "avg_bars_held": float(trades["bars_held"].mean()),
                "best_trade": float(pnl.max()),
                "worst_trade": float(pnl.min()),
                "exit_reasons": trades["exit_reason"].value_counts().to_dict(),
            }
        )
    else:
        out.update(
            {
                "trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "avg_trade_pnl": 0.0,
                "avg_trade_return": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "expectancy": 0.0,
                "avg_bars_held": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
                "exit_reasons": {},
            }
        )
    return out


def entry_budget(
    equity: float,
    price: float,
    stop_distance: float,
    *,
    max_fraction: float,
    sizing: str,
    risk_per_trade: float,
) -> float:
    """Quote-currency budget for one entry (includes the entry fee)."""
    if price <= 0 or equity <= 0:
        return 0.0
    budget = equity * max_fraction
    if sizing == "risk" and stop_distance > 0:
        risk_budget = equity * risk_per_trade / (stop_distance / price)
        budget = min(budget, risk_budget)
    return max(0.0, budget)


def _equity_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(
            columns=["open_time", "close", "cash", "position_value", "equity", "drawdown", "in_position", "qty"]
        )
    frame = pd.DataFrame(records)
    peak = frame["equity"].cummax()
    frame["drawdown"] = frame["equity"] / peak - 1.0
    return frame


def build_benchmark(df: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    """Fully-invested buy & hold, same fee/slippage on entry."""
    if df.empty:
        return pd.DataFrame(columns=["open_time", "close", "equity", "drawdown"])
    slip = cfg.slippage_bps / 10_000.0
    first_open = float(df["open"].iloc[0])
    buy_px = first_open * (1.0 + slip)
    qty = cfg.initial_cash / (buy_px * (1.0 + cfg.fee_rate))
    equity = qty * df["close"].astype(float)
    out = pd.DataFrame({"open_time": df["open_time"].to_numpy(), "close": df["close"].to_numpy()})
    out["equity"] = equity.to_numpy()
    out["drawdown"] = out["equity"] / out["equity"].cummax() - 1.0
    return out


def run_backtest(
    df: pd.DataFrame,
    signals: pd.DataFrame,
    cfg: BacktestConfig,
    *,
    strategy: str = "trend",
    params: dict[str, Any] | None = None,
) -> BacktestResult:
    if signals.empty:
        raise ValueError("signals are empty")
    required = {
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "entry_signal",
        "exit_signal",
        "stop_atr",
        "trail_atr",
        "max_hold",
        "atr14",
    }
    missing = required.difference(signals.columns)
    if missing:
        raise ValueError(f"signals missing columns: {sorted(missing)}")

    s = signals.sort_values("open_time").reset_index(drop=True)
    n = len(s)
    times = list(s["open_time"])
    open_ = s["open"].to_numpy(dtype=float)
    high = s["high"].to_numpy(dtype=float)
    low = s["low"].to_numpy(dtype=float)
    close = s["close"].to_numpy(dtype=float)
    entry_sig = s["entry_signal"].to_numpy(dtype=bool)
    exit_sig = s["exit_signal"].to_numpy(dtype=bool)
    stop_atr = s["stop_atr"].to_numpy(dtype=float)
    trail_atr = s["trail_atr"].to_numpy(dtype=float)
    max_hold_arr = s["max_hold"].to_numpy(dtype=int)
    atr_arr = s["atr14"].to_numpy(dtype=float)

    slip = cfg.slippage_bps / 10_000.0
    fee = cfg.fee_rate
    cash = float(cfg.initial_cash)
    qty = 0.0
    entry_price = 0.0
    entry_time: pd.Timestamp | None = None
    entry_i = -1
    entry_cost = 0.0
    entry_fee = 0.0
    stop_price = 0.0
    highest_close = 0.0
    bars_held = 0
    pending_entry = False
    pending_exit = False
    pending_exit_reason = ""
    trades: list[Trade] = []
    records: list[dict[str, Any]] = []

    def close_trade(i: int, price: float, reason: str) -> None:
        nonlocal cash, qty, entry_price, entry_time, entry_i, entry_cost, entry_fee, bars_held
        if qty <= 0:
            return
        gross = qty * (price - entry_price)
        exit_fee = qty * price * fee
        proceeds = qty * price - exit_fee
        cash += proceeds
        net = proceeds - entry_cost
        trades.append(
            Trade(
                entry_time=entry_time if entry_time is not None else times[entry_i],
                exit_time=times[i],
                entry_price=float(entry_price),
                exit_price=float(price),
                qty=float(qty),
                notional=float(qty * entry_price),
                gross_pnl=float(gross),
                fees=float(entry_fee + exit_fee),
                net_pnl=float(net),
                return_pct=float(net / entry_cost) if entry_cost > 0 else 0.0,
                bars_held=int(bars_held),
                exit_reason=reason,
            )
        )
        qty = 0.0
        entry_price = 0.0
        entry_time = None
        entry_i = -1
        entry_cost = 0.0
        entry_fee = 0.0
        stop_price = 0.0
        highest_close = 0.0
        bars_held = 0

    for i in range(n):
        exited_this_bar = False

        # 1) Exit order from the previous close, filled at this open.
        if qty > 0 and pending_exit:
            px = open_[i] * (1.0 - slip)
            close_trade(i, px, pending_exit_reason or "signal")
            pending_exit = False
            pending_exit_reason = ""
            exited_this_bar = True

        # 2) Entry order from the previous close, filled at this open.
        if qty <= 0 and pending_entry and not exited_this_bar:
            pending_entry = False
            ref_i = i - 1
            ref_atr = atr_arr[ref_i] if ref_i >= 0 else np.nan
            ref_stop_atr = stop_atr[ref_i] if ref_i >= 0 else np.nan
            if np.isfinite(ref_atr) and ref_atr > 0 and np.isfinite(ref_stop_atr):
                px = open_[i] * (1.0 + slip)
                stop_distance = float(ref_stop_atr) * float(ref_atr)
                budget = entry_budget(
                    cash,
                    px,
                    stop_distance,
                    max_fraction=cfg.max_fraction,
                    sizing=cfg.sizing,
                    risk_per_trade=cfg.risk_per_trade,
                )
                if budget > 10.0 and stop_distance > 0 and px > stop_distance:
                    qty = budget / (px * (1.0 + fee))
                    entry_price = px
                    entry_time = times[i]
                    entry_i = i
                    entry_cost = qty * px * (1.0 + fee)
                    entry_fee = qty * px * fee
                    cash -= entry_cost
                    stop_price = entry_price - stop_distance
                    highest_close = entry_price
                    bars_held = 0
        else:
            pending_entry = False

        # 3) Intrabar protective stop.  Gap through the stop -> fill at open.
        if qty > 0 and stop_price > 0:
            if open_[i] <= stop_price:
                close_trade(i, open_[i] * (1.0 - slip), "gap_stop")
                exited_this_bar = True
            elif low[i] <= stop_price:
                close_trade(i, stop_price * (1.0 - slip), "stop")
                exited_this_bar = True

        # 4) Mark to market and update trailing stop on the close.
        if qty > 0:
            bars_held += 1
            highest_close = max(highest_close, close[i])
            if trail_atr[i] > 0 and np.isfinite(atr_arr[i]) and atr_arr[i] > 0:
                candidate = highest_close - float(trail_atr[i]) * float(atr_arr[i])
                stop_price = max(stop_price, candidate)
            if max_hold_arr[i] > 0 and bars_held >= max_hold_arr[i]:
                pending_exit = True
                pending_exit_reason = "time"
            elif exit_sig[i]:
                pending_exit = True
                pending_exit_reason = "signal"
        elif not exited_this_bar:
            pending_entry = bool(entry_sig[i]) and i < n - 1

        position_value = qty * close[i]
        records.append(
            {
                "open_time": times[i],
                "close": close[i],
                "cash": cash,
                "position_value": position_value,
                "equity": cash + position_value,
                "in_position": qty > 0,
                "qty": qty,
            }
        )

    # Close any remaining position at the final close so trade statistics are
    # complete.  The equity curve is adjusted for the exit fee.
    if qty > 0:
        px = close[-1] * (1.0 - slip)
        close_trade(n - 1, px, "end_of_data")
        records[-1]["cash"] = cash
        records[-1]["position_value"] = 0.0
        records[-1]["equity"] = cash
        records[-1]["in_position"] = False
        records[-1]["qty"] = 0.0

    equity = _equity_frame(records)
    trades_df = pd.DataFrame([t.__dict__ for t in trades], columns=TRADE_COLUMNS) if trades else _empty_trades()
    bench = build_benchmark(s, cfg)
    res = BacktestResult(
        strategy=strategy,
        params=dict(params or {}),
        equity=equity,
        trades=trades_df,
        metrics=compute_metrics(equity, trades_df, cfg.initial_cash),
        benchmark_equity=bench,
        benchmark_metrics=compute_metrics(
            bench.rename(columns={"equity": "equity"}).assign(in_position=True),
            _empty_trades(),
            cfg.initial_cash,
        )
        if not bench.empty
        else {},
        config=cfg,
        signals=s,
    )
    return res
