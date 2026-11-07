"""Spot long-only grid trading simulator.

This is a deliberately simplified, local paper implementation inspired by the
per-level state machine in ritmex-bot's grid guide:

    idle -> holding (buy at a grid line) -> idle (sell one line higher)

It never sends an exchange order.  It is meant for learning the mechanics,
range selection, fee drag and inventory risk before using a real grid bot.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4

import pandas as pd
import requests

from .broker import LIVE_FINAL, LivePreflight, _load_json
from .config import AppConfig
from .data import _clean_klines, _rows_to_frame, fetch_recent_klines, fetch_ticker
from .quant_risk import data_guard, update_trailing


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class GridPaperService:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.symbol = cfg.market.symbol.upper()
        suffix = "" if self.symbol == "BTCUSDT" else f"_{self.symbol.lower()}"
        self.state_file = cfg.root / "runtime" / f"grid_paper_state{suffix}.json"
        self.lock = threading.RLock()
        self.running = False
        self.thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.state = self._load()

    # ------------------------------------------------------------------ state
    def _default_config(self) -> dict[str, Any]:
        return {
            "lower": 74_000.0,
            "upper": 86_000.0,
            "grids": 20,
            "investment": 3_000.0,
            "mode": "arithmetic",
            "initial_ratio": 0.5,
            "stop_loss_pct": 0.08,
        }

    def _new_state(self) -> dict[str, Any]:
        cfg = self._default_config()
        initial = float(self.cfg.paper.initial_cash)
        return {
            "mode": "paper_grid",
            "created_at": _now(),
            "updated_at": _now(),
            "running": False,
            "initial_cash": initial,
            "cash": initial,
            "grid_cash": 0.0,
            "inventory": 0.0,
            "inventory_cost": 0.0,
            "realized_pnl": 0.0,
            "fees": 0.0,
            "fills": [],
            "events": [],
            "levels": [],
            "config": cfg,
            "anchor_price": None,
            "last_price": None,
            "started_at": None,
            "stopped_at": None,
            "stop_reason": None,
        }

    def _load(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return self._new_state()
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"corrupt grid state: {self.state_file}") from exc
        base = self._new_state()
        for key, value in base.items():
            state.setdefault(key, value)
        return state

    def save(self) -> None:
        self.state["updated_at"] = _now()
        _atomic(self.state_file, self.state)

    def _event(self, level: str, message: str) -> None:
        self.state.setdefault("events", []).append({"time": _now(), "level": level, "message": message})
        self.state["events"] = self.state["events"][-100:]

    # --------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="btcquant-grid-paper", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)

    def _loop(self) -> None:
        while self.running:
            try:
                self.poll()
            except Exception as exc:
                self.last_error = str(exc)
                self._event("error", f"grid poll: {exc}")
            time.sleep(2)

    def price(self) -> float:
        return float(fetch_ticker(self.cfg.data, self.cfg.market.symbol)["price"])

    def candles(self, interval: str = "1h", limit: int = 240) -> pd.DataFrame:
        return fetch_recent_klines(self.cfg.data, self.cfg.market, limit=limit, interval=interval)

    # ---------------------------------------------------------------- levels
    def _levels(self, lower: float, upper: float, grids: int, mode: str) -> list[float]:
        if mode == "geometric":
            ratio = (upper / lower) ** (1.0 / grids)
            return [lower * (ratio ** i) for i in range(grids + 1)]
        step = (upper - lower) / grids
        return [lower + step * i for i in range(grids + 1)]

    def start_grid(self, config: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if self.state.get("running"):
                raise ValueError("grid already running")
            if float(self.state.get("inventory", 0.0)) > 0 or float(self.state.get("grid_cash", 0.0)) > 0:
                raise ValueError("已有网格库存/资金；请先点击“重置模拟账户”再重新启动")
            lower = float(config.get("lower", 0))
            upper = float(config.get("upper", 0))
            grids = int(config.get("grids", 20))
            investment = float(config.get("investment", 0))
            mode = str(config.get("mode", "arithmetic"))
            initial_ratio = float(config.get("initial_ratio", 0.5))
            stop_loss_pct = float(config.get("stop_loss_pct", 0.08))
            if lower <= 0 or upper <= lower:
                raise ValueError("require 0 < lower < upper")
            if not 2 <= grids <= 100:
                raise ValueError("grids must be 2..100")
            if investment < 10:
                raise ValueError("investment must be >= 10 USDT")
            if investment > float(self.state["cash"]) + 1e-9:
                raise ValueError("investment exceeds available cash")
            if not 0 <= initial_ratio <= 1:
                raise ValueError("initial_ratio must be 0..1")
            if stop_loss_pct < 0 or stop_loss_pct > 0.5:
                raise ValueError("stop_loss_pct must be 0..0.5")
            live = self.price()
            levels = self._levels(lower, upper, grids, mode)
            self.state["config"] = {
                "lower": lower,
                "upper": upper,
                "grids": grids,
                "investment": investment,
                "mode": mode,
                "initial_ratio": initial_ratio,
                "stop_loss_pct": stop_loss_pct,
            }
            self.state["cash"] = float(self.state["cash"]) - investment
            self.state["grid_cash"] = investment
            self.state["inventory"] = 0.0
            self.state["inventory_cost"] = 0.0
            self.state["realized_pnl"] = 0.0
            self.state["fees"] = 0.0
            self.state["fills"] = []
            self.state["events"] = []
            self.state["anchor_price"] = live
            self.state["last_price"] = live
            self.state["started_at"] = _now()
            self.state["stopped_at"] = None
            self.state["stop_reason"] = None
            self.state["levels"] = [
                {"price": p, "state": "idle", "qty": 0.0, "entry": None, "target": None}
                for p in levels
            ]
            # Seed a starting inventory so the first sells above the anchor
            # have something to sell. The rest stays as buy-side grid cash.
            if lower <= live <= upper and initial_ratio > 0:
                idx = max(0, min(len(levels) - 2, sum(1 for p in levels if p <= live) - 1))
                if idx + 1 < len(levels):
                    seed_quote = investment * initial_ratio
                    fee = seed_quote * self.cfg.paper.fee_rate
                    qty = seed_quote / live
                    self.state["grid_cash"] = investment - seed_quote - fee
                    self.state["inventory"] = qty
                    self.state["inventory_cost"] = seed_quote + fee
                    self.state["levels"][idx].update(
                        {"state": "holding", "qty": qty, "entry": live, "target": idx + 1}
                    )
                    self.state["fees"] = float(self.state["fees"]) + fee
                    self._append_fill("BUY", live, qty, fee, 0.0, "seed")
            self.state["running"] = True
            self._event("info", f"grid started {lower:,.2f}-{upper:,.2f}, {grids} lines, {mode}")
            self.save()
            return self.status()

    def stop_grid(self, *, liquidate: bool = False, reason: str = "manual") -> dict[str, Any]:
        with self.lock:
            if not self.state.get("running"):
                return self.status()
            price = self._safe_price()
            if liquidate and float(self.state["inventory"]) > 0:
                self._sell_all(price, reason)
            self.state["running"] = False
            self.state["stopped_at"] = _now()
            self.state["stop_reason"] = reason
            self._event("info", f"grid stopped: {reason}")
            self.save()
            return self.status()

    def reset(self) -> dict[str, Any]:
        with self.lock:
            if self.state.get("running"):
                self.state["running"] = False
                self.state["stopped_at"] = _now()
            initial = float(self.cfg.paper.initial_cash)
            self.state = self._new_state()
            self.state["cash"] = initial
            self._event("info", "grid paper account reset")
            self.save()
            return self.status()

    # ------------------------------------------------------------------ fills
    def _append_fill(self, side: str, price: float, qty: float, fee: float, pnl: float, reason: str) -> None:
        self.state.setdefault("fills", []).append(
            {
                "time": _now(),
                "side": side,
                "price": price,
                "qty": qty,
                "fee": fee,
                "pnl": pnl,
                "reason": reason,
            }
        )
        self.state["fills"] = self.state["fills"][-300:]

    def _buy(self, price: float, quote: float, reason: str) -> bool:
        fee = quote * self.cfg.paper.fee_rate
        cost = quote + fee
        if cost > float(self.state["grid_cash"]) + 1e-9:
            return False
        qty = quote / price
        self.state["grid_cash"] = float(self.state["grid_cash"]) - cost
        self.state["inventory"] = float(self.state["inventory"]) + qty
        self.state["inventory_cost"] = float(self.state["inventory_cost"]) + cost
        self.state["fees"] = float(self.state["fees"]) + fee
        self._append_fill("BUY", price, qty, fee, 0.0, reason)
        return True

    def _sell(self, price: float, qty: float, reason: str) -> bool:
        qty = min(qty, float(self.state["inventory"]))
        if qty <= 0:
            return False
        avg_cost = float(self.state["inventory_cost"]) / float(self.state["inventory"])
        gross = qty * price
        fee = gross * self.cfg.paper.fee_rate
        pnl = gross - fee - avg_cost * qty
        self.state["grid_cash"] = float(self.state["grid_cash"]) + gross - fee
        self.state["inventory"] = float(self.state["inventory"]) - qty
        self.state["inventory_cost"] = max(0.0, float(self.state["inventory_cost"]) - avg_cost * qty)
        self.state["realized_pnl"] = float(self.state["realized_pnl"]) + pnl
        self.state["fees"] = float(self.state["fees"]) + fee
        self._append_fill("SELL", price, qty, fee, pnl, reason)
        return True

    def _sell_all(self, price: float, reason: str) -> None:
        qty = float(self.state["inventory"])
        if qty > 0:
            self._sell(price, qty, reason)
        self.state["levels"] = [
            {**level, "state": "idle", "qty": 0.0, "entry": None, "target": None}
            for level in self.state.get("levels", [])
        ]

    # ------------------------------------------------------------------- poll
    def poll(self) -> None:
        with self.lock:
            if not self.state.get("running"):
                return
            price = self._safe_price()
            if price <= 0:
                return
            prev = float(self.state.get("last_price") or price)
            config = self.state["config"]
            lower, upper = float(config["lower"]), float(config["upper"])
            stop_loss = float(config["stop_loss_pct"])
            if price <= lower * (1.0 - stop_loss):
                self._event("error", f"stop loss below {lower * (1 - stop_loss):,.2f}")
                self._sell_all(price, "stop_loss")
                self.state["running"] = False
                self.state["stopped_at"] = _now()
                self.state["stop_reason"] = "stop_loss"
                self.save()
                return
            if price >= upper:
                self._event("info", f"price reached upper {upper:,.2f}; take profit and stop")
                self._sell_all(price, "upper_exit")
                self.state["running"] = False
                self.state["stopped_at"] = _now()
                self.state["stop_reason"] = "upper_exit"
                self.save()
                return
            levels = self.state.get("levels", [])
            quote_per_grid = float(config["investment"]) / float(config["grids"])

            # Price fell through lines: buy the idle lines from high to low.
            if price < prev:
                for i in range(len(levels) - 1, -1, -1):
                    level = levels[i]
                    p = float(level["price"])
                    if p <= prev and p > price and level["state"] == "idle":
                        if i + 1 < len(levels) and self._buy(p, quote_per_grid, f"grid_buy_L{i}"):
                            qty = quote_per_grid / p
                            level.update({"state": "holding", "qty": qty, "entry": p, "target": i + 1})
            # Price rose through lines: sell the holding line immediately below.
            elif price > prev:
                for i in range(len(levels)):
                    level = levels[i]
                    p = float(level["price"])
                    if p >= prev and p < price and i > 0:
                        below = levels[i - 1]
                        if below["state"] == "holding" and below.get("target") == i:
                            if self._sell(p, float(below["qty"]), f"grid_sell_L{i}"):
                                below.update({"state": "idle", "qty": 0.0, "entry": None, "target": None})
            self.state["last_price"] = price
            self.save()

    def _safe_price(self) -> float:
        try:
            return self.price()
        except Exception as exc:
            self.last_error = str(exc)
            return 0.0

    # ------------------------------------------------------------------- read
    def status(self) -> dict[str, Any]:
        with self.lock:
            price = self.state.get("last_price")
            if price is None:
                price = self._safe_price()
            price = float(price or 0.0)
            inventory = float(self.state.get("inventory", 0.0))
            cost = float(self.state.get("inventory_cost", 0.0))
            unrealized = inventory * price - cost if inventory > 0 else 0.0
            equity = float(self.state["cash"]) + float(self.state["grid_cash"]) + inventory * price
            initial = float(self.state["initial_cash"])
            return {
                "mode": "paper_grid",
                "price": price,
                "running": bool(self.state.get("running")),
                "source": "Binance spot mark · local grid simulator",
                "config": self.state.get("config", {}),
                "levels": list(self.state.get("levels", [])),
                "account": {
                    "equity": equity,
                    "cash": float(self.state["cash"]),
                    "grid_cash": float(self.state["grid_cash"]),
                    "inventory": inventory,
                    "inventory_cost": cost,
                    "avg_cost": cost / inventory if inventory > 0 else 0.0,
                    "unrealized_pnl": unrealized,
                    "realized_pnl": float(self.state["realized_pnl"]),
                    "fees": float(self.state["fees"]),
                    "session_pnl": equity - initial,
                    "initial_cash": initial,
                },
                "fills": list(reversed(self.state.get("fills", [])[-30:])),
                "events": list(self.state.get("events", [])[-30:]),
                "anchor_price": self.state.get("anchor_price"),
                "last_error": self.last_error,
                "updated_at": self.state.get("updated_at"),
            }


class HyperliquidAuto:
    """One explicitly armed testnet model, with venue-side protection."""

    def __init__(self, venue, execution, models):
        self.venue, self.execution, self.models = venue, execution, models
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.preview = None
        self.plan = None

    def status(self):
        with self.lock:
            if not self.plan:
                return {"plan": None}
            return {"plan": {k: v for k, v in self.plan.items() if k not in {"model", "account", "owner"}}}

    def _require_flat(self, coin):
        state, position = self.venue.exposure(coin)
        if (position and Decimal(position["szi"])) or self.venue.orders_for(coin):
            raise ValueError("启动或开仓前，请先处理该合约已有的仓位和委托")
        return state

    def _leverage(self, coin):
        asset = self.venue._info(self.venue.NETWORK, "activeAssetData", user=self.venue.owner, coin=coin)
        leverage = asset["leverage"]
        if leverage["type"] != "isolated" or not 1 <= int(leverage["value"]) <= 10:
            raise ValueError("请先在 Hyperliquid 将该测试网合约设为逐仓、1–10×")
        return int(leverage["value"])

    def prepare(self, opts):
        with self.lock:
            if self.plan and self.plan["status"] == "running":
                raise ValueError("请先暂停运行中的测试网 AUTO")
            _, account = self.execution._context("hyperliquid", "futures")
            if opts["address"].lower() != self.venue.owner:
                raise ValueError("当前钱包尚未授权测试网代理")
            spec = self.models.get(opts["model_id"])
            if spec.scope != "futures" or spec.kind != "rules" or spec.ai_mode != "off":
                raise ValueError("测试网 AUTO 使用合约本地规则模型")
            coin = opts["symbol"][:-4]
            self._require_flat(coin)
            self.venue.contract(coin)
            plan = {
                "symbol": opts["symbol"], "interval": opts["interval"], "amount": opts["amount"],
                "model_id": spec.id, "model": spec.model_dump(mode="json"), "account": account,
                "owner": self.venue.owner, "leverage": self._leverage(coin),
            }
            token = uuid4().hex
            self.preview = (token, time.monotonic() + 60, plan)
            return {"confirmation": token, "leverage": plan["leverage"],
                    "risk_fraction": spec.risk_fraction, "atr_stop": spec.parameters.atr_stop}

    def start(self, token):
        with self.lock:
            if not self.preview or self.preview[0] != token or self.preview[1] < time.monotonic():
                raise ValueError("AUTO 确认已过期，请重新预览")
            _, _, plan = self.preview
            self.preview = None
            if self.execution._context("hyperliquid", "futures")[1] != plan["account"]:
                raise ValueError("代理账户已变化")
            self._require_flat(plan["symbol"][:-4])
            self.plan = dict(plan, status="running", message="等待首次收盘信号检查")
            if not self.thread or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, name="hyperliquid-testnet-auto", daemon=True)
                self.thread.start()
            return self.status()

    def pause(self):
        with self.lock:
            self.preview = None
            if self.plan and self.plan["status"] == "running":
                self.plan.update(status="paused", message="已暂停新单，交易所现有仓位及保护委托保留。")
            return self.status()

    def disable_agent(self):
        with self.lock:
            self.pause()
            return self.venue.disable_agent()

    def shutdown(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=8)

    def blocks_manual(self, symbol):
        return self.plan and self.plan["status"] == "running" and self.plan["symbol"] == symbol

    def _loop(self):
        while not self.stop_event.wait(15):
            with self.lock:
                if not self.plan or self.plan["status"] != "running":
                    continue
                try:
                    self._tick(self.plan)
                except (ValueError, KeyError, TypeError, InvalidOperation, IndexError) as exc:
                    self.plan.update(status="paused", message="AUTO 已暂停，请核对测试网仓位与委托。" + str(exc)[:160])

    def _send(self, plan, **fields):
        order_id = "bq" + uuid4().hex
        plan["last_order"] = order_id
        row = self.execution.strategy_order(
            dict(exchange="hyperliquid", market="futures", symbol=plan["symbol"],
                 source="auto", type="MARKET", dex_owner=plan["owner"], **fields),
            order_id, plan["account"],
        )
        if row["status"] != "FILLED":
            raise ValueError("IOC 订单未确认全部成交，需检查部分成交和保护委托")
        return order_id

    def _clear_stop(self, plan, coin):
        stop = plan["position"]["stop"]
        orders_for_coin = self.venue.orders_for(coin)
        if any(o.get("cloid") == stop for o in orders_for_coin):
            asset, _, _ = self.venue.contract(coin)
            raw = self.venue.trade({"type": "cancelByCloid", "cancels": [{"asset": asset, "cloid": stop}]})
            if self.venue._statuses(raw, "cancel")[0] != "success":
                raise ValueError("旧保护委托撤单待核对")
        elif orders_for_coin:
            raise ValueError("该合约仍有委托，请在交易所核对")
        plan.pop("position")

    def _check_protection(self, plan, coin):
        tracked = plan.get("position")
        if not tracked:
            return
        _, raw = self.venue.exposure(coin)
        size = Decimal(raw["szi"]) if raw else Decimal(0)
        if not size:
            self._clear_stop(plan, coin)
            return
        if (size > 0) != (tracked["side"] == "LONG") or abs(size) != Decimal(tracked["quantity"]):
            raise ValueError("交易所仓位已变化，AUTO 暂停管理")
        stop = self.venue._info(self.venue.NETWORK, "orderStatus", user=plan["owner"], oid=tracked["stop"])
        order = stop.get("order", {}).get("order", {})
        if (stop.get("status") != "order" or stop["order"].get("status") != "open"
            or order.get("cloid") != tracked["stop"] or order.get("coin") != coin
            or order.get("reduceOnly") is not True or order.get("isTrigger") is not True):
            raise ValueError("未确认交易所止损有效，立即检查测试网仓位")

    def _tick(self, plan):
        if self.execution._context("hyperliquid", "futures")[1] != plan["account"]:
            raise ValueError("代理账户已变化")
        interval, coin = plan["interval"], plan["symbol"][:-4]
        self._check_protection(plan, coin)
        data = self.venue.candles(self.venue.NETWORK, coin, interval, 400)
        if not data:
            plan["message"] = "等待已收盘 K 线"
            return
        frame = pd.DataFrame(data)
        frame["open_time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        step = pd.Timedelta(milliseconds=self.venue.INTERVAL_MS[interval])
        frame = frame[frame["open_time"] + step <= pd.Timestamp.now(tz="UTC")].reset_index(drop=True)
        frame.attrs["symbol"] = plan["symbol"]
        if guard := data_guard(frame, interval):
            plan["message"] = guard
            return
        if len(frame) < 220:
            plan["message"] = "历史收盘 K 线不足 220 根"
            return
        spec = self.models.get(plan["model_id"])
        if spec.model_dump(mode="json") != plan["model"]:
            raise ValueError("模型参数已变更，请重新预览")
        bar = str(frame.iloc[-1]["open_time"])
        if plan.get("last_bar") == bar:
            return
        signal = self.models.evaluate_nonblocking(frame, spec.id, interval, "futures", cost=.0024).iloc[-1]
        plan.update(last_bar=bar, message="已检查收盘信号，等待下一根")
        tracked = plan.get("position")
        if tracked:
            if bool(signal["short_exit_signal" if tracked["side"] == "SHORT" else "exit_signal"]):
                self._send(plan, action="CLOSE", position=tracked["side"], quantity=tracked["quantity"])
                self._check_protection(plan, coin)
            return
        side = "LONG" if bool(signal["entry_signal"]) else "SHORT" if bool(signal["short_entry_signal"]) else None
        if not side:
            return
        account = self._require_flat(coin)
        if self._leverage(coin) != plan["leverage"]:
            raise ValueError("交易所杠杆已变化")
        _, decimals, mark = self.venue.contract(coin)
        amount = Decimal(plan["amount"])
        quantity = (amount / (mark * Decimal("1.005"))).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN)
        distance = Decimal(str(signal.atr14)) * Decimal(str(spec.parameters.atr_stop))
        equity = Decimal(account["marginSummary"]["accountValue"])
        available = Decimal(account["withdrawable"])
        if (not distance.is_finite() or distance <= 0 or quantity * mark < Decimal("10.1")
            or distance / mark >= Decimal(1) / plan["leverage"] - Decimal(".01")
            or amount / plan["leverage"] > available
            or quantity * (distance + mark * Decimal(".1074")) > equity * Decimal(str(spec.risk_fraction))):
            plan["message"] = "名义金额未通过最小委托、保证金或止损风险检查"
            return
        bound = mark - distance if side == "LONG" else mark + distance
        trigger = bound.quantize(self.venue.price_tick(bound, decimals), rounding=ROUND_DOWN if side == "LONG" else ROUND_UP)
        order_id = self._send(plan, action="OPEN", position=side, quantity=str(quantity), stop_price=str(trigger))
        plan["position"] = {"side": side, "quantity": str(quantity), "stop": self.venue.stop_cloid(order_id)}
        self._check_protection(plan, coin)
        plan["message"] = "已核对测试网仓位和交易所保护止损"


class LiveStrategies:
    """Mainnet buy-first grids and fixed-notional Binance AUTO plans."""

    def __init__(self, root, execution, models):
        self.path = root / "runtime" / "live_strategies.json"
        self.risk_path = self.path.with_name("live_risk.json")
        self.execution = execution
        self.models = models
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.confirmations = {}
        self.flatten_confirmation = None
        self.plans = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        for plan in self.plans.values():
            if plan["status"] in {"running", "stopping"}:
                plan.update(status="paused", message="程序已重启。请核对交易所订单，再手动继续或停止。")

    RISK_DEFAULTS = {"max_order": 10, "max_symbol": 50, "max_margin": 50,
                     "max_daily_loss": 50, "max_losses": 3}

    def risk_settings(self, values=None):
        with self.lock:
            if values is not None:
                _atomic(self.risk_path, values)
            return {**self.RISK_DEFAULTS, **_load_json(self.risk_path)}

    def _risk_check(self, plan, connector, leverage=1):
        if self.execution.preflight is None:
            return
        limits = self.risk_settings()
        amount = Decimal(plan["amount"])
        order_size = amount if plan["market"] == "spot" else amount / Decimal(str(leverage))
        if order_size > Decimal(str(limits["max_order"])):
            raise ValueError("AUTO 单笔保证金/名义价值超过本机风控上限")
        if plan["market"] == "futures":
            wallet = connector.signed("GET", "/fapi/v3/account")
            positions = connector.signed("GET", "/fapi/v3/positionRisk", {"symbol": plan["symbol"]})
            exposure = sum((abs(Decimal(p["notional"])) for p in positions), Decimal(0))
            if exposure + amount > Decimal(str(limits["max_symbol"])):
                raise ValueError("该币种的合约名义敞口超过本机上限")
            margin = Decimal(wallet["totalInitialMargin"]) + amount / leverage
            if margin > Decimal(str(limits["max_margin"])):
                raise ValueError("合约账户累计初始保证金超过本机上限")
        else:
            snapshot = connector.snapshot()
            if not snapshot.get("configured") or snapshot.get("error"):
                raise ValueError("现货账户无法读取，已停止新开仓")
            held = next((Decimal(str(row["total"])) for row in snapshot["balances"]
                         if row["asset"] == plan["symbol"][:-4]), Decimal(0))
            pending = connector.signed("GET", "/api/v3/openOrders", {"symbol": plan["symbol"]})
            pending_buy = sum((Decimal(row["price"]) * (Decimal(row["origQty"]) - Decimal(row["executedQty"]))
                               for row in pending if row["side"] == "BUY"), Decimal(0))
            if any(row["side"] == "BUY" and Decimal(row["price"]) <= 0 for row in pending):
                raise ValueError("存在无法估价的现货买单，请先核对")
            exposure = held * self._price("binance", "spot", plan["symbol"]) + pending_buy if held else pending_buy
            if exposure + amount > Decimal(str(limits["max_symbol"])):
                raise ValueError("该币种的现货敞口超过本机上限")
        if plan["market"] == "futures":
            now = datetime.now(timezone(timedelta(hours=8)))
            start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            income = connector.signed("GET", "/fapi/v1/income", {"startTime": start, "limit": 1000})
            if not isinstance(income, list) or len(income) >= 1000:
                raise ValueError("当日合约损益记录不完整，已停止新开仓")
            settled = [item for item in income if item["asset"] == "USDT" and int(item["time"]) >= start]
            total = sum((Decimal(item["income"]) for item in settled if item["incomeType"]
                         in {"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"}), Decimal(0))
            recent = [Decimal(item["income"]) for item in sorted(settled, key=lambda item: int(item["time"]), reverse=True)
                      if item["incomeType"] == "REALIZED_PNL" and Decimal(item["income"])]
        else:
            now = datetime.now(timezone(timedelta(hours=8)))
            start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            rows = sorted((result for p in self.plans.values() if p["market"] == "spot" and p["account"] == plan["account"]
                           for result in p.get("realized", []) if result["time"] >= start),
                          key=lambda item: item["time"], reverse=True)
            recent = [Decimal(item["pnl"]) for item in rows if Decimal(item["pnl"])]
            total = sum(recent, Decimal(0))
        if total <= -Decimal(str(limits["max_daily_loss"])):
            raise ValueError("已触及香港时间当日亏损上限")
        count = int(limits["max_losses"])
        if len(recent) >= count and all(pnl < 0 for pnl in recent[:count]):
            raise ValueError("已触发连续亏损熔断")

    def _save(self):
        _atomic(self.path, self.plans)

    def start_worker(self):
        if self.thread is None or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._loop, daemon=True, name="live-strategies")
            self.thread.start()

    def shutdown(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=8)

    def _loop(self):
        while not self.stop_event.wait(2):
            with self.lock:
                for plan in list(self.plans.values()):
                    if self.stop_event.is_set():
                        break
                    if plan["status"] not in {"running", "stopping"}:
                        continue
                    try:
                        self._tick(plan)
                    except (ValueError, KeyError, TypeError, InvalidOperation, requests.RequestException) as exc:
                        plan.update(status="paused", message="策略暂停，请核对订单。"+str(exc)[:240])
                        self._save()

    @staticmethod
    def _public(url, params=None):
        try:
            response = requests.get(url, params=params, timeout=6, allow_redirects=False)
            if response.status_code != 200:
                raise ValueError("交易所行情暂不可用")
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ValueError("交易所行情暂不可用") from exc

    @staticmethod
    def _binance(market):
        return ("https://api.binance.com", "/api/v3") if market == "spot" else ("https://fapi.binance.com", "/fapi/v1")

    def _rules(self, exchange, market, symbol):
        if exchange == "coinw":
            data = self._public("https://api.coinw.com/api/v1/public", {"command":"returnSymbol"})
            pair = next((s for s in data.get("data", []) if s["currencyPair"] == symbol[:-4]+"_USDT"), None)
            if not pair or int(pair["state"]) != 1:
                raise ValueError("CoinW 该交易对当前不可下单")
            return {"tick":str(Decimal(1).scaleb(-int(pair["pricePrecision"]))),
                    "step":str(Decimal(1).scaleb(-int(pair["countPrecision"]))),
                    "min_qty":pair["minBuyCount"], "min_quote":pair["minBuyAmount"]}
        base, prefix = self._binance(market)
        data = self._public(base + prefix + "/exchangeInfo", {"symbol":symbol} if market=="spot" else None)
        pair = next((p for p in data.get("symbols", []) if p["symbol"] == symbol), None)
        if not pair or pair["status"] != "TRADING":
            raise ValueError("Binance 该交易对当前不可下单")
        filters = {f["filterType"]:f for f in pair["filters"]}
        lot = filters["LOT_SIZE"] if market=="spot" else filters.get("MARKET_LOT_SIZE", filters["LOT_SIZE"])
        minimum = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
        return {"tick":filters["PRICE_FILTER"]["tickSize"], "step":lot["stepSize"], "min_qty":lot["minQty"],
                "min_quote":minimum.get("minNotional", minimum.get("notional", "0"))}

    @staticmethod
    def _floor(value, unit):
        unit = Decimal(unit)
        if unit <= 0:
            raise ValueError("交易所数量精度无效")
        return (Decimal(value)/unit).to_integral_value(rounding=ROUND_DOWN)*unit

    def _price(self, exchange, market, symbol):
        if exchange == "coinw":
            data = self._public("https://api.coinw.com/api/v1/public", {"command":"returnTicker"})
            return Decimal(str(data["data"][symbol[:-4]+"_USDT"]["last"]))
        base, prefix = self._binance(market)
        key = "price" if market == "spot" else "markPrice"
        endpoint = "/ticker/price" if market == "spot" else "/premiumIndex"
        return Decimal(str(self._public(base+prefix+endpoint, {"symbol":symbol})[key]))

    def _symbol_config(self, connector, symbol):
        response = connector.signed("GET", "/fapi/v1/symbolConfig", {"symbol":symbol})
        item = next((v for v in response if v["symbol"] == symbol), None)
        cap = (self.execution.preflight.leverage(connector, "binance", symbol)["auto_max"]
               if self.execution.preflight else LivePreflight.AUTO_LEVERAGE)
        if not item or item["marginType"] != "ISOLATED" or not 1 <= int(item["leverage"]) <= cap:
            raise ValueError(f"实盘合约 AUTO 需要在 Binance 设置逐仓，当前杠杆上限 {cap}×")
        if connector.signed("GET", "/fapi/v1/positionSide/dual").get("dualSidePosition") is not True:
            raise ValueError("实盘合约 AUTO 需要在 Binance 启用双向持仓")
        return int(item["leverage"])

    @staticmethod
    def _position(connector, symbol):
        return [v for v in connector.signed("GET", "/fapi/v3/positionRisk", {"symbol":symbol})
                if v["symbol"]==symbol and Decimal(v["positionAmt"]) != 0]

    def _futures_existing(self, connector, symbol):
        params = {"symbol":symbol}
        return (self._position(connector, symbol)
                or connector.signed("GET", "/fapi/v1/openOrders", params)
                or connector.signed("GET", "/fapi/v1/openAlgoOrders", params))

    def _flatten_positions(self, connector, account):
        if any(p["account"] == account and p["exchange"] == "binance" and p["market"] == "futures"
               and (p["status"] in {"running", "stopping"} or p.get("pending") and p["status"] != "stopped")
               for p in self.plans.values()):
            raise ValueError("请先暂停该账户的合约 AUTO，并核对在途委托")
        for endpoint in ("/fapi/v1/openOrders", "/fapi/v1/openAlgoOrders"):
            orders = connector.signed("GET", endpoint)
            if not isinstance(orders, list) or orders:
                raise ValueError("存在普通或条件挂单，请先在 Binance 核对并撤销")
        positions = connector.signed("GET", "/fapi/v3/positionRisk")
        result = []
        for position in positions:
            qty = Decimal(position["positionAmt"])
            if not qty:
                continue
            symbol = position["symbol"]
            if not symbol.endswith("USDT"):
                raise ValueError("账户有非 USDT 合约仓位，请先在交易所处理")
            side = position["positionSide"]
            result.append({"symbol": symbol, "position": ("LONG" if qty > 0 else "SHORT") if side == "BOTH" else side,
                           "quantity": format(abs(qty), "f")})
        if not result:
            raise ValueError("该账户暂无可平仓位")
        return sorted(result, key=lambda row: (row["symbol"], row["position"]))

    def preview_flatten(self):
        with self.lock:
            connector, account = self.execution._context("binance", "futures")
            if self.execution.preflight:
                self.execution.preflight.ensure_trade(connector, "binance", "futures")
            positions = self._flatten_positions(connector, account)
            token = uuid4().hex
            self.flatten_confirmation = (token, time.monotonic() + 45, account, positions)
            return {"confirmation": token, "positions": positions}

    def flatten(self, token):
        with self.lock:
            saved = self.flatten_confirmation
            if not saved or saved[0] != token or saved[1] <= time.monotonic():
                raise ValueError("全平确认已过期，请重新预览")
            self.flatten_confirmation = None
            connector, account = self.execution._context("binance", "futures")
            if account != saved[2] or self._flatten_positions(connector, account) != saved[3]:
                raise ValueError("账户或仓位发生变化，请重新预览")
            rows, message = [], None
            for position in saved[3]:
                try:
                    row = self.execution.strategy_order(
                        {**position, "exchange":"binance", "market":"futures", "source":"manual",
                         "action":"CLOSE", "type":"MARKET"}, "bq" + uuid4().hex, account,
                    )
                except ValueError as exc:
                    message = str(exc)
                    break
                rows.append(row)
                if row["status"] != "FILLED" or Decimal(row.get("filled") or "0") != Decimal(position["quantity"]):
                    message = "有平仓未确认全部成交，请核对，剩余仓位未继续提交"
                    break
            return {"orders": rows, "remaining": len(saved[3]) - len(rows), "message": message}

    @staticmethod
    def _balance(connector, asset):
        result = connector.snapshot()
        if result.get("error"):
            raise ValueError(result["error"])
        return next((Decimal(str(b["free"])) for b in result.get("balances", []) if b["asset"]==asset), Decimal(0))

    def credentials_change(self, exchange, market, operation, *args):
        with self.lock:
            if any(p["exchange"] == exchange and p["market"] == market and p["status"] != "stopped"
                   for p in self.plans.values()):
                raise ValueError("请先停止该市场的实盘策略，再更换 API")
            return operation(*args)

    def submit_manual(self, confirmation):
        with self.lock:
            preview = self.execution.previews.get(confirmation)
            if preview:
                order = preview[2]
                if any(plan["status"] in {"running","stopping"} and all(
                    plan[k] == order[k] for k in ("exchange","market","symbol")
                ) for plan in self.plans.values()):
                    raise ValueError("当前交易对有实盘策略，请先暂停或停止再手动下单")
            return self.execution.submit(confirmation)

    def _check_overlap(self, plan):
        for other in self.plans.values():
            if other["id"] != plan["id"] and other["status"] != "stopped" and all(
                other[key] == plan[key] for key in ("exchange", "market", "symbol")
            ):
                raise ValueError("当前交易对已有实盘策略，请先停止或处理旧策略")

    def preview(self, options):
        with self.lock:
            exchange, market, symbol = (options[k] for k in ("exchange", "market", "symbol"))
            connector, account = self.execution._context(exchange, market)
            plan = dict(options, id=uuid4().hex, account=account, status="ready", pending=None, position=None)
            self._check_overlap(plan)
            rules = self._rules(exchange, market, symbol)
            price = self._price(exchange, market, symbol)
            amount = Decimal(plan["amount"])
            if plan["kind"] == "grid" and self.execution.preflight and self.execution.preflight.risk:
                self.execution.preflight.risk.check_grid(float(amount))
            if amount < Decimal(rules["min_quote"]) or price <= 0:
                raise ValueError("预算低于最小订单金额，或行情不可用")
            plan["rules"] = rules
            if plan["kind"] == "grid":
                initial_ratio = Decimal(str(plan.get("initial_ratio") or 0))
                if initial_ratio > 0:
                    if market != "spot":
                        raise ValueError("初始仓位仅支持现货网格")
                    initial_budget = amount * initial_ratio
                    if self.execution.preflight and self.execution.preflight.risk:
                        self.execution.preflight.risk.check(market="spot", notional=float(initial_budget))
                    initial_qty = self._floor(initial_budget / price, rules["step"])
                    if initial_qty < Decimal(rules["min_qty"]) or initial_qty * price < Decimal(rules["min_quote"]):
                        raise ValueError("初始仓位低于最小成交数量或金额")
                    plan["initial_budget"] = str(initial_budget)
                    plan["initial_qty"] = str(initial_qty)
                else:
                    plan["initial_budget"] = "0"
                    plan["initial_qty"] = "0"
                lower, upper = (Decimal(plan[k]) for k in ("lower","upper"))
                if not lower < price < upper:
                    raise ValueError("当前价需位于网格上下限内")
                if plan.get("distribution", "arithmetic") == "geometric":
                    ratio = (upper/lower) ** (Decimal(1) / plan["levels"])
                    lines = [self._floor(lower * (ratio ** i), rules["tick"])
                             for i in range(plan["levels"] + 1)]
                else:
                    spacing = (upper-lower)/plan["levels"]
                    lines = [self._floor(lower+spacing*i, rules["tick"])
                             for i in range(plan["levels"]+1)]
                pairs = [(a,b) for a,b in zip(lines, lines[1:]) if a < price]
                if not pairs or any(a <= 0 or b < a*Decimal("1.005") for a,b in pairs):
                    raise ValueError("请放宽区间或减少网格数，每格间距至少 0.5%")
                per = amount/len(pairs)/Decimal("1.01")
                cells = []
                for a,b in pairs:
                    qty = self._floor(per/a, rules["step"])
                    if qty < Decimal(rules["min_qty"]) or qty*a < Decimal(rules["min_quote"]):
                        raise ValueError("单格订单低于最小数量或金额，请提高投入或减少网格数")
                    cells.append({"buy":str(a),"sell":str(b),"qty":str(qty),"next_qty":str(qty),
                                  "held":"0","cycle":0,"side":"BUY","order":None})
                plan["cells"] = cells
            else:
                spec = self.models.get(plan["model_id"])
                if spec.scope != market or spec.kind != "rules" or spec.ai_mode != "off":
                    raise ValueError("实盘 AUTO 当前支持该市场的本地规则模型")
                plan["model"] = spec.model_dump(mode="json")
            if market == "spot":
                if self._balance(connector, "USDT") < amount:
                    raise ValueError("现货可用 USDT 不足")
            else:
                plan["leverage"] = self._symbol_config(connector, symbol)
                if self._futures_existing(connector, symbol):
                    raise ValueError("请先处理 Binance 当前交易对的既有仓位和挂单")
                account_data = connector.signed("GET", "/fapi/v3/account")
                if Decimal(account_data["availableBalance"]) < amount / plan["leverage"]:
                    raise ValueError("合约可用保证金不足")
            if plan["kind"] == "auto":
                if self.execution.preflight:
                    self.execution.preflight.ensure_trade(connector, exchange, market)
                self._risk_check(plan, connector, plan.get("leverage", 1))
            token = uuid4().hex
            now = time.monotonic()
            self.confirmations = {k:v for k,v in self.confirmations.items() if v[0]>now}
            if len(self.confirmations) >= 20:
                raise ValueError("待确认的策略太多，请稍后重试")
            self.confirmations[token] = (now+60, plan)
            return {"confirmation":token, "plan":self._public_plan(plan)}

    @staticmethod
    def _public_plan(plan):
        return {**{k:v for k,v in plan.items() if k not in {"account","model","rules","pending"}},
                "pending_order_id":(plan.get("pending") or {}).get("id")}

    def status(self, exchange, market, symbol, kind):
        with self.lock:
            try:
                _, account = self.execution._context(exchange, market)
            except ValueError:
                return {"plan":None}
            candidates = [p for p in self.plans.values() if (p["exchange"],p["market"],p["symbol"],p["kind"],p["account"])
                          == (exchange,market,symbol,kind,account)]
            return {"plan":self._public_plan(candidates[-1]) if candidates else None}

    def _seed_initial(self, plan):
        budget = Decimal(str(plan.get("initial_budget") or 0))
        if budget <= 0:
            return
        intent = {"exchange": plan["exchange"], "market": "spot", "symbol": plan["symbol"],
                  "side": "BUY", "type": "MARKET", "quote": str(budget)}
        preview = self.execution.preview(intent)
        row = self.execution.submit(preview["confirmation"])
        if row.get("status") != "FILLED":
            raise ValueError("初始仓位未完全成交，网格未启动；请在交易所核对订单")
        qty, _quote = self.execution.spot_fill_totals(row, plan["account"])
        qty = self._floor(qty, plan["rules"]["step"])
        price = self._price(plan["exchange"], plan["market"], plan["symbol"])
        cells = plan.get("cells") or []
        target = next((Decimal(cell["sell"]) for cell in cells if Decimal(cell["sell"]) > price), None)
        rebuy = next((Decimal(cell["buy"]) for cell in cells if Decimal(cell["buy"]) < price), None)
        if target is None or rebuy is None:
            raise ValueError("初始仓位已买入，但找不到可用的卖出/回买网格；请人工核对")
        cells.append({"buy": str(rebuy), "sell": str(target), "qty": str(qty),
                      "next_qty": str(qty), "held": str(qty), "cycle": 0,
                      "side": "SELL", "order": None, "initial": True})
        plan["cells"] = cells
        plan["message"] = f"初始仓位已成交 {qty}，等待网格卖出"

    def start(self, token, initial_confirm=None):
        with self.lock:
            item = self.confirmations.pop(token, None)
            if not item or item[0] < time.monotonic():
                raise ValueError("策略确认已过期，请重新预览")
            plan = item[1]
            self._check_overlap(plan)
            if self.execution._context(plan["exchange"], plan["market"])[1] != plan["account"]:
                raise ValueError("API 账户已变更")
            if plan["kind"] == "grid" and self.execution.preflight and self.execution.preflight.risk:
                self.execution.preflight.risk.check_grid(float(plan["amount"]))
            if Decimal(str(plan.get("initial_budget") or 0)) > 0:
                if initial_confirm != "BUY":
                    raise ValueError("初始仓位需要再次输入 BUY 确认")
                self._seed_initial(plan)
            plan.update(status="running", message="策略已启用，等待首次检查", checked_at=None)
            self.plans[plan["id"]] = plan
            self._save()
            self.start_worker()
            return self._public_plan(plan)

    def control(self, plan_id, action):
        with self.lock:
            plan = self.plans.get(plan_id)
            if not plan or self.execution._context(plan["exchange"], plan["market"])[1] != plan["account"]:
                raise ValueError("找不到当前账户的实盘策略")
            if action == "stop":
                if plan["kind"] == "grid":
                    plan.update(status="stopping", message="正在核对和撤销本网格的挂单")
                elif plan["status"] == "running" and (
                    plan.get("pending") or plan["market"] == "futures" and plan.get("position", {})
                    and plan["position"].get("protection_state") != "active"
                ):
                    plan.update(pause_requested=True, message="已停止新增信号。当前订单与保护委托核对后暂停。")
                elif plan.get("pending") or plan.get("position"):
                    plan.update(status="paused", message="暂停新单。现有资产与交易所保护委托保留。")
                else:
                    plan.update(status="stopped", message="AUTO 已停止")
            elif action == "retire":
                if plan["status"] != "paused" or plan["kind"] != "auto":
                    raise ValueError("只可结束已暂停的 AUTO 跟踪")
                if plan.get("pending"):
                    record = self.execution._lookup(plan["pending"]["id"])
                    if record and record[1]["status"] not in LIVE_FINAL:
                        raise ValueError("请先查清待确认的实盘订单")
                plan.update(status="stopped", message="已结束 AUTO 跟踪，交易所资产和保护委托保留")
            elif plan["status"] == "paused":
                self._check_overlap(plan)
                plan.update(status="running", message="正在核对策略订单", pause_requested=False)
            else:
                raise ValueError("只能继续已暂停的策略")
            self._save()
            self.start_worker()
            return self._public_plan(plan)

    def _intent(self, plan, **specific):
        return {**{k:plan[k] for k in ("exchange","market","symbol")}, "source":plan["kind"], **specific}

    def _strategy_order(self, plan, intent, order_id):
        prior = self.execution._lookup(order_id)
        row = self.execution.strategy_order(intent, order_id, plan["account"])
        if prior and row["status"] not in LIVE_FINAL:
            row = self.execution.manage(order_id, "query")
        failed = {"REJECTED","CANCELED","EXPIRED","ACKNOWLEDGED"}
        partial_future = intent["market"] == "futures" and Decimal(row.get("filled") or "0") > 0
        if row["status"] in failed and not (row["status"] in {"CANCELED","EXPIRED"} and partial_future):
            raise ValueError(row.get("message") or "策略委托被拒绝或已终止")
        return row

    def _tick(self, plan):
        connector, account = self.execution._context(plan["exchange"], plan["market"])
        if account != plan["account"]:
            raise ValueError("策略的 API 账户已变更")
        with self.execution.lock, connector.lock:
            if self.execution._context(plan["exchange"], plan["market"])[1] != account:
                raise ValueError("策略的 API 账户已变更")
            if plan["status"] == "stopping":
                self._stop_grid(plan)
            elif plan["kind"] == "grid":
                self._grid_tick(plan, connector)
            else:
                self._auto_tick(plan, connector)
        plan["checked_at"] = _now()
        self._save()

    def _stop_grid(self, plan):
        for cell in plan["cells"]:
            order_id = cell.get("order")
            if not order_id:
                continue
            record = self.execution._lookup(order_id)
            row = record[1] if record else None
            if row and row["status"] not in LIVE_FINAL:
                row = self.execution.manage(order_id, "query")
                if row["status"] in {"NEW","PARTIALLY_FILLED","ACCEPTED"}:
                    self.execution.manage(order_id, "cancel")
                    return
                if row["status"] not in LIVE_FINAL:
                    raise ValueError("有网格订单状态未确认，暂不能完成停止")
            if row and row.get("filled") not in (None, "0"):
                cell["stop_fill"] = row["filled"]
            cell["order"] = None
        plan.update(status="stopped", message="本网格挂单已核对，已成交资产留在现货账户")

    def _grid_tick(self, plan, connector):
        stop_pct = Decimal(str(plan.get("stop_loss_pct") or 0))
        if stop_pct > 0:
            price = self._price(plan["exchange"], plan["market"], plan["symbol"])
            trigger = Decimal(str(plan["lower"])) * (Decimal(1) - stop_pct / Decimal(100))
            if price <= trigger:
                plan["message"] = f"价格跌破止损线 {trigger:.2f}，停止网格并撤销本网格挂单"
                self._stop_grid(plan)
                return
        cells = plan["cells"]
        idx = next((i for i,c in enumerate(cells) if not c["order"]), None)
        if idx is None:
            idx = plan.get("cursor", 0) % len(cells)
        plan["cursor"] = (idx+1) % len(cells)
        cell = cells[idx]
        side = cell["side"]
        quantity = cell["held"] if side=="SELL" else cell["next_qty"]
        price = cell["sell"] if side=="SELL" else cell["buy"]
        rules = plan["rules"]
        if Decimal(quantity) < Decimal(rules["min_qty"]) or Decimal(quantity)*Decimal(price) < Decimal(rules["min_quote"]):
            raise ValueError("可补单数量低于最小委托要求")
        if not cell["order"]:
            if side == "SELL" and self._balance(connector, plan["symbol"][:-4]) < Decimal(quantity):
                raise ValueError("策略可用库存已变化")
            digest = hashlib.sha256(f"{plan['id']}:{idx}:{cell['cycle']}:{side}".encode()).hexdigest()
            cell["order"] = "bq"+digest[:32]
            self._save()
        intent = self._intent(plan, type="LIMIT", side=side, price=price, quantity=quantity)
        row = self._strategy_order(plan, intent, cell["order"])
        if row["status"] == "FILLED":
            qty, quote = self.execution.spot_fill_totals(row, plan["account"])
            if side == "BUY":
                cell.update(side="SELL", held=str(self._floor(qty, rules["step"])), order=None)
            else:
                reusable = self._floor(quote/Decimal("1.01")/Decimal(cell["buy"]), rules["step"])
                cell.update(side="BUY", next_qty=str(min(Decimal(cell["qty"]), reusable)),
                            held="0", cycle=cell["cycle"]+1, order=None)
        plan["message"] = "按交易所实际成交补挂下一格"

    def _auto_schedule(self, plan, **specific):
        plan["pending"] = {"id":"bq"+uuid4().hex, "intent":self._intent(plan, type="MARKET", **specific)}
        self._save()

    def _settle_auto(self, plan, connector):
        pending = plan["pending"]
        intent = pending["intent"]
        opening = intent.get("action") == "OPEN" or intent.get("side") == "BUY"
        if opening and self.execution._lookup(pending["id"]) is None:
            if plan.get("pause_requested"):
                plan["pending"] = None
                self._save()
                return
            self._risk_check(plan, connector, plan.get("leverage", 1))
            if intent.get("action") == "OPEN" and (
                self._symbol_config(connector, plan["symbol"]) != plan["leverage"] or self._futures_existing(connector, plan["symbol"])
            ):
                raise ValueError("已批准的合约杠杆、仓位或委托发生变化")
        row = self._strategy_order(plan, intent, pending["id"])
        if row["status"] not in LIVE_FINAL:
            plan["message"] = "等待交易所确认实盘成交"
            return
        if plan["market"] == "spot":
            if intent["side"] == "BUY":
                qty, spent = self.execution.spot_fill_totals(row, plan["account"])
                entry = spent / qty
                params = plan["model"]["parameters"]
                atr = Decimal(plan["entry_atr"])
                plan["position"] = {"qty":str(qty),"spent":str(spent),"side":"LONG",
                                    "entry_price":float(entry), "trailing_enabled":params["trailing_enabled"],
                                    "trail_activation_pct":float(atr*Decimal(str(params["atr_stop"]))*Decimal(str(params["activation_r"]))/entry),
                                    "trail_distance_pct":float(atr*Decimal(str(params["trail_atr"]))/entry)}
            else:
                if self.execution.preflight:
                    _, received = self.execution.spot_fill_totals(row, plan["account"])
                    now = time.time()
                    history = [item for item in plan.get("realized", []) if now - item["time"] < 172800]
                    history.append({"time": now, "pnl": str(received - Decimal(plan["position"]["spent"]))})
                    plan["realized"] = history
                plan["position"] = None
        elif intent["action"] == "OPEN":
            qty, entry = Decimal(row.get("filled") or "0"), Decimal(str(row.get("average_price") or "0"))
            if qty <= 0 or entry <= 0:
                raise ValueError("缺少合约开仓成交量或均价，请检查仓位")
            distance = Decimal(plan["distance"])
            stop = entry-distance if intent["position"]=="LONG" else entry+distance
            stop = self._floor(stop, plan["rules"]["tick"])
            if stop <= 0:
                raise ValueError("初始保护价格无效，请检查仓位")
            plan["position"] = {"qty":str(qty),"side":intent["position"],"stop":str(stop),
                                "protection":"sl"+row["id"][2:],"protection_state":"ready"}
        else:
            if row["status"] != "FILLED" or self._position(connector, plan["symbol"]):
                raise ValueError("平仓未全部完成，请核对剩余仓位和保护委托")
            self._clear_protection(plan, connector)
            plan["position"] = None
        plan["pending"] = None
        plan["message"] = "已确认实盘成交"
        self._save()

    def _clear_protection(self, plan, connector):
        position = plan["position"]
        if position and position["protection_state"] != "ready":
            args = {"clientAlgoId":position["protection"]}
            state = connector.signed("GET", "/fapi/v1/algoOrder", args)
            if state.get("algoStatus") == "NEW":
                connector.signed("DELETE", "/fapi/v1/algoOrder", args)

    def _protect_futures(self, plan, connector):
        position = plan["position"]
        rows = self._position(connector, plan["symbol"])
        if not rows:
            self._clear_protection(plan, connector)
            plan["position"] = None
            self._save()
            return False
        qty = Decimal(position["qty"])
        if len(rows)!=1 or rows[0]["positionSide"]!=position["side"] or abs(Decimal(rows[0]["positionAmt"]))!=qty:
            raise ValueError("交易所仓位与 AUTO 记录不一致，请手动核对")
        args = {"clientAlgoId":position["protection"]}
        state = position["protection_state"]
        if state == "ready":
            mark = self._price("binance","futures",plan["symbol"])
            long = position["side"] == "LONG"
            if (long and mark <= Decimal(position["stop"])) or (not long and mark >= Decimal(position["stop"])):
                self._auto_schedule(plan, action="CLOSE", position=position["side"], quantity=str(qty))
                return False
            position["protection_state"] = "pending"
            self._save()
            try:
                raw = connector.signed("POST", "/fapi/v1/algoOrder", {
                    **args, "algoType":"CONDITIONAL", "symbol":plan["symbol"], "positionSide":position["side"],
                    "side":"SELL" if long else "BUY", "type":"STOP_MARKET", "workingType":"MARK_PRICE",
                    "quantity":str(qty), "triggerPrice":position["stop"],
                })
            except ValueError as exc:
                raise ValueError("交易所止损尚未确认，立即检查真实仓位") from exc
        else:
            raw = connector.signed("GET", "/fapi/v1/algoOrder", args)
        if raw.get("algoStatus") != "NEW" or raw.get("clientAlgoId") != position["protection"]:
            raise ValueError("合约交易所止损状态需要核对")
        position["protection_state"] = "active"
        self._save()
        return True

    def _exit_spot(self, plan, connector):
        quantity = self._floor(Decimal(plan["position"]["qty"]), plan["rules"]["step"])
        if quantity < Decimal(plan["rules"]["min_qty"]) or self._balance(connector, plan["symbol"][:-4]) < quantity:
            raise ValueError("策略仓位已变化或小于交易所最小单位")
        self._auto_schedule(plan, side="SELL", quantity=str(quantity))

    def _auto_tick(self, plan, connector):
        if plan.get("pending"):
            self._settle_auto(plan, connector)
            return
        futures = plan["market"] == "futures"
        position = plan.get("position")
        if futures and position and not self._protect_futures(plan, connector):
            return
        if plan.pop("pause_requested", False):
            plan.update(status="paused" if position else "stopped", message="AUTO 已暂停新单")
            return
        if not futures and position and position.get("trailing_enabled"):
            price = float(self._price("binance","spot",plan["symbol"]))
            update_trailing(position, price, .001, .0002)
            if position.get("trailing_active") and price <= position["trailing_stop"]:
                self._exit_spot(plan, connector)
                return
        now = time.time()
        if now - plan.get("last_check",0) < 15:
            return
        plan["last_check"] = now
        interval, symbol = plan["interval"], plan["symbol"]
        base, prefix = self._binance(plan["market"])
        raw = self._public(base+prefix+"/klines", {"symbol":symbol,"interval":interval,"limit":400})
        frame = _clean_klines(_rows_to_frame(raw), interval)
        frame.attrs["symbol"] = symbol
        guard = data_guard(frame, interval)
        if guard:
            plan["message"] = guard
            return
        spec = self.models.get(plan["model_id"])
        if spec.model_dump(mode="json") != plan["model"]:
            raise ValueError("模型版本已变化，请重新预览")
        signal = self.models.evaluate_nonblocking(frame, spec.id, interval, plan["market"], cost=.0024).iloc[-1]
        bar = str(signal.open_time)
        if bar == plan.get("last_bar"):
            return
        plan["last_bar"] = bar
        plan["message"] = "已检查收盘信号，等待下一根"
        if position:
            short = position["side"] == "SHORT"
            if bool(signal["short_exit_signal" if short else "exit_signal"]):
                if futures:
                    self._auto_schedule(plan, action="CLOSE", position=position["side"], quantity=position["qty"])
                else:
                    self._exit_spot(plan, connector)
            return
        side = "LONG" if bool(signal["entry_signal"]) else "SHORT" if futures and bool(signal["short_entry_signal"]) else None
        if not side:
            return
        amount = Decimal(plan["amount"])
        if futures:
            leverage = self._symbol_config(connector, symbol)
            if leverage != plan["leverage"] or self._futures_existing(connector,symbol):
                raise ValueError("杠杆、仓位或委托已变化，请重新检查")
            mark = self._price("binance","futures",symbol)
            wallet = connector.signed("GET","/fapi/v3/account")
            available = Decimal(wallet["availableBalance"])
            distance = Decimal(str(signal.atr14))*Decimal(str(spec.parameters.atr_stop))
            qty = self._floor(amount/mark, plan["rules"]["step"])
            estimated_loss = qty*(distance+mark*Decimal(".0024"))
            if (distance <= 0 or distance/mark >= Decimal(1)/leverage-Decimal(".01")
                or estimated_loss > Decimal(wallet["totalWalletBalance"])*Decimal(str(spec.risk_fraction))
                or qty < Decimal(plan["rules"]["min_qty"]) or qty*mark < Decimal(plan["rules"]["min_quote"])
                or amount/leverage > available):
                plan["message"] = "自定义名义价值未通过最小委托、保证金或止损风险检查"
                return
            plan["distance"] = str(distance)
            self._auto_schedule(plan, action="OPEN", position=side, quantity=str(qty))
        elif self._balance(connector, "USDT") >= amount:
            atr = Decimal(str(signal.atr14))
            if not atr.is_finite() or atr <= 0:
                raise ValueError("现货 ATR 无效，已跳过买入")
            plan["entry_atr"] = str(atr)
            self._auto_schedule(plan, side="BUY", quote=str(amount))
        else:
            plan["message"] = "自定义买入金额超过现货可用 USDT"
