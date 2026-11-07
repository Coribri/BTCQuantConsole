"""Local USDT-perpetual paper trading simulator.

There is intentionally no Binance Futures API call here.  Hong Kong accounts may
not have futures permissions, so this page uses the public spot mark price and
simulates leverage, shorts, margin, fees and liquidation locally.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

import pandas as pd

from .config import AppConfig
from .data import fetch_recent_klines, fetch_ticker
from .strategies import generate_signals, strategy_snapshot
from .quant_risk import data_guard, update_trailing
from .data import INTERVAL_MS
import math
from .models import ModelRegistry


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class FuturesPaperService:
    MAX_LEVERAGE = 100
    FEE_RATE = 0.0005
    SLIPPAGE_BPS = 2.0
    MMR = 0.005

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.symbol = cfg.market.symbol.upper()
        self.base_asset = self.symbol.removesuffix(cfg.market.quote)
        suffix = "" if self.symbol == "BTCUSDT" else f"_{self.symbol.lower()}"
        self.state_file = cfg.root / "runtime" / f"futures_paper_state{suffix}.json"
        self.lock = threading.RLock()
        self.running = False
        self.thread: threading.Thread | None = None
        self.last_error: str | None = None
        self.model_snapshot: dict[str, Any] = {}
        self.models = ModelRegistry(cfg.root,cfg.web.port)
        self._last_model_key = None
        self._last_auto_check = 0.0
        self.state = self._load()
        self._events: list[dict[str, Any]] = list(self.state.get("events", []))[-100:]
        self.events = self._events

    # ------------------------------------------------------------------ state
    def _new_state(self) -> dict[str, Any]:
        now = _now()
        return {
            "mode": "paper_futures",
            "created_at": now,
            "updated_at": now,
            "initial_equity": 10_000.0,
            "wallet": 10_000.0,
            "realized_pnl": 0.0,
            "fees": 0.0,
            "positions": [],
            "pending": [],
            "orders": [],
            "events": [],
            "last_price": None,
            "auto_enabled": False,
            "auto_interval": "1h",
            "auto_leverage": 5,
            "auto_last_bar": None,
            "auto_risk_fraction": 0.005,
            "auto_cooldown_until": 0,
            "auto_model_version": "Trend V2",
            "auto_model_id": "trend-v2",
        }

    def _load(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return self._new_state()
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"corrupt futures state: {self.state_file}") from exc
        base = self._new_state()
        for key, value in base.items():
            state.setdefault(key, value)
        if not state.get("positions"):
            old = state.get("position")
            if isinstance(old, dict):
                state["positions"] = [{**old, "id": old.get("id") or f"P{int(time.time()*1000)}"}]
        state.pop("position", None)
        return state

    def save(self) -> None:
        self.state["updated_at"] = _now()
        _atomic(self.state_file, self.state)

    def _positions(self) -> list[dict[str, Any]]:
        return self.state.setdefault("positions", [])

    def _position_by_id(self, position_id: str | None) -> dict[str, Any] | None:
        positions = self._positions()
        if position_id:
            return next((p for p in positions if p.get("id") == position_id), None)
        return positions[0] if positions else None

    def _event(self, level: str, message: str) -> None:
        item = {"time": _now(), "level": level, "message": message}
        self.state.setdefault("events", []).append(item)
        self.state["events"] = self.state["events"][-100:]
        self._events = list(self.state["events"])

    # --------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="btcquant-futures-paper", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self.models.close()

    def _loop(self) -> None:
        while self.running:
            try:
                self.poll()
            except Exception as exc:  # keep the simulator alive
                self.last_error = str(exc)
                self._event("error", f"futures poll: {exc}")
            time.sleep(2)

    # ------------------------------------------------------------------ market
    def price(self) -> float:
        return float(fetch_ticker(self.cfg.data, self.cfg.market.symbol)["price"])

    def candles(self, interval: str = "1h", limit: int = 240) -> pd.DataFrame:
        return fetch_recent_klines(self.cfg.data, self.cfg.market, limit=limit, interval=interval)

    # ------------------------------------------------------------------- poll
    def poll(self) -> None:
        with self.lock:
            try:
                price = self.price()
            except Exception as exc:
                self.last_error = str(exc)
                return
            self.state["last_price"] = price
            self._check_pending(price)
            self._check_tp_sl(price)
            self._check_liquidation(price)
            self._check_auto(price)
            self.save()

    def set_auto(self, enabled: bool, interval: str = "1h", leverage: int = 5, risk_fraction: float = 0.005, model_id: str | None=None) -> dict[str, Any]:
        with self.lock:
            if interval not in {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d"}:
                raise ValueError("合约 AUTO 支持 1M–1D")
            leverage = max(1, min(10, int(leverage)))
            if not math.isfinite(risk_fraction) or not .001 <= risk_fraction <= .02:
                raise ValueError("单笔风险范围为 0.1%–2%")
            if self.state.get("auto_enabled") and interval != self.state.get("auto_interval"):
                raise ValueError("请先关闭 AUTO，再修改执行周期")
            if model_id:
                spec = self.models.get(model_id)
                if spec.scope != "futures":
                    raise ValueError("合约 AUTO 只能选择合约模型")
                if self.state.get("auto_enabled") and model_id!=self.state.get("auto_model_id"):
                    raise ValueError("请先关闭 AUTO，再切换模型")
                self.state["auto_model_id"]=model_id
            self.state["auto_enabled"] = bool(enabled)
            self.state["auto_interval"] = interval
            self.state["auto_leverage"] = leverage
            self.state["auto_risk_fraction"] = min(risk_fraction,self.models.get(self.state.get("auto_model_id","trend-v2")).risk_fraction)
            self._last_auto_check = 0
            self._event("info", f"futures AUTO {'ON' if enabled else 'OFF'} · {interval} · {leverage}x")
            self.save()
            return self.status()

    def _check_auto(self, price: float) -> None:
        if not self.state.get("auto_enabled") and not self.running:
            return
        now = time.time()
        if now - self._last_auto_check < 20:
            return
        self._last_auto_check = now
        interval = self.state.get("auto_interval", "15m")
        try:
            frame = self.candles(interval, 400)
            model_id=self.state.get("auto_model_id","trend-v2")
            params=self.models.get(model_id).parameters.model_dump()
            frame.attrs["symbol"]=self.symbol
            bar_key=str(frame.iloc[-1].open_time)
            signals = self.models.evaluate_nonblocking(frame,model_id,interval,"futures",
                allow_remote=bool(self.state.get("auto_enabled")) and bar_key!=self.state.get("auto_last_bar"),
                cost=2*(self.FEE_RATE+self.SLIPPAGE_BPS/10000))
            if signals.empty:
                return
            last = signals.iloc[-1]
            bar = str(last.get("open_time"))
            guard = data_guard(frame,interval) or str(last.get("model_error") or "") or None
            self.model_snapshot = {"interval":interval,"signal":strategy_snapshot(signals,"trend_v2"),"guard":guard,"checked_at":_now()}
            self.last_error = None
            if not self.state.get("auto_enabled") or guard:
                return
            if bar == self.state.get("auto_last_bar"):
                return
            fast = float(last.get("ema_fast"))
            slow = float(last.get("ema_slow"))
            regime = float(last.get("ema_regime"))
            close = float(last.get("close"))
            adx = float(last.get("adx14") or 0.0)
            long_entry = bool(last.get("entry_signal"))
            short_entry = bool(last.get("short_entry_signal"))
            positions = self._positions()
            exited = False
            for pos in list(positions):
                if pos.get("reason") != "auto":
                    continue
                should_exit = bool(last.get("exit_signal")) if pos["side"]=="LONG" else bool(last.get("short_exit_signal"))
                if should_exit:
                    self._close_at(price, "auto_exit", apply_slippage=True, position_id=pos.get("id"))
                    self._event("trade", f"AUTO EXIT {interval} · {pos['side']}")
                    exited = True
            positions = self._positions()
            if positions and any(p.get('reason')!='auto' for p in positions):
                self.model_snapshot['guard']='已有手动仓位，AUTO 不接管；请在持仓列表中单独管理'
            if not positions and not exited:
                if now < float(self.state.get("auto_cooldown_until",0)):
                    self.model_snapshot["guard"] = "退出后冷却一根周期，暂不重新开仓"
                else:
                    leverage = min(10,int(self.state.get("auto_leverage",5)))
                    wallet = max(0.,float(self.state["wallet"]))
                    distance = float(last.get("atr14",0))*float(last.get("stop_atr",2.5))
                    cost = price*2*(self.FEE_RATE+self.SLIPPAGE_BPS/10000)
                    if not math.isfinite(distance) or distance<=0 or distance>=price*.3:
                        raise ValueError("ATR 风险距离无效，拒绝新开仓")
                    if distance/price >= 1/leverage-self.MMR-2*(self.FEE_RATE+self.SLIPPAGE_BPS/10000):
                        self.model_snapshot['guard']='ATR 止损超出逐仓强平缓冲，请降低 AUTO 杠杆'
                        return
                    budget = wallet*float(self.state.get("auto_risk_fraction",.005))
                    qty = min(budget/(distance+cost),wallet*.25*leverage/price,wallet/price)
                    side = "LONG" if long_entry else "SHORT" if short_entry else None
                    if side and qty*price>=5:
                        stop = price-distance if side=="LONG" else price+distance
                        self._open_position(side,qty,leverage,price,"auto",stop_loss=stop)
                        p = self._positions()[-1]
                        actual_distance=abs(p["entry_price"]-stop)
                        p.update(trailing_enabled=bool(params.get("trailing_enabled",True)),trail_activation_pct=actual_distance/p["entry_price"]*float(params.get("activation_r",1)),
                                 trail_distance_pct=float(params.get("trail_atr",2))*float(last.atr14)/p["entry_price"],initial_risk_price=actual_distance,
                                 trailing_best=p["entry_price"],trailing_active=False,trailing_stop=None,
                                 model_version="Trend V2")
                        self._event("trade",f"AUTO {side} {interval} · {qty:.6f} @ {price:,.2f} · SL {stop:,.2f} · risk {budget:.2f}")
            self.state["auto_last_bar"] = bar
            self.save()
        except Exception as exc:
            self.last_error = str(exc)
            self._event("warning", f"futures model: {exc}")

    def _check_pending(self, price: float) -> None:
        remaining = []
        for order in list(self.state.get("pending", [])):
            side = order["side"]
            limit = float(order["limit_price"])
            action = order.get("action", "OPEN")
            if action == "CLOSE":
                # A long is closed with a sell limit (price >= limit); a short
                # is closed with a buy limit (price <= limit).
                hit = (side == "LONG" and price >= limit) or (side == "SHORT" and price <= limit)
                if not hit:
                    remaining.append(order)
                    continue
                try:
                    self._close_at(
                        limit, "limit_close", apply_slippage=False,
                        position_id=order.get("position_id"),
                    )
                    self._event("trade", f"LIMIT CLOSE {side} filled at {limit:,.2f}")
                except Exception as exc:
                    self._event("warning", f"limit close cancelled: {exc}")
                continue
            hit = (side == "LONG" and price <= limit) or (side == "SHORT" and price >= limit)
            if not hit:
                remaining.append(order)
                continue
            try:
                self._open_position(
                    side, float(order["qty"]), int(order["leverage"]), limit, "limit",
                    take_profit=order.get("take_profit"), stop_loss=order.get("stop_loss"),
                    margin_mode=order.get("margin_mode", "ISOLATED"),
                )
                self._event("trade", f"LIMIT {side} filled at {limit:,.2f}")
            except Exception as exc:
                self._event("warning", f"limit {side} cancelled: {exc}")
        self.state["pending"] = remaining

    def _check_tp_sl(self, price: float) -> None:
        for pos in list(self._positions()):
            side = pos["side"]
            pid = pos.get("id")
            tp = pos.get("take_profit")
            sl = pos.get("stop_loss")
            update_trailing(pos,price,self.FEE_RATE,self.SLIPPAGE_BPS/10000)
            trailing = pos.get("trailing_stop") if pos.get("trailing_enabled") else None
            if trailing:
                sl = (max(float(sl),float(trailing)) if side=="LONG" else min(float(sl),float(trailing))) if sl else float(trailing)
            if tp:
                tp = float(tp)
                hit = (side == "LONG" and price >= tp) or (side == "SHORT" and price <= tp)
                if hit:
                    self._close_at(tp, "take_profit", apply_slippage=False, position_id=pid)
                    self._event("trade", f"TAKE PROFIT {side} at {tp:,.2f}")
                    continue
            if sl:
                sl = float(sl)
                hit = (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl)
                if hit:
                    # Stop is a market exit; use the current price (gap included)
                    # plus simulated slippage rather than the ideal stop level.
                    reason = "trailing_profit" if trailing and float(sl)==float(trailing) else "stop_loss"
                    self._close_at(price, reason, apply_slippage=True, position_id=pid)
                    self._event("warning", f"STOP LOSS {side} at {price:,.2f}")

    def _check_liquidation(self, price: float) -> None:
        for pos in list(self._positions()):
            liq = float(pos["liq_price"])
            if (pos["side"] == "LONG" and price <= liq) or (pos["side"] == "SHORT" and price >= liq):
                margin = float(pos["margin"])
                self.state["wallet"] = float(self.state["wallet"]) - margin
                self.state["realized_pnl"] = float(self.state["realized_pnl"]) - margin
                self._append_order(
                    {
                        "time": _now(),
                        "action": "LIQUIDATION",
                        "side": pos["side"],
                        "price": liq,
                        "qty": pos["qty"],
                        "pnl": -margin,
                        "fee": 0.0,
                    }
                )
                self._event("error", f"LIQUIDATED {pos['side']} at {liq:,.2f}; margin lost {margin:,.2f}")
                self.state["positions"] = [
                    p for p in self._positions() if p.get("id") != pos.get("id")
                ]
                self.state["pending"] = [
                    o for o in self.state.get("pending", []) if o.get("position_id") != pos.get("id")
                ]

    # ------------------------------------------------------------------ orders
    def place_order(
        self,
        *,
        side: str,
        action: str = "OPEN",
        order_type: str = "MARKET",
        qty: float,
        leverage: int = 10,
        limit_price: float | None = None,
        take_profit: float | None = None,
        stop_loss: float | None = None,
        margin_mode: str = "ISOLATED",
        position_id: str | None = None,
    ) -> dict[str, Any]:
        side = side.upper()
        margin_mode = "CROSS" if str(margin_mode).upper() == "CROSS" else "ISOLATED"
        action = action.upper()
        order_type = order_type.upper()
        if side not in {"LONG", "SHORT"}:
            raise ValueError("side must be LONG or SHORT")
        if action not in {"OPEN", "CLOSE"}:
            raise ValueError("action must be OPEN or CLOSE")
        if order_type not in {"MARKET", "LIMIT"}:
            raise ValueError("order_type must be MARKET or LIMIT")
        pos = self._position_by_id(position_id)
        if action == "CLOSE":
            if not pos:
                raise ValueError("no open position")
            if order_type == "MARKET":
                return self.close_position(reason="manual", position_id=position_id)
            if limit_price is None or float(limit_price) <= 0:
                raise ValueError("limit_price is required for LIMIT close")
            # Replace any previous close-limit order for this position.
            self.state["pending"] = [
                o for o in self.state.get("pending", [])
                if not (o.get("action") == "CLOSE" and o.get("position_id") == pos.get("id"))
            ]
            order = {
                "id": f"C{int(time.time() * 1000)}",
                "time": _now(),
                "action": "CLOSE",
                "side": pos["side"],
                "qty": float(pos["qty"]),
                "leverage": int(pos["leverage"]),
                "limit_price": float(limit_price),
                "position_id": pos.get("id"),
                "status": "PENDING",
            }
            self.state.setdefault("pending", []).append(order)
            self._event("info", f"LIMIT CLOSE {pos['side']} @ {float(limit_price):,.2f} placed")
            self.save()
            return order
        leverage = int(leverage)
        if not 1 <= leverage <= self.MAX_LEVERAGE:
            raise ValueError("leverage must be between 1 and 100")
        qty = float(qty)
        if qty <= 0:
            raise ValueError("qty must be positive")
        if take_profit is not None:
            take_profit = float(take_profit)
            if take_profit <= 0:
                take_profit = None
        if stop_loss is not None:
            stop_loss = float(stop_loss)
            if stop_loss <= 0:
                stop_loss = None
        if order_type == "LIMIT":
            if limit_price is None or float(limit_price) <= 0:
                raise ValueError("limit_price is required for LIMIT")
            order = {
                "id": f"L{int(time.time() * 1000)}",
                "time": _now(),
                "action": "OPEN",
                "side": side,
                "qty": qty,
                "leverage": leverage,
                "limit_price": float(limit_price),
                "take_profit": take_profit,
                "stop_loss": stop_loss,
                "margin_mode": margin_mode,
                "status": "PENDING",
            }
            self.state.setdefault("pending", []).append(order)
            self._event("info", f"LIMIT {side} {qty:.6f} {self.base_asset} @ {float(limit_price):,.2f} placed")
            self.save()
            return order
        price = self.price()
        self._open_position(
            side, qty, leverage, price, "market", take_profit=take_profit,
            stop_loss=stop_loss, margin_mode=margin_mode,
        )
        self.save()
        return self.state["orders"][-1]

    def _open_position(
        self,
        side: str,
        qty: float,
        leverage: int,
        price: float,
        reason: str,
        *,
        take_profit: float | None = None,
        stop_loss: float | None = None,
        margin_mode: str = "ISOLATED",
    ) -> None:
        margin_mode = "CROSS" if str(margin_mode).upper() == "CROSS" else "ISOLATED"
        positions = self._positions()
        old = next((p for p in positions if p["side"] == side), None)
        if old and old.get("margin_mode", "ISOLATED") != margin_mode:
            raise ValueError("close the existing position before changing margin mode")
        if not old and len(positions) >= 2:
            raise ValueError("long and short positions already exist")
        slip = self.SLIPPAGE_BPS / 10_000.0
        fill = price * (1.0 + slip if side == "LONG" else 1.0 - slip)
        notional = qty * fill
        if notional < 5:
            raise ValueError("notional must be >= 5 USDT")
        required_margin = notional / leverage
        fee = notional * self.FEE_RATE
        wallet = float(self.state["wallet"])
        free_margin = wallet-sum(float(p.get("required_margin",p.get("margin",0))) for p in positions)
        if required_margin + fee > free_margin + 1e-9:
            raise ValueError(
                f"insufficient margin: need {required_margin + fee:,.2f}, available {free_margin:,.2f}"
            )
        old_qty = float(old["qty"]) if old else 0.0
        old_entry = float(old["entry_price"]) if old else 0.0
        old_required = float(old.get("required_margin", 0.0)) if old else 0.0
        old_notional = float(old.get("notional", 0.0)) if old else 0.0
        total_qty = old_qty + qty
        total_notional = old_notional + notional
        total_required = old_required + required_margin
        entry = (
            (old_entry * old_qty + fill * qty) / total_qty if total_qty > 0 else fill
        )
        margin = total_required
        if side == "LONG":
            if margin_mode == "CROSS":
                liq = (entry - wallet / total_qty) / (1.0 - self.MMR)
            else:
                liq = entry * (1.0 - total_required / total_notional + self.MMR)
            if take_profit is not None and take_profit <= entry:
                raise ValueError("long take profit must be above entry")
            if stop_loss is not None and stop_loss >= entry:
                raise ValueError("long stop loss must be below entry")
        else:
            if margin_mode == "CROSS":
                liq = (entry + wallet / total_qty) / (1.0 + self.MMR)
            else:
                liq = entry * (1.0 + total_required / total_notional - self.MMR)
            if take_profit is not None and take_profit >= entry:
                raise ValueError("short take profit must be below entry")
            if stop_loss is not None and stop_loss <= entry:
                raise ValueError("short stop loss must be above entry")
        self.state["wallet"] = wallet - fee
        self.state["fees"] = float(self.state["fees"]) + fee
        if old:
            merged = {
                **old,
                "qty": total_qty,
                "entry_price": entry,
                "leverage": max(1, int(round(total_notional / total_required))) if total_required else leverage,
                "margin": margin,
                "required_margin": total_required,
                "notional": total_notional,
                "liq_price": max(0.01, liq),
                "take_profit": take_profit if take_profit is not None else old.get("take_profit"),
                "stop_loss": stop_loss if stop_loss is not None else old.get("stop_loss"),
                "opened_at": old.get("opened_at"),
            }
            self.state["positions"] = [merged if p is old else p for p in positions]
        else:
            self.state["positions"] = positions + [{
                "id": f"P{time.time_ns()}",
                "side": side,
                "qty": total_qty,
                "entry_price": entry,
                "leverage": leverage,
                "margin": margin,
                "required_margin": total_required,
                "margin_mode": margin_mode,
                "notional": total_notional,
                "liq_price": max(0.01, liq),
                "take_profit": take_profit,
                "stop_loss": stop_loss,
                "opened_at": _now(),
                "reason": reason,
            }]
        self._append_order(
            {
                "time": _now(),
                "action": "ADD" if old else "OPEN",
                "side": side,
                "price": fill,
                "qty": qty,
                "leverage": leverage,
                "margin": margin,
                "fee": fee,
                "pnl": 0.0,
            }
        )
        self._event(
            "trade",
            f"{'ADD' if old else 'OPEN'} {side} {qty:.6f} {self.base_asset} @ {fill:,.2f} {leverage}x",
        )

    def set_tpsl(
        self, take_profit: float | None, stop_loss: float | None, position_id: str | None = None
    ) -> dict[str, Any]:
        with self.lock:
            pos = self._position_by_id(position_id)
            if not pos:
                raise ValueError("no open position")
            entry = float(pos["entry_price"])
            tp = None if take_profit in (None, "") else float(take_profit)
            sl = None if stop_loss in (None, "") else float(stop_loss)
            if tp is not None:
                if pos["side"] == "LONG" and tp <= entry:
                    raise ValueError("long take profit must be above entry")
                if pos["side"] == "SHORT" and tp >= entry:
                    raise ValueError("short take profit must be below entry")
            if sl is not None:
                if pos["side"] == "LONG" and sl >= entry:
                    raise ValueError("long stop loss must be below entry")
                if pos["side"] == "SHORT" and sl <= entry:
                    raise ValueError("short stop loss must be above entry")
            pos["take_profit"] = tp
            pos["stop_loss"] = sl
            self._event("info", f"TP/SL updated: {tp or '—'} / {sl or '—'}")
            self.save()
            return self.status()

    def close_position(self, reason: str = "manual", position_id: str | None = None) -> dict[str, Any]:
        targets = [self._position_by_id(position_id)] if position_id else list(self._positions())
        targets = [p for p in targets if p]
        if not targets:
            raise ValueError("no open position")
        price = self.price()
        last = None
        for pos in targets:
            last = self._close_at(
                price, reason, apply_slippage=True, position_id=pos.get("id")
            )
        return last

    def _close_at(
        self, price: float, reason: str, *, apply_slippage: bool, position_id: str | None = None
    ) -> dict[str, Any]:
        pos = self._position_by_id(position_id)
        if not pos:
            raise ValueError("no open position")
        if pos.get("reason")=="auto":
            self.state["auto_cooldown_until"] = time.time()+INTERVAL_MS[self.state.get("auto_interval","1h")]/1000
        price = float(price)
        slip = self.SLIPPAGE_BPS / 10_000.0 if apply_slippage else 0.0
        fill = price * (1.0 - slip if pos["side"] == "LONG" else 1.0 + slip)
        qty = float(pos["qty"])
        entry = float(pos["entry_price"])
        pnl = (fill - entry) * qty if pos["side"] == "LONG" else (entry - fill) * qty
        fee = qty * fill * self.FEE_RATE
        self.state["wallet"] = float(self.state["wallet"]) + pnl - fee
        self.state["realized_pnl"] = float(self.state["realized_pnl"]) + pnl
        self.state["fees"] = float(self.state["fees"]) + fee
        self._append_order(
            {
                "time": _now(),
                "action": "CLOSE",
                "side": pos["side"],
                "price": fill,
                "qty": qty,
                "leverage": pos["leverage"],
                "margin": pos["margin"],
                "fee": fee,
                "pnl": pnl,
                "reason": reason,
            }
        )
        self._event("trade", f"CLOSE {pos['side']} {qty:.6f} {self.base_asset} @ {fill:,.2f}; PnL {pnl:,.2f}")
        pid = pos.get("id")
        self.state["positions"] = [p for p in self._positions() if p.get("id") != pid]
        # A position exit invalidates its remaining close-limit order.
        self.state["pending"] = [
            o for o in self.state.get("pending", [])
            if not (o.get("action") == "CLOSE" and o.get("position_id") == pid)
        ]
        self.save()
        return self.state["orders"][-1]

    def cancel_order(self, order_id: str) -> None:
        before = len(self.state.get("pending", []))
        self.state["pending"] = [o for o in self.state.get("pending", []) if o.get("id") != order_id]
        if len(self.state["pending"]) == before:
            raise ValueError("pending order not found")
        self._event("info", f"cancelled {order_id}")
        self.save()

    def set_trailing(self, position_id: str, enabled: bool, activation_pct: float=.01, distance_pct: float=.005):
        with self.lock:
            if not all(math.isfinite(v) for v in (activation_pct,distance_pct)) or not .001<=activation_pct<=.5 or not .0005<=distance_pct<activation_pct:
                raise ValueError("启动浮盈范围 0.1%–50%，回撤距离至少 0.05% 且小于启动浮盈")
            pos = self._position_by_id(position_id)
            if not pos:
                raise ValueError("仓位不存在")
            pos.update(trailing_enabled=enabled,trail_activation_pct=activation_pct,trail_distance_pct=distance_pct)
            if not enabled:
                pos.update(trailing_active=False,trailing_stop=None,trailing_best=pos["entry_price"])
            self.save()
            return self.status()

    def reset(self) -> None:
        self.state = self._new_state()
        self._event("info", "futures paper account reset")
        self.save()

    # ------------------------------------------------------------------- read
    def _append_order(self, order: dict[str, Any]) -> None:
        self.state.setdefault("orders", []).append(order)
        self.state["orders"] = self.state["orders"][-200:]

    def status(self) -> dict[str, Any]:
        with self.lock:
            price = self.state.get("last_price")
            if price is None:
                try:
                    price = self.price()
                    self.state["last_price"] = price
                except Exception:
                    price = 0.0
            positions = []
            unrealized = 0.0
            margin = 0.0
            for pos in self._positions():
                qty = float(pos["qty"])
                entry = float(pos["entry_price"])
                upnl = (price - entry) * qty if pos["side"] == "LONG" else (entry - price) * qty
                pos_margin = float(pos["margin"])
                unrealized += upnl
                margin += pos_margin
                positions.append({
                    **pos,
                    "mark_price": price,
                    "unrealized_pnl": upnl,
                    "roe": upnl / pos_margin if pos_margin else 0.0,
                })
            position = positions[0] if positions else None
            wallet = float(self.state["wallet"])
            equity = wallet + unrealized
            return {
                "symbol": self.symbol,
                "mode": "paper_futures",
                "price": float(price or 0.0),
                "source": "Spot mark price · simulated perpetual",
                "account": {
                    "equity": equity,
                    "wallet": wallet,
                    "available": max(0.0, wallet - margin),
                    "margin": margin,
                    "unrealized_pnl": unrealized,
                    "realized_pnl": float(self.state["realized_pnl"]),
                    "session_pnl": equity - float(self.state["initial_equity"]),
                    "fees": float(self.state["fees"]),
                },
                "positions": positions,
                "position": position,
                "pending": list(self.state.get("pending", [])),
                "orders": list(reversed(self.state.get("orders", [])[-40:])),
                "events": [
                    {**event, "message": event["message"].replace(" BTC @", f" {self.base_asset} @")}
                    for event in self.state.get("events", [])[-40:]
                ],
                "config": {
                    "max_leverage": self.MAX_LEVERAGE,
                    "fee_rate": self.FEE_RATE,
                    "slippage_bps": self.SLIPPAGE_BPS,
                    "mmr": self.MMR,
                },
                "auto": {
                    "enabled": bool(self.state.get("auto_enabled")),
                    "interval": self.state.get("auto_interval", "15m"),
                    "leverage": int(self.state.get("auto_leverage", 5)),
                    "risk_fraction": float(self.state.get("auto_risk_fraction",.005)),
                    "model_version": "Trend V2",
                    "model_id": self.state.get("auto_model_id","trend-v2"),
                    "cooldown_until": float(self.state.get("auto_cooldown_until",0)),
                    "snapshot": dict(self.model_snapshot),
                    "protection": "2.5 ATR 初始止损；1R 启动、2 ATR 跟踪；退出冷却一根",
                },
                "last_error": self.last_error,
                "updated_at": self.state.get("updated_at"),
            }
