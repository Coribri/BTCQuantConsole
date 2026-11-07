"""Strategy service shared by the testnet console and the local paper fallback."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any

import pandas as pd

from . import indicators as ta
from .broker import BaseBroker, PaperBroker, build_broker
from .config import AppConfig
from .engine import entry_budget
from .strategies import strategy_snapshot
from .testnet import TestnetError
from .models import ModelRegistry
from .quant_risk import data_guard, update_trailing
from .data import INTERVAL_MS
import math


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


class TradingService:
    def __init__(self, cfg: AppConfig, *, force_paper: bool = False):
        self.cfg = cfg
        self.base_asset = cfg.market.symbol.removesuffix(cfg.market.quote)
        self.broker: BaseBroker = PaperBroker(cfg) if force_paper else build_broker(cfg)
        self.strategy = "trend_v2"
        self.models = ModelRegistry(cfg.root,cfg.web.port)
        settings=self.broker.state.get("auto_settings",{})
        self.execution_interval = settings.get("interval",cfg.market.interval)
        candidate_model = settings.get("model_id","spot-trend-v2")
        try:
            candidate_spec = self.models.get(candidate_model)
            if candidate_spec.scope != "spot":
                candidate_model = "spot-trend-v2"
        except ValueError:
            candidate_model = "spot-trend-v2"
        self.model_id = candidate_model
        self.risk_fraction = float(settings.get("risk_fraction",.005))
        self.params = self.models.get(self.model_id).parameters.model_dump()
        self.risk_fraction=min(self.risk_fraction,self.models.get(self.model_id).risk_fraction)
        self.auto_enabled = bool(cfg.testnet.auto_start)
        self.lock = threading.RLock()
        self.events: deque[dict[str, Any]] = deque(maxlen=200)
        self.snapshot: dict[str, Any] = {}
        self.last_error: str | None = None
        self.last_update: str | None = None
        self.running = False
        self.thread: threading.Thread | None = None
        self.events_file = cfg.root / "runtime" / "dashboard_events.jsonl"
        self._event("info", f"service initialized in {self.broker.mode} mode")

    # --------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="btcquant-paper-loop", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self.models.close()

    def _loop(self) -> None:
        while self.running:
            self.poll()
            time.sleep(max(1, int(self.cfg.testnet.poll_seconds)))

    # ------------------------------------------------------------------ events
    def _event(self, level: str, message: str, **detail: Any) -> None:
        item = {
            "time": _now_iso(),
            "level": level,
            "message": message,
            **detail,
        }
        self.events.append(item)
        try:
            self.events_file.parent.mkdir(parents=True, exist_ok=True)
            with self.events_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------------- poll
    def poll(self) -> dict[str, Any]:
        with self.lock:
            try:
                # Older spot accounts may still have a saved fixed stop.
                if self.broker.state.get("stop_price") is not None:
                    self.broker.state["stop_price"] = None
                    self.broker.save()
                price = self.broker.price()
                # Keep the chart and signal available even if the signed account
                # endpoint is down or the testnet key is invalid.
                account: dict[str, Any] = {}
                account_error: str | None = None
                try:
                    account = self.broker.account(price)
                except Exception as exc:
                    account_error = str(exc)
                    account = {
                        "mode": self.broker.mode,
                        "connected": False,
                        "error": account_error,
                        "price": price,
                        "position_qty": 0.0,
                        "equity": None,
                    }
                    self._event("error", f"account sync failed: {account_error}")

                acted = False
                if not account_error:
                    acted = self._protect_position(account,price)
                    if acted:
                        account=self.broker.account(price)
                try:
                    frame = self.broker.klines(self.execution_interval,limit=400)
                    frame.attrs["symbol"]=self.cfg.market.symbol
                    current_bar=pd.Timestamp(frame.iloc[-1].open_time).isoformat()
                    remote=self.auto_enabled and self.broker.get_meta().get("last_processed_bar")!=current_bar
                    signals=self.models.evaluate_nonblocking(frame,self.model_id,self.execution_interval,"spot",allow_remote=remote,
                        cost=2*(self.cfg.paper.fee_rate+self.cfg.paper.slippage_bps/10000))
                    last=signals.iloc[-1]
                    signal=strategy_snapshot(signals,self.strategy)
                    signal["data_guard"]=data_guard(frame,self.execution_interval) or signal.get("model_error") or None
                except Exception as exc:
                    # Quote/account and protective exits remain live even when
                    # fetching signal history or a plugin fails.
                    self.snapshot.update(price=price,account=account)
                    raise exc
                if self.auto_enabled and not account_error:
                    acted = self._auto_step(account, last, signal, price) or acted
                if acted:
                    try:
                        account = self.broker.account(price)
                    except Exception as exc:
                        account_error = str(exc)
                        self._event("error", f"post-trade account sync failed: {account_error}")

                self.snapshot = {
                    "price": price,
                    "signal": signal,
                    "account": account,
                    "strategy": self.strategy,
                    "params": self.params,
                    "bar": {
                        "time": signal.get("bar_time"),
                        "close": signal.get("close"),
                        "atr14": signal.get("atr14"),
                    },
                }
                self.last_error = account_error
                self.last_update = _now_iso()
            except Exception as exc:
                self.last_error = str(exc)
                self._event("error", str(exc))
                if not self.snapshot:
                    self.snapshot = {"price": None, "signal": {}, "account": {}}
                self.snapshot.setdefault("signal",{})["data_guard"]=str(exc)
            return self.status()

    def _protect_position(self,account,price):
        if float(account.get("position_qty") or 0)<=1e-8:
            return False
        protection=self.broker.state.get("model_protection")
        if not isinstance(protection, dict) or not protection.get("trailing_enabled"):
            return False
        update_trailing(protection, price, self.cfg.paper.fee_rate, self.cfg.paper.slippage_bps / 10000)
        self.broker.save()
        stop = protection.get("trailing_stop")
        if protection.get("trailing_active") and stop and price <= float(stop):
            self.broker.sell_all(reason="trailing_profit")
            self.broker.set_meta(last_exit_at=_now_iso(),bars_held=0,highest_close=0.)
            self.broker.state.pop("model_protection",None)
            self.broker.save()
            self._event("trade", "移动止盈触发，按当前市价退出")
            return True
        return False

    def _auto_step(
        self,
        account: dict[str, Any],
        last: pd.Series,
        signal: dict[str, Any],
        price: float,
    ) -> bool:
        qty = float(account.get("position_qty") or 0.0)
        meta = self.broker.get_meta()
        bar_time = pd.Timestamp(last["open_time"])
        if bar_time.tzinfo is None:
            bar_time = bar_time.tz_localize("UTC")
        last_processed = _parse_ts(meta.get("last_processed_bar"))
        new_bar = last_processed is None or bar_time > last_processed

        if not new_bar:
            return False
        if signal.get("data_guard"):
            return False

        max_hold = int(last.get("max_hold", 0) or 0)
        atr = float(last.get("atr14", 0) or 0)
        close = float(last["close"])
        exit_signal = bool(last["exit_signal"])
        entry_signal = bool(last["entry_signal"])
        interval_s = INTERVAL_MS.get(self.execution_interval, 14_400_000) / 1000

        if qty > 1e-6:
            missed = 1
            if last_processed is not None:
                missed = max(1, int(round((bar_time - last_processed).total_seconds() / interval_s)))
            bars_held = int(meta.get("bars_held", 0) or 0) + missed
            highest = max(float(meta.get("highest_close") or price), close)
            reason = ""
            if max_hold > 0 and bars_held >= max_hold:
                reason = "time"
            elif exit_signal:
                reason = "signal"

            if reason:
                try:
                    order = self.broker.sell_all(reason=reason)
                    self.broker.state.pop("model_protection", None)
                    self._event(
                        "trade",
                        f"{reason.upper()} SELL {order.get('qty', 0):.6f} {self.base_asset} @ {order.get('price', 0):,.2f}",
                        order=order,
                    )
                    self.broker.set_meta(
                        last_processed_bar=bar_time.isoformat(),
                        last_exit_at=_now_iso(),
                        bars_held=0,
                        highest_close=0.0,
                    )
                    return True
                except TestnetError as exc:
                    self._event("error", f"auto sell failed: {exc}")
                    self.broker.set_meta(last_processed_bar=bar_time.isoformat())
                    return False

            self.broker.set_meta(
                last_processed_bar=bar_time.isoformat(),
                bars_held=bars_held,
                highest_close=highest,
            )
            return False

        last_exit=_parse_ts(meta.get("last_exit_at"))
        cooldown=last_exit is not None and pd.Timestamp.now(tz="UTC")<last_exit+pd.Timedelta(seconds=interval_s)
        if entry_signal and not cooldown and meta.get("last_entry_bar") != bar_time.isoformat():
            sizing_distance = float(last.get("stop_atr", 3.0) or 3.0) * atr
            if not math.isfinite(sizing_distance) or not 0 < sizing_distance < price:
                self._event("warning", "entry skipped: invalid ATR sizing distance")
                self.broker.set_meta(last_processed_bar=bar_time.isoformat())
                return False
            budget = entry_budget(
                float(account.get("equity", 0.0)),
                price,
                sizing_distance,
                max_fraction=min(.25,self.cfg.paper.max_fraction),
                sizing="risk",
                risk_per_trade=self.risk_fraction,
            )
            cost=price*2*(self.cfg.paper.fee_rate+self.cfg.paper.slippage_bps/10000)
            budget=min(budget,float(account.get("equity") or 0)*self.risk_fraction/(sizing_distance+cost)*price)
            if not math.isfinite(budget):
                raise ValueError("模型仓位预算无效")
            if budget < self.broker.filters.min_notional:
                self._event(
                    "warning",
                    f"entry skipped: budget {budget:.2f} < minNotional {self.broker.filters.min_notional:.2f}",
                )
                self.broker.set_meta(last_processed_bar=bar_time.isoformat())
                return False
            try:
                order = self.broker.buy_quote(
                    budget,
                    signal_close=close,
                    reason="auto_entry",
                )
                self._event(
                    "trade",
                    f"AUTO BUY {order.get('qty', 0):.6f} {self.base_asset} @ {order.get('price', 0):,.2f}",
                    order=order,
                )
                self.broker.set_meta(
                    last_processed_bar=bar_time.isoformat(),
                    last_entry_bar=bar_time.isoformat(),
                    bars_held=0,
                    highest_close=close,
                )
                actual=float(order.get("price") or price)
                self.broker.state["model_protection"]={"entry_price":actual,"side":"LONG",
                    "trailing_enabled":bool(self.params.get("trailing_enabled",True)),
                    "trail_activation_pct":sizing_distance/actual*float(self.params.get("activation_r",1)),
                    "trail_distance_pct":float(self.params.get("trail_atr",2))*atr/actual,
                    "trailing_best":actual,"trailing_active":False,"trailing_stop":None}
                self.broker.save()
                return True
            except TestnetError as exc:
                self._event("error", f"auto buy failed: {exc}")
                self.broker.set_meta(last_processed_bar=bar_time.isoformat())
                return False

        self.broker.set_meta(last_processed_bar=bar_time.isoformat())
        return False

    # ----------------------------------------------------------------- manual
    def manual_order(
        self,
        side: str,
        *,
        quote_amount: float | None = None,
        reason: str = "manual",
    ) -> dict[str, Any]:
        side = side.upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        with self.lock:
            if side == "BUY":
                if quote_amount is None or float(quote_amount) <= 0:
                    raise ValueError("quote_amount is required for BUY")
                order = self.broker.buy_quote(
                    float(quote_amount),
                    signal_close=(self.snapshot.get("signal") or {}).get("close"),
                    reason=reason,
                )
            else:
                order = self.broker.sell_all(reason=reason)
            if self.broker.state.pop("model_protection", None) is not None:
                self.broker.save()
            self._event(
                "trade",
                f"MANUAL {side} {order.get('qty', 0):.6f} {self.base_asset} @ {order.get('price', 0):,.2f}",
                order=order,
            )
            self.poll()
            return order

    def set_auto(self, enabled: bool, interval: str | None=None, risk_fraction: float | None=None, model_id: str | None=None) -> None:
        with self.lock:
            if self.auto_enabled and (interval and interval!=self.execution_interval or model_id and model_id!=self.model_id):
                raise ValueError("请先关闭 AUTO，再修改模型或执行周期")
            if interval:
                if interval not in {"1m","5m","15m","30m","1h","2h","4h","8h","12h","1d"}:
                    raise ValueError("现货 AUTO 支持 1M–1D")
                self.execution_interval=interval
            if model_id:
                spec=self.models.get(model_id)
                if spec.scope != "spot":
                    raise ValueError("现货 AUTO 只能选择现货模型")
                self.model_id=spec.id
                self.params=spec.parameters.model_dump()
            if risk_fraction is not None:
                if not math.isfinite(risk_fraction) or not .001<=risk_fraction<=.02:
                    raise ValueError("仓位预算系数范围为 0.1%–2%")
                self.risk_fraction=risk_fraction
            self.broker.state["auto_settings"]={"interval":self.execution_interval,"risk_fraction":self.risk_fraction,"model_id":self.model_id}
            self.risk_fraction=min(self.risk_fraction,self.models.get(self.model_id).risk_fraction)
            self.broker.state["auto_settings"]["risk_fraction"]=self.risk_fraction
            self.broker.save()
            if enabled and self.broker.mode == "testnet":
                self.broker.connect()  # raises a clear error if keys are invalid
            self.auto_enabled = bool(enabled)
            self._event(
                "info",
                f"auto trading {'ENABLED' if self.auto_enabled else 'DISABLED'}",
            )
            if self.auto_enabled:
                # Evaluate immediately rather than waiting for the next poll.
                self.poll()

    def reconnect(self) -> dict[str, Any]:
        with self.lock:
            if self.broker.mode == "testnet":
                self.broker.connect()
            self._event("info", "reconnect requested")
            return self.poll()

    def reset_paper(self, initial_cash: float | None = None) -> dict[str, Any]:
        with self.lock:
            if self.broker.mode != "paper":
                raise ValueError("reset is only available in local paper mode")
            reset = getattr(self.broker, "reset", None)
            if not callable(reset):
                raise ValueError("broker does not support reset")
            reset(initial_cash=initial_cash)
            self._event("info", "local paper account reset")
            return self.poll()

    # ------------------------------------------------------------------- read
    def status(self) -> dict[str, Any]:
        with self.lock:
            account = dict(self.snapshot.get("account") or {})
            signal = dict(self.snapshot.get("signal") or {})
            price = self.snapshot.get("price")
            if price is None:
                price = account.get("price")
            return {
                "symbol": self.cfg.market.symbol,
                "mode": self.broker.mode,
                "label": self.broker.label,
                "connected": bool(getattr(self.broker, "connected", True)),
                "configured": bool(getattr(self.broker, "configured", True)),
                "data_source": getattr(self.broker, "market_data_source", "unknown"),
                "error": self.last_error or getattr(self.broker, "error", None),
                "auto_enabled": self.auto_enabled,
                "auto": {"interval":self.execution_interval,"risk_fraction":self.risk_fraction,"model_id":self.model_id,"model_version":"Trend V2"},
                "protection": self.broker.state.get("model_protection"),
                "strategy": self.strategy,
                "params": self.params,
                "price": price,
                "account": account,
                "signal": signal,
                "risk": {
                    "max_fraction": self.cfg.paper.max_fraction,
                    "risk_per_trade": self.risk_fraction,
                },
                "orders": self.broker.recent_orders(25),
                "events": list(self.events)[-40:],
                "last_update": self.last_update,
                "poll_seconds": self.cfg.testnet.poll_seconds,
                "safety": "TESTNET ONLY — no mainnet order endpoint is used",
            }

    def model_snapshot(self, interval: str) -> dict[str, Any]:
        interval = interval if interval in {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d"} else "4h"
        with self.lock:
            frame = self.broker.klines(interval, limit=400)
            signals = self.models.evaluate(frame,self.model_id,interval,"spot",allow_remote=False,
                cost=2*(self.cfg.paper.fee_rate+self.cfg.paper.slippage_bps/10000))
            signal = strategy_snapshot(signals, self.strategy)
            last = signals.iloc[-1]
            return {
                "symbol": self.cfg.market.symbol,
                "interval": interval,
                "strategy": self.strategy,
                "params": self.params,
                "signal": signal,
                "bar": {
                    "time": signal.get("bar_time"),
                    "close": float(last.get("close", 0.0)),
                    "atr14": signal.get("atr14"),
                },
            }

    def candles(self, interval: str | None = None, limit: int = 240) -> dict[str, Any]:
        interval = interval or self.cfg.market.interval
        limit = max(50, min(int(limit), 3000))
        with self.lock:
            # Fetch extra history so EMA200 is fully populated on the visible
            # window, then trim the response to the requested number of bars.
            fetch_limit = min(3000, limit + 220)
            frame = self.broker.klines(interval, limit=fetch_limit)
            close = frame["close"]
            ema20 = ta.ema(close, 20)
            ema100 = ta.ema(close, 100)
            ema200 = ta.ema(close, 200)
            ma5 = ta.sma(close, 5)
            ma10 = ta.sma(close, 10)
            ma20 = ta.sma(close, 20)
            ma60 = ta.sma(close, 60)
            view = frame.tail(limit).reset_index(drop=True)
            visible_start = int(pd.Timestamp(view["open_time"].iloc[0]).timestamp())
            candles = []
            for _, row in view.iterrows():
                candles.append(
                    {
                        "time": int(pd.Timestamp(row["open_time"]).timestamp()),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                        "quote_volume": float(row["quote_volume"]) if pd.notna(row.get("quote_volume")) else None,
                    }
                )
            times = [c["time"] for c in candles]

            def line(series: pd.Series) -> list[dict[str, Any]]:
                out = []
                for ts, value in zip(frame["open_time"], series):
                    t = int(pd.Timestamp(ts).timestamp())
                    if t >= visible_start and pd.notna(value):
                        out.append({"time": t, "value": float(value)})
                return out

            markers = []
            if times:
                for order in self.broker.recent_orders(100):
                    if order.get("side") not in {"BUY", "SELL"}:
                        continue
                    ts = int(pd.Timestamp(order["time"]).timestamp())
                    # Snap the marker to the candle at or before the fill.
                    snap_time = times[0]
                    for t in times:
                        if t <= ts:
                            snap_time = t
                        else:
                            break
                    markers.append(
                        {
                            "time": snap_time,
                            "position": "belowBar" if order["side"] == "BUY" else "aboveBar",
                            "color": "#16c784" if order["side"] == "BUY" else "#ea3943",
                            "shape": "arrowUp" if order["side"] == "BUY" else "arrowDown",
                            "text": order["side"],
                        }
                    )
            markers.sort(key=lambda item: item["time"])
            return {
                "symbol": self.cfg.market.symbol,
                "interval": interval,
                "data_source": getattr(self.broker, "market_data_source", "unknown"),
                "candles": candles,
                "ema20": line(ema20),
                "ema100": line(ema100),
                "ema200": line(ema200),
                "ma5": line(ma5),
                "ma10": line(ma10),
                "ma20": line(ma20),
                "ma60": line(ma60),
                "markers": markers[-40:],
            }
