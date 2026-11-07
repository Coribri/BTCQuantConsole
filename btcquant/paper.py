"""Local paper-trading broker for Binance spot BTC.

This module **cannot** place a real order:
- it never imports a signed Binance endpoint;
- it never reads API keys;
- it fills simulated orders locally against the public ticker.

The point is to practise the full loop -- data, signal, sizing, stop, state,
trade log -- before risking money.  A paper fill is not a real fill: queue
position, spread and adverse selection are only approximated by fees/slippage.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any

import pandas as pd

from .config import AppConfig
from .data import INTERVAL_MS, fetch_recent_klines, fetch_ticker
from .engine import entry_budget
from .strategies import generate_signals, strategy_snapshot


TRADE_FIELDS = [
    "trade_id",
    "entry_time",
    "exit_time",
    "entry_bar",
    "exit_bar",
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
    "signal_close_at_entry",
    "fill_vs_signal_bps",
]

EQUITY_FIELDS = [
    "time",
    "bar_time",
    "price",
    "cash",
    "qty",
    "position_value",
    "equity",
    "in_position",
    "stop_price",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _append_csv(path: Path, row: dict[str, Any], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


class PaperTrader:
    def __init__(self, cfg: AppConfig, *, strategy: str | None = None):
        self.cfg = cfg
        self.paper = cfg.paper
        self.market = cfg.market
        self.data_cfg = cfg.data
        self.strategy = strategy or cfg.strategy_name
        self.params = cfg.strategy_params_for(self.strategy)
        self.state_file = self.paper.state_file
        self.trades_file = self.paper.trades_file
        self.equity_file = self.paper.equity_file
        self.log_file = self.paper.log_file
        self.state = self._load_state()

    # ------------------------------------------------------------------ state
    def _new_state(self) -> dict[str, Any]:
        now = _now_iso()
        return {
            "mode": "paper",
            "symbol": self.market.symbol,
            "interval": self.market.interval,
            "strategy": self.strategy,
            "params": self.params,
            "created_at": now,
            "updated_at": now,
            "initial_cash": float(self.paper.initial_cash),
            "cash": float(self.paper.initial_cash),
            "qty": 0.0,
            "entry_price": None,
            "entry_time": None,
            "entry_bar": None,
            "entry_signal_close": None,
            "entry_cost": 0.0,
            "entry_fee": 0.0,
            "stop_price": None,
            "highest_close": None,
            "bars_held": 0,
            "last_processed_bar": None,
            "last_entry_bar": None,
            "last_price": None,
            "equity": float(self.paper.initial_cash),
            "realized_pnl": 0.0,
            "fees_paid": 0.0,
            "trades": 0,
            "last_signal": {},
            "last_action": "INIT",
            "last_action_reason": "state initialized",
        }

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return self._new_state()
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"corrupt paper state: {self.state_file}") from exc
        if state.get("mode") != "paper":
            raise RuntimeError("refusing to load a state file that is not paper mode")
        if state.get("strategy") and state["strategy"] != self.strategy:
            raise RuntimeError(
                f"paper state uses strategy={state['strategy']!r}, config uses {self.strategy!r}; "
                "reset the paper account before switching strategies"
            )
        # Forward-compatible defaults for fields added in later versions.
        base = self._new_state()
        for key, value in base.items():
            state.setdefault(key, value)
        return state

    def save(self) -> None:
        self.state["updated_at"] = _now_iso()
        _atomic_write_json(self.state_file, self.state)

    def reset(self, *, archive: bool = True) -> Path | None:
        archive_dir: Path | None = None
        if archive:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            archive_dir = self.cfg.root / "runtime" / "archive" / stamp
            archive_dir.mkdir(parents=True, exist_ok=True)
            for path in (self.state_file, self.trades_file, self.equity_file, self.log_file):
                if path.exists():
                    shutil.move(str(path), archive_dir / path.name)
        else:
            for path in (self.state_file, self.trades_file, self.equity_file, self.log_file):
                if path.exists():
                    path.unlink()
        self.state = self._new_state()
        self.save()
        return archive_dir

    # ----------------------------------------------------------------- signals
    def _latest_signal(self) -> tuple[pd.DataFrame, dict[str, Any]]:
        # Fetch enough closed bars for the longest lookback plus warm-up.
        regime = int(self.params.get("regime", 200))
        slow = int(self.params.get("slow", 100))
        limit = max(400, regime + 100, slow + 100)
        klines = fetch_recent_klines(self.data_cfg, self.market, limit=limit)
        minimum = max(250, regime + 40)
        if len(klines) < minimum:
            raise RuntimeError(f"not enough closed bars: {len(klines)} < {minimum}")
        signals = generate_signals(klines, self.strategy, self.params)
        snap = strategy_snapshot(signals, self.strategy)
        return signals, snap

    # ------------------------------------------------------------------ orders
    def _buy(self, price: float, bar_time: pd.Timestamp, signal_close: float, snap: dict[str, Any]) -> bool:
        slip = self.paper.slippage_bps / 10_000.0
        fill = price * (1.0 + slip)
        atr_value = float(snap["atr14"])
        stop_atr = float(snap["stop_atr"])
        stop_distance = stop_atr * atr_value
        if not (stop_distance > 0 and fill > stop_distance):
            self._event("SKIP_BUY", "invalid stop distance", price, snap)
            return False
        budget = entry_budget(
            float(self.state["cash"]),
            fill,
            stop_distance,
            max_fraction=self.paper.max_fraction,
            sizing=self.paper.sizing,
            risk_per_trade=self.paper.risk_per_trade,
        )
        if budget <= 10.0:
            self._event("SKIP_BUY", f"budget too small: {budget:.2f}", price, snap)
            return False
        qty = budget / (fill * (1.0 + self.paper.fee_rate))
        entry_cost = qty * fill * (1.0 + self.paper.fee_rate)
        entry_fee = qty * fill * self.paper.fee_rate
        if entry_cost > float(self.state["cash"]) + 1e-9:
            self._event("SKIP_BUY", "insufficient simulated cash", price, snap)
            return False
        self.state["cash"] = float(self.state["cash"]) - entry_cost
        self.state["qty"] = qty
        self.state["entry_price"] = fill
        self.state["entry_time"] = _now_iso()
        self.state["entry_bar"] = bar_time.isoformat()
        self.state["entry_signal_close"] = signal_close
        self.state["entry_cost"] = entry_cost
        self.state["entry_fee"] = entry_fee
        self.state["stop_price"] = fill - stop_distance
        self.state["highest_close"] = fill
        self.state["bars_held"] = 0
        self.state["last_entry_bar"] = bar_time.isoformat()
        self.state["realized_pnl"] = float(self.state["realized_pnl"])
        self.state["last_action"] = "BUY"
        self.state["last_action_reason"] = "entry_signal"
        self._event(
            "BUY",
            f"entry_signal; stop={self.state['stop_price']:.2f}; qty={qty:.8f}",
            price,
            snap,
            fill=fill,
            signal_close=signal_close,
        )
        return True

    def _sell(self, price: float, reason: str, bar_time: pd.Timestamp, snap: dict[str, Any]) -> bool:
        qty = float(self.state["qty"])
        if qty <= 0:
            return False
        slip = self.paper.slippage_bps / 10_000.0
        fill = price * (1.0 - slip)
        entry_price = float(self.state["entry_price"])
        entry_cost = float(self.state["entry_cost"])
        entry_fee = float(self.state["entry_fee"])
        exit_fee = qty * fill * self.paper.fee_rate
        proceeds = qty * fill - exit_fee
        net = proceeds - entry_cost
        gross = qty * (fill - entry_price)
        self.state["cash"] = float(self.state["cash"]) + proceeds
        self.state["realized_pnl"] = float(self.state["realized_pnl"]) + net
        self.state["fees_paid"] = float(self.state["fees_paid"]) + entry_fee + exit_fee
        self.state["trades"] = int(self.state["trades"]) + 1
        trade = {
            "trade_id": int(self.state["trades"]),
            "entry_time": self.state["entry_time"],
            "exit_time": _now_iso(),
            "entry_bar": self.state["entry_bar"],
            "exit_bar": bar_time.isoformat(),
            "entry_price": entry_price,
            "exit_price": fill,
            "qty": qty,
            "notional": qty * entry_price,
            "gross_pnl": gross,
            "fees": entry_fee + exit_fee,
            "net_pnl": net,
            "return_pct": net / entry_cost if entry_cost else 0.0,
            "bars_held": int(self.state["bars_held"]),
            "exit_reason": reason,
            "signal_close_at_entry": self.state.get("entry_signal_close"),
            "fill_vs_signal_bps": (
                (entry_price / float(self.state["entry_signal_close"]) - 1.0) * 10_000.0
                if self.state.get("entry_signal_close")
                else None
            ),
        }
        _append_csv(self.trades_file, trade, TRADE_FIELDS)
        self.state.update(
            {
                "qty": 0.0,
                "entry_price": None,
                "entry_time": None,
                "entry_bar": None,
                "entry_signal_close": None,
                "entry_cost": 0.0,
                "entry_fee": 0.0,
                "stop_price": None,
                "highest_close": None,
                "bars_held": 0,
                "last_action": "SELL",
                "last_action_reason": reason,
            }
        )
        self._event(
            "SELL",
            f"{reason}; net={net:.2f}",
            price,
            snap,
            fill=fill,
            net_pnl=net,
        )
        return True

    # ------------------------------------------------------------------- loop
    def step(self) -> dict[str, Any]:
        signals, snap = self._latest_signal()
        last = signals.iloc[-1]
        bar_time = pd.Timestamp(last["open_time"])
        if bar_time.tzinfo is None:
            bar_time = bar_time.tz_localize("UTC")
        ticker = fetch_ticker(self.data_cfg, self.market.symbol)
        price = float(ticker["price"])
        interval_s = INTERVAL_MS[self.market.interval] / 1000.0
        last_processed = (
            pd.Timestamp(self.state["last_processed_bar"])
            if self.state.get("last_processed_bar")
            else None
        )
        new_bar = last_processed is None or bar_time > last_processed
        sold_this_step = False
        traded_this_step = False

        # Stops are checked on every poll, not only when a bar closes.
        if float(self.state["qty"]) > 0 and self.state.get("stop_price"):
            if price <= float(self.state["stop_price"]):
                if self._sell(price, "stop", bar_time, snap):
                    sold_this_step = True
                    traded_this_step = True

        if new_bar:
            if float(self.state["qty"]) > 0:
                missed = 1
                if last_processed is not None:
                    missed = max(1, int(round((bar_time - last_processed).total_seconds() / interval_s)))
                self.state["bars_held"] = int(self.state["bars_held"]) + missed
                highest = max(float(self.state["highest_close"]), float(last["close"]))
                self.state["highest_close"] = highest
                if float(last["trail_atr"]) > 0 and float(last["atr14"]) > 0:
                    candidate = highest - float(last["trail_atr"]) * float(last["atr14"])
                    self.state["stop_price"] = max(float(self.state["stop_price"]), candidate)
                if price <= float(self.state["stop_price"]):
                    if self._sell(price, "trail_stop", bar_time, snap):
                        sold_this_step = True
                        traded_this_step = True
                elif int(last["max_hold"]) > 0 and int(self.state["bars_held"]) >= int(last["max_hold"]):
                    if self._sell(price, "time", bar_time, snap):
                        sold_this_step = True
                        traded_this_step = True
                elif bool(last["exit_signal"]):
                    if self._sell(price, "signal", bar_time, snap):
                        sold_this_step = True
                        traded_this_step = True
            elif not sold_this_step and bool(last["entry_signal"]):
                if self.state.get("last_entry_bar") != bar_time.isoformat():
                    if self._buy(price, bar_time, float(last["close"]), snap):
                        traded_this_step = True
            self.state["last_processed_bar"] = bar_time.isoformat()

        if not traded_this_step:
            self.state["last_action"] = "HOLD"
            if float(self.state["qty"]) > 0:
                self.state["last_action_reason"] = "hold_position"
            elif bool(last["entry_signal"]):
                self.state["last_action_reason"] = "entry_signal_already_processed"
            else:
                self.state["last_action_reason"] = "no_entry_signal"

        self.state["last_price"] = price
        self.state["equity"] = float(self.state["cash"]) + float(self.state["qty"]) * price
        self.state["last_signal"] = snap
        self.save()

        _append_csv(
            self.equity_file,
            {
                "time": _now_iso(),
                "bar_time": bar_time.isoformat(),
                "price": price,
                "cash": self.state["cash"],
                "qty": self.state["qty"],
                "position_value": float(self.state["qty"]) * price,
                "equity": self.state["equity"],
                "in_position": float(self.state["qty"]) > 0,
                "stop_price": self.state["stop_price"],
            },
            EQUITY_FIELDS,
        )
        event = {
            "time": _now_iso(),
            "action": self.state["last_action"],
            "reason": self.state["last_action_reason"],
            "price": price,
            "equity": self.state["equity"],
            "qty": self.state["qty"],
            "stop": self.state["stop_price"],
            "bar_time": bar_time.isoformat(),
            "entry_signal": bool(last["entry_signal"]),
            "exit_signal": bool(last["exit_signal"]),
            "atr14": float(last["atr14"]),
        }
        _append_jsonl(self.log_file, event)
        return self.summary()

    def summary(self) -> dict[str, Any]:
        qty = float(self.state["qty"])
        last_price = self.state.get("last_price")
        unrealized = (
            qty * (float(last_price) - float(self.state["entry_price"]))
            if qty > 0 and last_price is not None
            else 0.0
        )
        return {
            "mode": "paper",
            "symbol": self.state["symbol"],
            "interval": self.state["interval"],
            "strategy": self.state["strategy"],
            "equity": float(self.state["equity"]),
            "cash": float(self.state["cash"]),
            "qty": qty,
            "in_position": qty > 0,
            "entry_price": self.state.get("entry_price"),
            "stop_price": self.state.get("stop_price"),
            "unrealized_pnl": unrealized,
            "realized_pnl": float(self.state["realized_pnl"]),
            "fees_paid": float(self.state["fees_paid"]),
            "trades": int(self.state["trades"]),
            "last_price": last_price,
            "last_processed_bar": self.state.get("last_processed_bar"),
            "last_action": self.state.get("last_action"),
            "last_action_reason": self.state.get("last_action_reason"),
            "last_signal": self.state.get("last_signal", {}),
        }

    def _event(
        self,
        action: str,
        reason: str,
        price: float,
        snap: dict[str, Any],
        **extra: Any,
    ) -> None:
        payload = {
            "time": _now_iso(),
            "action": action,
            "reason": reason,
            "price": price,
            "bar_time": snap.get("bar_time"),
            **extra,
        }
        _append_jsonl(self.log_file, payload)

    def run_forever(self, *, interval_seconds: int | None = None, max_steps: int | None = None) -> None:
        interval = interval_seconds or self.paper.poll_seconds
        step = 0
        while True:
            try:
                summary = self.step()
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] {summary['last_action']:4s} "
                    f"price={summary['last_price']:.2f} equity={summary['equity']:.2f} "
                    f"pos={'LONG' if summary['in_position'] else 'FLAT'}"
                )
            except Exception as exc:  # keep the loop alive through transient API errors
                print(f"[{datetime.now().strftime('%H:%M:%S')}] WARN {exc}")
            step += 1
            if max_steps is not None and step >= max_steps:
                return
            time.sleep(interval)


def format_status(summary: dict[str, Any]) -> str:
    signal = summary.get("last_signal", {})
    lines = [
        "=== PAPER TRADING — 本地虚拟盘，不会发出真实订单 ===",
        f"symbol       : {summary['symbol']}  {summary['interval']}",
        f"strategy     : {summary['strategy']}",
        f"equity       : {summary['equity']:,.2f} USDT",
        f"cash         : {summary['cash']:,.2f} USDT",
        f"position     : {'LONG ' + format(summary['qty'], '.8f') + ' BTC' if summary['in_position'] else 'FLAT'}",
        f"last price   : {summary['last_price'] if summary['last_price'] is not None else 'n/a'}",
        f"realized PnL : {summary['realized_pnl']:,.2f} USDT",
        f"unrealized   : {summary['unrealized_pnl']:,.2f} USDT",
        f"trades       : {summary['trades']}",
        f"last bar     : {summary['last_processed_bar']}",
        f"last action  : {summary['last_action']} ({summary['last_action_reason']})",
    ]
    if summary.get("entry_price"):
        lines.append(f"entry / stop : {summary['entry_price']:,.2f} / {summary['stop_price']:,.2f}")
    if signal:
        lines.append(
            "last signal  : "
            f"entry={signal.get('entry_signal')} exit={signal.get('exit_signal')} "
            f"close={signal.get('close')} atr={signal.get('atr14')}"
        )
    return "\n".join(lines)
