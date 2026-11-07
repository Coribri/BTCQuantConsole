"""Trading backends for the visual console.

Two implementations share the same small interface:

- ``TestnetBroker``: signed Binance Spot Testnet orders and real testnet balances.
- ``PaperBroker``: local simulated account used when Testnet keys are absent.

The service layer owns the strategy; brokers only deal with price, account,
orders and persisted position metadata.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from uuid import uuid4
from typing import Any

import pandas as pd
import requests
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_keys.exceptions import BadSignature
from hyperliquid.utils.signing import sign_l1_action, user_signed_payload

from .binance_spot import ExchangeRequestError
from .config import AppConfig
from .data import fetch_recent_klines, fetch_ticker
from .testnet import (
    TESTNET_BASE,
    SymbolFilters,
    TestnetAPI,
    TestnetError,
    TestnetNotConfigured,
    parse_order_fills,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"corrupt state file: {path}") from exc


class BaseBroker:
    mode = "base"
    label = "BASE"

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.symbol = cfg.market.symbol
        self.connected = False
        self.error: str | None = None
        self._filters: SymbolFilters | None = None
        self.market_data_source = "unknown"
        self.state: dict[str, Any] = {}

    # --------------------------------------------------------------- market
    def price(self) -> float:
        raise NotImplementedError

    def klines(self, interval: str, limit: int = 300) -> pd.DataFrame:
        raise NotImplementedError

    # --------------------------------------------------------------- account
    def account(self, price: float | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def buy_quote(
        self,
        quote_amount: float,
        *,
        stop_price: float | None = None,
        signal_close: float | None = None,
        reason: str = "manual",
    ) -> dict[str, Any]:
        raise NotImplementedError

    def sell_all(self, *, reason: str = "manual") -> dict[str, Any]:
        raise NotImplementedError

    # ---------------------------------------------------------------- state
    @property
    def filters(self) -> SymbolFilters:
        if self._filters is None:
            self._filters = SymbolFilters(
                step_size=0.00001, min_qty=0.00001, min_notional=5.0, tick_size=0.01
            )
        return self._filters

    def get_meta(self) -> dict[str, Any]:
        return dict(self.state.get("meta", {}))

    def set_meta(self, **kwargs: Any) -> None:
        self.state.setdefault("meta", {}).update(kwargs)
        self.save()

    def recent_orders(self, limit: int = 20) -> list[dict[str, Any]]:
        orders = self.state.get("orders", [])
        return list(reversed(orders[-limit:]))

    def save(self) -> None:
        raise NotImplementedError


class TestnetBroker(BaseBroker):
    mode = "testnet"
    label = "BINANCE TESTNET"

    def __init__(self, cfg: AppConfig):
        super().__init__(cfg)
        self.api_key = os.environ.get(cfg.testnet.api_key_env, "").strip()
        self.api_secret = os.environ.get(cfg.testnet.api_secret_env, "").strip()
        self.configured = bool(self.api_key and self.api_secret)
        self.state_file = cfg.testnet.state_file
        self.state = _load_json(self.state_file)
        self.state.setdefault("mode", "testnet")
        self.state.setdefault("orders", [])
        self.state.setdefault("meta", {})
        self.state.setdefault("initial_equity", None)
        self.state.setdefault("entry_price", None)
        self.state.setdefault("entry_qty", 0.0)
        self.state.setdefault("entry_time", None)
        self.state.setdefault("entry_fee", 0.0)
        self.state.setdefault("stop_price", None)
        self.state.setdefault("realized_pnl", 0.0)
        self.state.setdefault("fees", 0.0)
        self.state.setdefault("last_price", None)
        self.api: TestnetAPI | None = None
        self.public_api = TestnetAPI(
            "",
            "",
            base_url=cfg.testnet.base_url or TESTNET_BASE,
            recv_window=cfg.testnet.recv_window,
        )
        if self.configured:
            self.api = TestnetAPI(
                self.api_key,
                self.api_secret,
                base_url=cfg.testnet.base_url or TESTNET_BASE,
                recv_window=cfg.testnet.recv_window,
            )

    def connect(self) -> None:
        if not self.configured or self.api is None:
            self.connected = False
            self.error = (
                f"missing {self.cfg.testnet.api_key_env} / {self.cfg.testnet.api_secret_env}"
            )
            return
        try:
            self.api.sync_time()
            self._filters = self.api.symbol_filters(self.symbol)
            self.api.account()  # validates the signature and key permissions
            self.connected = True
            self.error = None
        except TestnetError as exc:
            self.connected = False
            self.error = str(exc)
            raise

    def _ensure_api(self) -> TestnetAPI:
        if not self.configured or self.api is None:
            raise TestnetNotConfigured(
                "Binance Testnet keys are not configured; set "
                f"{self.cfg.testnet.api_key_env} and {self.cfg.testnet.api_secret_env}"
            )
        if not self.connected:
            self.connect()
        assert self.api is not None
        return self.api

    def price(self) -> float:
        try:
            return self.public_api.price(self.symbol)
        except TestnetError:
            return float(fetch_ticker(self.cfg.data, self.symbol)["price"])

    def klines(self, interval: str, limit: int = 300) -> pd.DataFrame:
        # Always use mainnet candles for charts/signals.  Testnet candles can
        # contain bad wicks and only retain a short history; execution and
        # balances still come from the Testnet account.
        try:
            frame = fetch_recent_klines(self.cfg.data, self.cfg.market, limit=limit, interval=interval)
            self.market_data_source = "mainnet"
            return frame
        except Exception:
            self.market_data_source = "testnet"
            return self.public_api.klines(self.symbol, interval, limit)

    def account(self, price: float | None = None) -> dict[str, Any]:
        api = self._ensure_api()
        try:
            raw = api.account()
            if price is None:
                price = api.price(self.symbol)
            self.connected = True
            self.error = None
        except TestnetError as exc:
            self.connected = False
            self.error = str(exc)
            raise
        balances = raw["balances"]
        usdt = balances.get("USDT", {"free": 0.0, "locked": 0.0, "total": 0.0})
        base_asset = self.symbol.removesuffix("USDT")
        btc = balances.get(base_asset, {"free": 0.0, "locked": 0.0, "total": 0.0})
        equity = float(usdt["total"]) + float(btc["total"]) * float(price)
        if self.state.get("initial_equity") is None:
            self.state["initial_equity"] = equity
        qty = float(btc["total"])
        free_qty = float(btc["free"])
        entry = self.state.get("entry_price")
        adopted = False
        if qty > 0.000001 and entry is None:
            # The testnet account may already hold BTC from an earlier session.
            # Adopt it at the current mark so the UI is coherent, but mark it.
            entry = float(price)
            self.state["entry_price"] = entry
            self.state["entry_qty"] = qty
            self.state["entry_time"] = _now_iso()
            self.state["stop_price"] = None
            adopted = True
        if qty <= 0.000001:
            self.state["entry_price"] = None
            self.state["entry_qty"] = 0.0
            self.state["entry_time"] = None
            self.state["entry_fee"] = 0.0
            self.state["stop_price"] = None
        if adopted:
            self._append_order(
                {
                    "time": _now_iso(),
                    "side": "ADOPT",
                    "price": float(price),
                    "qty": qty,
                    "quote": qty * float(price),
                    "fee": 0.0,
                    "status": "INFO",
                    "reason": f"existing testnet {base_asset} balance adopted at mark",
                }
            )
        self.state["last_price"] = float(price)
        self.save()
        unrealized = (
            qty * (float(price) - float(entry)) if qty > 0 and entry is not None else 0.0
        )
        return {
            "mode": self.mode,
            "connected": self.connected,
            "error": self.error,
            "price": float(price),
            "usdt_free": float(usdt["free"]),
            "usdt_locked": float(usdt["locked"]),
            "btc_free": free_qty,
            "btc_locked": float(btc["locked"]),
            "btc_total": qty,
            "position_qty": qty,
            "equity": equity,
            "initial_equity": float(self.state["initial_equity"]),
            "session_pnl": equity - float(self.state["initial_equity"]),
            "entry_price": entry,
            "stop_price": self.state.get("stop_price"),
            "unrealized_pnl": unrealized,
            "realized_pnl": float(self.state.get("realized_pnl", 0.0)),
            "fees": float(self.state.get("fees", 0.0)),
            "adopted_position": adopted,
        }

    def buy_quote(
        self,
        quote_amount: float,
        *,
        stop_price: float | None = None,
        signal_close: float | None = None,
        reason: str = "manual",
    ) -> dict[str, Any]:
        api = self._ensure_api()
        filters = self.filters
        quote_amount = float(quote_amount)
        if quote_amount < filters.min_notional:
            raise TestnetError(
                f"order too small: {quote_amount:.2f} USDT < minNotional {filters.min_notional:.2f}"
            )
        raw = api.market_buy(self.symbol, quote_amount)
        qty, avg, fee = parse_order_fills(raw)
        if qty <= 0:
            raise TestnetError(f"testnet order returned no fills: {raw}")
        prev_qty = float(self.state.get("entry_qty", 0) or 0)
        prev_entry = float(self.state.get("entry_price") or 0.0)
        new_qty = prev_qty + qty
        self.state["entry_price"] = (
            (prev_qty * prev_entry + qty * avg) / new_qty if new_qty > 0 else avg
        )
        self.state["entry_qty"] = new_qty
        self.state["entry_time"] = _now_iso()
        self.state["entry_fee"] = float(self.state.get("entry_fee", 0.0)) + fee
        self.state["stop_price"] = stop_price
        self.state["fees"] = float(self.state.get("fees", 0.0)) + fee
        self.state["realized_pnl"] = float(self.state.get("realized_pnl", 0.0))
        order = {
            "time": _now_iso(),
            "side": "BUY",
            "price": avg,
            "qty": qty,
            "quote": qty * avg,
            "fee": fee,
            "status": "FILLED",
            "reason": reason,
            "order_id": raw.get("orderId"),
            "signal_close": signal_close,
            "stop_price": stop_price,
        }
        self._append_order(order)
        self.save()
        return order

    def sell_all(self, *, reason: str = "manual") -> dict[str, Any]:
        api = self._ensure_api()
        filters = self.filters
        acct = api.account()
        base_asset = self.symbol.removesuffix("USDT")
        btc = acct["balances"].get(base_asset, {"free": 0.0})
        free = float(btc.get("free", 0.0))
        qty = filters.round_qty(free)
        if qty < filters.min_qty:
            raise TestnetError(f"{base_asset} balance too small to sell: {free:.8f}")
        price = api.price(self.symbol)
        if qty * price < filters.min_notional:
            raise TestnetError(
                f"notional too small: {qty * price:.2f} < {filters.min_notional:.2f} USDT"
            )
        raw = api.market_sell(self.symbol, filters.fmt_qty(qty))
        filled, avg, fee = parse_order_fills(raw)
        if filled <= 0:
            raise TestnetError(f"testnet sell returned no fills: {raw}")
        entry = self.state.get("entry_price")
        gross = filled * (avg - float(entry)) if entry is not None else 0.0
        entry_fee = float(self.state.get("entry_fee", 0.0))
        net = gross - fee - entry_fee
        self.state["realized_pnl"] = float(self.state.get("realized_pnl", 0.0)) + net
        self.state["fees"] = float(self.state.get("fees", 0.0)) + fee
        self.state["entry_price"] = None
        self.state["entry_qty"] = 0.0
        self.state["entry_time"] = None
        self.state["entry_fee"] = 0.0
        self.state["stop_price"] = None
        order = {
            "time": _now_iso(),
            "side": "SELL",
            "price": avg,
            "qty": filled,
            "quote": filled * avg,
            "fee": fee,
            "status": "FILLED",
            "reason": reason,
            "order_id": raw.get("orderId"),
            "net_pnl": net,
        }
        self._append_order(order)
        self.save()
        return order

    def _append_order(self, order: dict[str, Any]) -> None:
        orders = self.state.setdefault("orders", [])
        orders.append(order)
        self.state["orders"] = orders[-200:]

    def save(self) -> None:
        _atomic_json(self.state_file, self.state)


class PaperBroker(BaseBroker):
    mode = "paper"
    label = "LOCAL PAPER"

    def __init__(self, cfg: AppConfig):
        super().__init__(cfg)
        self.state_file = cfg.testnet.paper_state_file
        self.state = _load_json(self.state_file)
        initial = float(cfg.paper.initial_cash)
        self.state.setdefault("mode", "paper")
        self.state.setdefault("initial_cash", initial)
        self.state.setdefault("initial_equity", initial)
        self.state.setdefault("cash", initial)
        self.state.setdefault("qty", 0.0)
        self.state.setdefault("cost_basis", 0.0)
        self.state.setdefault("entry_price", None)
        self.state.setdefault("entry_time", None)
        self.state.setdefault("stop_price", None)
        self.state.setdefault("realized_pnl", 0.0)
        self.state.setdefault("fees", 0.0)
        self.state.setdefault("orders", [])
        self.state.setdefault("meta", {})
        self.state.setdefault("last_price", None)
        self._public = TestnetAPI("", "", base_url=cfg.testnet.base_url or TESTNET_BASE)
        self.connected = True
        self.error = None

    def price(self) -> float:
        try:
            price = float(fetch_ticker(self.cfg.data, self.symbol)["price"])
            self.market_data_source = "mainnet"
        except Exception:
            price = self._public.price(self.symbol)
            self.market_data_source = "testnet"
        self.state["last_price"] = price
        return price

    def klines(self, interval: str, limit: int = 300) -> pd.DataFrame:
        try:
            frame = fetch_recent_klines(self.cfg.data, self.cfg.market, limit=limit, interval=interval)
            self.market_data_source = "mainnet"
            return frame
        except Exception:
            frame = self._public.klines(self.symbol, interval, limit)
            self.market_data_source = "testnet"
            return frame

    def account(self, price: float | None = None) -> dict[str, Any]:
        if price is None:
            price = self.price()
        qty = float(self.state.get("qty", 0.0))
        cash = float(self.state.get("cash", 0.0))
        equity = cash + qty * price
        cost_basis = float(self.state.get("cost_basis", 0.0))
        entry = self.state.get("entry_price")
        unrealized = qty * price - cost_basis if qty > 0 else 0.0
        return {
            "mode": self.mode,
            "connected": True,
            "error": None,
            "price": float(price),
            "usdt_free": cash,
            "usdt_locked": 0.0,
            "btc_free": qty,
            "btc_locked": 0.0,
            "btc_total": qty,
            "position_qty": qty,
            "equity": equity,
            "initial_equity": float(self.state.get("initial_equity", self.state["initial_cash"])),
            "session_pnl": equity - float(self.state.get("initial_equity", self.state["initial_cash"])),
            "entry_price": entry,
            "stop_price": self.state.get("stop_price"),
            "unrealized_pnl": unrealized,
            "realized_pnl": float(self.state.get("realized_pnl", 0.0)),
            "fees": float(self.state.get("fees", 0.0)),
            "adopted_position": False,
        }

    def buy_quote(
        self,
        quote_amount: float,
        *,
        stop_price: float | None = None,
        signal_close: float | None = None,
        reason: str = "manual",
    ) -> dict[str, Any]:
        quote_amount = float(quote_amount)
        if quote_amount < 5.0:
            raise TestnetError("paper order too small: minimum 5 USDT")
        cash = float(self.state.get("cash", 0.0))
        if quote_amount > cash:
            raise TestnetError(f"insufficient paper USDT: {quote_amount:.2f} > {cash:.2f}")
        price = self.price()
        fill = price * (1.0 + self.cfg.paper.slippage_bps / 10_000.0)
        fee = quote_amount * self.cfg.paper.fee_rate
        qty = quote_amount / fill
        prev_qty = float(self.state.get("qty", 0.0))
        prev_entry = float(self.state.get("entry_price") or 0.0)
        new_qty = prev_qty + qty
        self.state["cash"] = cash - quote_amount - fee
        self.state["qty"] = new_qty
        self.state["cost_basis"] = float(self.state.get("cost_basis", 0.0)) + quote_amount + fee
        self.state["fees"] = float(self.state.get("fees", 0.0)) + fee
        self.state["entry_price"] = (
            (prev_qty * prev_entry + qty * fill) / new_qty if new_qty > 0 else None
        )
        if self.state.get("entry_time") is None:
            self.state["entry_time"] = _now_iso()
        self.state["stop_price"] = stop_price
        order = {
            "time": _now_iso(),
            "side": "BUY",
            "price": fill,
            "qty": qty,
            "quote": quote_amount,
            "fee": fee,
            "status": "FILLED",
            "reason": reason,
            "signal_close": signal_close,
            "stop_price": stop_price,
        }
        self._append_order(order)
        self.save()
        return order

    def sell_all(self, *, reason: str = "manual") -> dict[str, Any]:
        qty = float(self.state.get("qty", 0.0))
        if qty <= 0:
            raise TestnetError(f"no paper {self.symbol.removesuffix(self.cfg.market.quote)} position to sell")
        price = self.price()
        fill = price * (1.0 - self.cfg.paper.slippage_bps / 10_000.0)
        gross = qty * fill
        fee = gross * self.cfg.paper.fee_rate
        proceeds = gross - fee
        cost_basis = float(self.state.get("cost_basis", 0.0))
        net = proceeds - cost_basis
        self.state["cash"] = float(self.state.get("cash", 0.0)) + proceeds
        self.state["realized_pnl"] = float(self.state.get("realized_pnl", 0.0)) + net
        self.state["fees"] = float(self.state.get("fees", 0.0)) + fee
        self.state["qty"] = 0.0
        self.state["cost_basis"] = 0.0
        self.state["entry_price"] = None
        self.state["entry_time"] = None
        self.state["stop_price"] = None
        order = {
            "time": _now_iso(),
            "side": "SELL",
            "price": fill,
            "qty": qty,
            "quote": gross,
            "fee": fee,
            "status": "FILLED",
            "reason": reason,
            "net_pnl": net,
        }
        self._append_order(order)
        self.save()
        return order

    def reset(self, initial_cash: float | None = None) -> None:
        initial = float(
            initial_cash
            if initial_cash is not None
            else self.state.get("initial_cash", self.cfg.paper.initial_cash)
        )
        self.state.update(
            {
                "initial_cash": initial,
                "initial_equity": initial,
                "cash": initial,
                "qty": 0.0,
                "cost_basis": 0.0,
                "entry_price": None,
                "entry_time": None,
                "stop_price": None,
                "realized_pnl": 0.0,
                "fees": 0.0,
                "orders": [],
                "meta": {},
            }
        )
        self.save()

    def _append_order(self, order: dict[str, Any]) -> None:
        orders = self.state.setdefault("orders", [])
        orders.append(order)
        self.state["orders"] = orders[-200:]

    def save(self) -> None:
        _atomic_json(self.state_file, self.state)


def build_broker(cfg: AppConfig) -> BaseBroker:
    """Use testnet when keys exist; otherwise fall back to local paper."""
    key = os.environ.get(cfg.testnet.api_key_env, "").strip()
    secret = os.environ.get(cfg.testnet.api_secret_env, "").strip()
    if key and secret:
        return TestnetBroker(cfg)
    return PaperBroker(cfg)


class HyperliquidReadOnly:
    """Public native perpetual market and wallet snapshots on Hyperliquid."""

    INFO_URL = {
        "mainnet": "https://api.hyperliquid.xyz/info",
        "testnet": "https://api.hyperliquid-testnet.xyz/info",
    }
    INTERVAL_MS = {"1h": 3_600_000, "4h": 14_400_000}

    @classmethod
    def _info(cls, network, kind, **fields):
        try:
            response = requests.post(
                cls.INFO_URL[network], json={"type": kind, **fields},
                timeout=7, allow_redirects=False,
            )
            if response.status_code != 200:
                raise ExchangeRequestError(f"Hyperliquid 数据暂不可用（HTTP {response.status_code}）")
            return response.json()
        except (requests.RequestException, json.JSONDecodeError) as exc:
            raise ExchangeRequestError("Hyperliquid 未返回有效数据，请稍后刷新") from exc

    def market(self, network, coin, interval):
        snapshot = self._info(network, "metaAndAssetCtxs")
        try:
            meta, contexts = snapshot
            idx = next((i for i, asset in enumerate(meta["universe"])
                        if asset["name"] == coin and not asset.get("isDelisted")), None)
            context = contexts[idx] if idx is not None else None
            if idx is not None and not isinstance(context, dict):
                raise TypeError("missing market context")
        except (TypeError, KeyError, IndexError, ValueError) as exc:
            raise ExchangeRequestError("Hyperliquid 合约行情格式异常") from exc
        if idx is None:
            raise LookupError(f"Hyperliquid 此网络暂未提供 {coin} 原生永续合约")

        candles = self.candles(network, coin, interval, 160)
        return {
            "network": network, "coin": coin, "interval": interval, "updated_at": _now_iso(),
            "mark": context.get("markPx"), "funding": context.get("funding"),
            "open_interest": context.get("openInterest"), "quote_volume": context.get("dayNtlVlm"),
            "candles": candles,
        }

    def candles(self, network, coin, interval, limit):
        end = time.time_ns() // 1_000_000
        start = end - limit * self.INTERVAL_MS[interval]
        raw = self._info(network, "candleSnapshot", req={
            "coin": coin, "interval": interval, "startTime": start, "endTime": end,
        })
        if not isinstance(raw, list):
            raise ExchangeRequestError("Hyperliquid K 线格式异常")
        candles = {}
        for item in raw:
            try:
                if item["s"] != coin or item["i"] != interval or not start <= item["t"] <= end:
                    continue
                opening, high, low, close = (float(item[key]) for key in ("o", "h", "l", "c"))
                if not all(math.isfinite(price) and price > 0 for price in (opening, high, low, close)):
                    continue
                if low > min(opening, close) or high < max(opening, close):
                    continue
                stamp = item["t"] // 1000
                volume = float(item.get("v", 0))
                if not math.isfinite(volume) or volume < 0:
                    continue
                candles[stamp] = dict(time=stamp, open=opening, high=high, low=low, close=close, volume=volume)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        return [candles[stamp] for stamp in sorted(candles)]

    def wallet(self, network, address):
        address = address.lower()
        state = self._info(network, "clearinghouseState", user=address)
        try:
            summary = state["marginSummary"]
            positions = [item["position"] for item in state["assetPositions"]]
            if not isinstance(summary, dict) or any(not isinstance(item, dict) for item in positions):
                raise TypeError("missing account details")
        except (TypeError, KeyError) as exc:
            raise ExchangeRequestError("Hyperliquid 合约账户格式异常") from exc
        orders = self._info(network, "frontendOpenOrders", user=address)
        if not isinstance(orders, list) or any(not isinstance(item, dict) for item in orders):
            raise ExchangeRequestError("Hyperliquid 委托格式异常")
        # The native perpetual endpoint also returns spot orders under @index or BASE/QUOTE.
        perpetual_orders = [item for item in orders if re.fullmatch(r"[A-Za-z0-9]+", str(item.get("coin", "")))]
        return {
            "network": network, "updated_at": _now_iso(),
            "equity": summary.get("accountValue"), "margin_used": summary.get("totalMarginUsed"),
            "withdrawable": state.get("withdrawable"),
            "positions": [{key: item.get(key) for key in ("coin", "szi", "entryPx", "liquidationPx", "unrealizedPnl", "leverage")}
                          for item in positions],
            "orders": [{key: item.get(key) for key in ("oid", "coin", "side", "sz", "limitPx", "isTrigger", "triggerPx", "reduceOnly")}
                       for item in perpetual_orders],
        }


LIVE_FINAL = {"FILLED", "CANCELED", "EXPIRED", "REJECTED", "ACKNOWLEDGED"}
LIVE_UNCERTAIN = {"SUBMITTING", "UNKNOWN", "CANCEL_PENDING", "CANCEL_UNKNOWN"}


class HyperliquidTrading(HyperliquidReadOnly):
    """Native Hyperliquid testnet execution with an in-memory delegated signer."""

    NETWORK = "testnet"
    EXCHANGE_URL = "https://api.hyperliquid-testnet.xyz/exchange"
    AGENT_FIELDS = [
        {"name": "hyperliquidChain", "type": "string"},
        {"name": "agentAddress", "type": "address"},
        {"name": "agentName", "type": "string"},
        {"name": "nonce", "type": "uint64"},
    ]

    def __init__(self):
        self.lock = threading.RLock()
        self.owner = ""
        self.agent = None
        self.approvals = {}
        self.last_nonce = 0

    def _nonce(self):
        self.last_nonce = max(time.time_ns() // 1_000_000, self.last_nonce + 1)
        return self.last_nonce

    def load(self):
        with self.lock:
            # LiveExecution uses the stable account identity for its order ledger.
            return {"api_key": self.owner, "api_secret": self.agent is not None}

    def status(self):
        with self.lock:
            return {"network": self.NETWORK, "address": self.owner or None,
                    "agent": self.agent.address if self.agent else None}

    def prepare_agent(self, address, chain_id):
        with self.lock:
            if self.agent:
                raise ValueError("请先停用当前测试网代理")
            now = time.monotonic()
            self.approvals = {key: item for key, item in self.approvals.items() if item[0] > now}
            if len(self.approvals) >= 8:
                raise ValueError("钱包授权请求过多，请稍后再试")
            agent = Account.create()
            token = uuid4().hex
            action = {"type": "approveAgent", "signatureChainId": hex(chain_id),
                      "hyperliquidChain": "Testnet", "agentAddress": agent.address,
                      "agentName": "BTCQuantTest", "nonce": self._nonce()}
            typed = user_signed_payload("HyperliquidTransaction:ApproveAgent", self.AGENT_FIELDS, action)
            typed["message"] = {field["name"]: action[field["name"]] for field in self.AGENT_FIELDS}
            self.approvals[token] = (now + 90, address.lower(), agent, action, typed)
            return {"token": token, "typed_data": typed}

    def enable_agent(self, token, signature):
        with self.lock:
            item = self.approvals.pop(token, None)
            if self.agent or not item or item[0] <= time.monotonic():
                raise ValueError("授权请求已失效，请重新申请")
            _, owner, agent, action, typed = item
            try:
                signed = encode_typed_data(full_message=typed)
                recovered = Account.recover_message(signed, signature=signature)
            except (ValueError, TypeError, BadSignature) as exc:
                raise ValueError("钱包签名格式无效") from exc
            if recovered.lower() != owner:
                raise ValueError("签名账户与授权请求不一致")
            raw = bytes.fromhex(signature[2:])
            v = raw[64] + 27 if raw[64] in (0, 1) else raw[64]
            if v not in (27, 28):
                raise ValueError("钱包签名恢复标记无效")
            result = self._exchange(action, action["nonce"], {
                "r": hex(int.from_bytes(raw[:32], "big")),
                "s": hex(int.from_bytes(raw[32:64], "big")), "v": v,
            })
            if result.get("type") != "default":
                raise ExchangeRequestError("Hyperliquid 授权结果未确认", uncertain=True)
            self.approvals.clear()
            self.owner, self.agent = owner, agent
            return self.status()

    def disable_agent(self):
        with self.lock:
            self.owner, self.agent = "", None
            self.approvals.clear()
            return self.status()

    def _exchange(self, action, nonce, signature, expires=None):
        payload = {"action": action, "nonce": nonce, "signature": signature, "vaultAddress": None}
        if expires is not None:
            payload["expiresAfter"] = expires
        try:
            response = requests.post(self.EXCHANGE_URL, json=payload, timeout=8, allow_redirects=False)
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ExchangeRequestError("Hyperliquid 响应未确认，请核对交易所状态", uncertain=True) from exc
        if response.status_code != 200 or not isinstance(body, dict):
            raise ExchangeRequestError("Hyperliquid 请求结果未确认", uncertain=True)
        if body.get("status") == "err" and isinstance(body.get("response"), str):
            raise ExchangeRequestError("Hyperliquid 拒绝请求：" + body["response"][:160])
        if body.get("status") != "ok" or not isinstance(body.get("response"), dict):
            raise ExchangeRequestError("Hyperliquid 请求结果未确认", uncertain=True)
        return body["response"]

    def trade(self, action):
        with self.lock:
            if not self.agent:
                raise ValueError("请先授权测试网交易代理")
            nonce = self._nonce()
            expires = nonce + 20_000
            signature = sign_l1_action(self.agent, action, None, nonce, expires, False)
            return self._exchange(action, nonce, signature, expires)

    def contract(self, coin):
        meta, contexts = self._info(self.NETWORK, "metaAndAssetCtxs")
        for index, item in enumerate(meta["universe"]):
            if item["name"] == coin and not item.get("isDelisted"):
                mark = Decimal(contexts[index]["markPx"])
                decimals = int(item["szDecimals"])
                if mark.is_finite() and mark > 0 and 0 <= decimals <= 6:
                    return index, decimals, mark
        raise ValueError("测试网暂无此合约或报价不可用")

    @staticmethod
    def price_tick(price, decimals):
        return min(Decimal(1), max(Decimal(1).scaleb(-(6 - decimals)), Decimal(1).scaleb(price.adjusted() - 4)))

    def exposure(self, coin):
        state = self._info(self.NETWORK, "clearinghouseState", user=self.owner)
        positions = [item["position"] for item in state["assetPositions"] if item["position"]["coin"] == coin]
        if len(positions) > 1:
            raise ValueError("无法确认该合约的单向仓位")
        return state, positions[0] if positions else None

    def orders_for(self, coin):
        rows = self._info(self.NETWORK, "frontendOpenOrders", user=self.owner)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("Hyperliquid 返回的委托列表无法核对")
        return [row for row in rows if row.get("coin") == coin]

    def order_spec(self, row):
        if row["dex_owner"] != self.owner:
            raise ValueError("钱包账户已变化，请重新连接")
        coin = row["symbol"][:-4]
        asset, decimals, mark = self.contract(coin)
        _, position = self.exposure(coin)
        size = Decimal(position["szi"]) if position else Decimal(0)
        quantity = Decimal(row["quantity"])
        is_buy = row["side"] == "BUY"
        reduce = row["action"] == "CLOSE"
        if quantity != quantity.quantize(Decimal(1).scaleb(-decimals)):
            raise ValueError(f"该合约数量最多支持 {decimals} 位小数")
        if reduce and (not size or (size > 0) == is_buy or quantity > abs(size)):
            raise ValueError("可平仓数量不足，或持仓方向已经变化")
        if not reduce and size and (size > 0) != is_buy:
            raise ValueError("当前为单向持仓，请先平掉反向仓位")
        if row["type"] == "MARKET":
            if "execution_price" not in row:
                bound = mark * (Decimal("1.005") if is_buy else Decimal("0.995"))
                rounding = ROUND_DOWN if is_buy else ROUND_UP
                row["execution_price"] = format(bound.quantize(self.price_tick(bound, decimals), rounding=rounding), "f")
            price, tif = Decimal(row["execution_price"]), "Ioc"
        else:
            price, tif = Decimal(row["price"]), "Gtc"
        if price <= 0 or price != price.quantize(self.price_tick(price, decimals)):
            raise ValueError("价格精度不符合 Hyperliquid 合约规则")
        if not reduce and quantity * price < 10:
            raise ValueError("开仓委托名义价值须至少 10 USD")
        wire = {"a": asset, "b": is_buy, "p": self.wire_number(price),
                "s": self.wire_number(quantity), "r": reduce, "t": {"limit": {"tif": tif}},
                "c": "0x" + row["id"][2:]}
        if row.get("stop_price") and not reduce:
            if size:
                raise ValueError("AUTO 开仓前要求该合约为空仓")
            trigger = Decimal(row["stop_price"])
            if trigger <= 0 or (trigger >= mark if is_buy else trigger <= mark):
                raise ValueError("初始止损已经触发或无效")
            bound = trigger * (Decimal("0.9") if is_buy else Decimal("1.1"))
            stop_limit = bound.quantize(self.price_tick(bound, decimals), rounding=ROUND_DOWN if is_buy else ROUND_UP)
            stop = {"a": asset, "b": not is_buy, "p": self.wire_number(stop_limit),
                    "s": wire["s"], "r": True, "c": self.stop_cloid(row["id"]),
                    "t": {"trigger": {"isMarket": True, "triggerPx": self.wire_number(trigger), "tpsl": "sl"}}}
            return {"type": "order", "orders": [wire, stop], "grouping": "normalTpsl"}
        return {"type": "order", "orders": [wire], "grouping": "na"}

    @staticmethod
    def stop_cloid(order_id):
        return "0x" + hashlib.sha256((order_id + ":stop").encode()).hexdigest()[:32]

    @staticmethod
    def wire_number(number):
        return format(number.normalize(), "f")

    @staticmethod
    def _statuses(response, kind):
        statuses = response.get("data", {}).get("statuses")
        if response.get("type") != kind or not isinstance(statuses, list) or not statuses:
            raise ExchangeRequestError("Hyperliquid 回报未确认，请先查单", uncertain=True)
        return statuses

    def accepted(self, row, response):
        statuses = self._statuses(response, "order")
        result = statuses[0]
        if isinstance(result, dict):
            if "error" in result:
                raise ExchangeRequestError("Hyperliquid 拒绝订单：" + str(result["error"])[:160])
            if (fill := result.get("filled")) and fill.get("oid") is not None:
                filled = Decimal(fill["totalSz"])
                status = "FILLED" if filled == Decimal(row["quantity"]) else "EXPIRED"
                return {"order_id": str(fill["oid"]), "status": status,
                        "filled": str(filled), "average_price": fill["avgPx"],
                        "protection_pending": bool(row.get("stop_price"))}
            if (resting := result.get("resting")) and resting.get("oid") is not None:
                return {"order_id": str(resting["oid"]), "status": "NEW", "filled": "0"}
        raise ExchangeRequestError("Hyperliquid 回报未确认，请先查单", uncertain=True)

    def query_order(self, row):
        cloid = "0x" + row["id"][2:]
        raw = self._info(self.NETWORK, "orderStatus", user=self.owner, oid=cloid)
        if raw.get("status") != "order":
            raise ExchangeRequestError("暂未查到该订单，请继续核对，勿重复提交", uncertain=True)
        result = raw["order"]
        order = result["order"]
        if order.get("cloid") != cloid or order.get("coin") != row["symbol"][:-4]:
            raise ExchangeRequestError("Hyperliquid 返回的订单身份不一致", uncertain=True)
        state = result["status"]
        filled = Decimal(order["origSz"]) - Decimal(order["sz"])
        if state == "filled":
            status = "FILLED"
        elif state in {"open", "triggered"}:
            status = "PARTIALLY_FILLED" if filled else "NEW"
        elif state.lower().endswith("canceled"):
            status = "CANCELED"
        elif state.lower().endswith("rejected"):
            status = "REJECTED"
        else:
            raise ExchangeRequestError("Hyperliquid 返回未知订单状态", uncertain=True)
        return {"order_id": str(order["oid"]), "status": status, "filled": str(filled)}

    def cancel_order(self, row):
        asset, _, _ = self.contract(row["symbol"][:-4])
        result = self.trade({"type": "cancelByCloid", "cancels": [{"asset": asset, "cloid": "0x" + row["id"][2:]}]})
        if self._statuses(result, "cancel")[0] != "success":
            raise ExchangeRequestError("撤单结果待核对，请先查单", uncertain=True)
        return {"status": "CANCELED"}








class LivePreflight:
    MANUAL_LEVERAGE = 100
    AUTO_LEVERAGE = 10

    def __init__(self, risk=None):
        self.cache = {}
        self.lock = threading.Lock()
        self.risk = risk

    @staticmethod
    def public(url, params=None):
        try:
            response = requests.get(url, params=params, timeout=5, allow_redirects=False)
            if response.status_code == 200:
                return response.json()
        except (ValueError, requests.RequestException):
            pass
        raise ValueError("无法读取交易所行情或交易规则")

    def rules(self, exchange, market, symbol):
        key = exchange, market, symbol
        with self.lock:
            previous = self.cache.get(key)
            if previous and time.monotonic() - previous[0] < 60:
                return previous[1]
        result = self._rules(exchange, market, symbol)
        with self.lock:
            self.cache[key] = (time.monotonic(), result)
        return result

    def _rules(self, exchange, market, symbol):
        if exchange == "binance":
            spot = market == "spot"
            url = "https://api.binance.com/api/v3" if spot else "https://fapi.binance.com/fapi/v1"
            data = self.public(url + "/exchangeInfo", {"symbol": symbol} if spot else None)
            pair = next((s for s in data.get("symbols", []) if s["symbol"] == symbol), None)
            if not pair or pair["status"] != "TRADING" or spot and pair.get("isSpotTradingAllowed") is False:
                raise ValueError("Binance 该交易对当前不可交易")
            filters = {f["filterType"]: f for f in pair["filters"]}
            return {"filters": filters, "quote_precision": pair.get("quoteAssetPrecision"),
                    "quote_market": pair.get("quoteOrderQtyMarketAllowed", True)}
        if market == "spot":
            raw = self.public("https://api.coinw.com/api/v1/public", {"command": "returnSymbol"})
            pair = next((s for s in raw.get("data", []) if s["currencyPair"] == symbol[:-4] + "_USDT"), None)
            if not pair or int(pair["state"]) != 1:
                raise ValueError("CoinW 该交易对当前不可交易")
            return {"coinw_spot": pair}
        raw = self.public("https://api.coinw.com/v1/perpum/instruments", {"name": symbol[:-4]})
        pair = next((s for s in raw.get("data", []) if s["base"].upper() == symbol[:-4] and s["quote"].upper() == "USDT"), None)
        if not pair or pair["status"] != "online":
            raise ValueError("CoinW 该合约当前不可交易")
        return {"coinw_futures": pair}

    @staticmethod
    def check_range(value, name, minimum="0", maximum="0", step="0"):
        low, high, unit = map(lambda x: Decimal(str(x)), (minimum, maximum, step))
        if low and value < low:
            raise ValueError(f"{name}低于交易所下限 {low:f}")
        if high and value > high:
            raise ValueError(f"{name}超过交易所上限 {high:f}")
        if unit and value % unit:
            raise ValueError(f"{name}须按 {unit:f} 的步长填写")

    @staticmethod
    def binance_notional_limits(filters, *, limit, market, closing=False):
        for name in ("MIN_NOTIONAL", "NOTIONAL"):
            f = filters.get(name, {})
            apply_min = limit or market == "futures" or f.get("applyToMarket", f.get("applyMinToMarket", False))
            low = f.get("minNotional", f.get("notional", "0")) if apply_min and not closing else "0"
            high = f.get("maxNotional", "0") if limit or f.get("applyMaxToMarket") else "0"
            yield Decimal(low), Decimal(high)

    @classmethod
    def binance_spot_ticket_rules(cls, rules):
        filters = rules["filters"]
        lot = filters["LOT_SIZE"]
        market_lot = filters.get("MARKET_LOT_SIZE", {})
        steps = [Decimal(f.get("stepSize", "0")) for f in (lot, market_lot)]
        scale = max(0, *(-step.as_tuple().exponent for step in steps))
        integers = [int(step.scaleb(scale)) for step in steps if step]
        market_step = Decimal(math.lcm(*integers)).scaleb(-scale) if integers else Decimal(0)
        min_market = max(Decimal(f.get("minQty", "0")) for f in (lot, market_lot))
        max_market = min((Decimal(f["maxQty"]) for f in (lot, market_lot)
                          if Decimal(f.get("maxQty", "0")) > 0), default=Decimal(0))
        result = {
            "market_step": format(market_step, "f"), "market_min_qty": str(min_market),
            "market_max_qty": str(max_market), "price_filter": filters.get("PRICE_FILTER", {}),
            "quote_market": bool(rules["quote_market"] and rules["quote_precision"] is not None),
            "quote_step": str(Decimal(1).scaleb(-int(rules["quote_precision"]))) if rules["quote_precision"] is not None else None,
        }
        for kind, limit in (("market", False), ("limit", True)):
            bounds = list(cls.binance_notional_limits(filters, limit=limit, market="spot"))
            result[f"{kind}_min_notional"] = str(max(low for low, _ in bounds))
            result[f"{kind}_max_notional"] = str(min((high for _, high in bounds if high > 0), default=Decimal(0)))
        return result

    def leverage(self, connector, exchange, symbol, *, notional=None):
        local_cap = self.MANUAL_LEVERAGE
        if self.risk:
            try:
                local_cap = min(local_cap, int(self.risk.load()["max_leverage"]))
            except (KeyError, TypeError, ValueError):
                pass
        if exchange == "coinw":
            pair = self.rules(exchange, "futures", symbol)["coinw_futures"]
            venue = int(pair["maxLeverage"])
            return {"venue_max": venue, "minimum": int(pair.get("minLeverage", 1)),
                    "manual_max": min(venue, local_cap), "current": None}
        config = connector.signed("GET", "/fapi/v1/symbolConfig", {"symbol": symbol})
        row = next((entry for entry in config if entry["symbol"] == symbol), None)
        raw = connector.signed("GET", "/fapi/v1/leverageBracket", {"symbol": symbol})
        records = raw if isinstance(raw, list) else [raw]
        brackets = next((entry["brackets"] for entry in records if entry["symbol"] == symbol), [])
        if not row or not brackets:
            raise ValueError("无法读取 Binance 当前杠杆与名义价值分档")
        if notional is not None:
            existing = connector.signed("GET", "/fapi/v3/positionRisk", {"symbol": symbol})
            pending = connector.signed("GET", "/fapi/v1/openOrders", {"symbol": symbol})
            exposure = sum((abs(Decimal(p["notional"])) for p in existing if p["symbol"] == symbol), Decimal(0))
            for order in pending:
                if order.get("reduceOnly") is True:
                    continue
                price = Decimal(order["price"])
                if price <= 0:
                    raise ValueError("存在无法估价的合约挂单，请先核对")
                exposure += price * (Decimal(order["origQty"]) - Decimal(order["executedQty"]))
            total = exposure + notional
            brackets = [b for b in brackets if Decimal(str(b["notionalFloor"])) <= total < Decimal(str(b["notionalCap"]))]
            if not brackets or total > Decimal(row["maxNotionalValue"]):
                raise ValueError("合约累计名义价值超过当前杠杆的交易所分档")
        venue = max(int(item["initialLeverage"]) for item in brackets)
        return {"venue_max": venue, "minimum": 1, "manual_max": min(venue, local_cap),
                "auto_max": min(venue, self.AUTO_LEVERAGE, local_cap), "current": int(row["leverage"])}

    def account(self, connector, exchange, market):
        snapshot = connector.snapshot()
        if not snapshot.get("configured") or snapshot.get("error"):
            return {**snapshot, "trade_permission": "unavailable"}
        account = snapshot.get("account") or {}
        if exchange != "binance":
            return {**snapshot, "trade_permission": "unknown"}
        can_trade = snapshot.get("can_trade", account.get("can_trade"))
        if can_trade is False:
            return {**snapshot, "trade_permission": "denied"}
        try:
            permissions = connector.key_permissions()
        except ValueError as exc:
            return {**snapshot, "trade_permission": "unknown", "permission_error": str(exc)}
        field = "enableFutures" if market == "futures" else "enableSpotAndMarginTrading"
        key_allowed = permissions.get(field)
        permission = ("confirmed" if can_trade is True and key_allowed is True
                      else "denied" if can_trade is False or key_allowed is False else "unknown")
        return {**snapshot, "trade_permission": permission}

    def ensure_trade(self, connector, exchange, market):
        snapshot = self.account(connector, exchange, market)
        if snapshot.get("error"):
            raise ValueError(snapshot["error"])
        status = snapshot["trade_permission"]
        if status == "denied":
            raise ValueError("读取成功，但账户或 API Key 没有此市场的交易权限")
        if status != "confirmed" and not (exchange == "coinw" and status == "unknown"):
            raise ValueError(snapshot.get("permission_error") or "无法确认此市场的 API 交易权限")

    def validate(self, row, connector):
        exchange, market, symbol = (row[k] for k in ("exchange", "market", "symbol"))
        rules = self.rules(exchange, market, symbol)
        limit = row["type"] == "LIMIT"
        qty, price, quote = (Decimal(row[k]) if row.get(k) else None for k in ("quantity", "price", "quote"))
        closing = row.get("action") == "CLOSE"
        if exchange == "coinw":
            if market == "futures":
                pair = rules["coinw_futures"]
                lot = Decimal(str(pair["oneLotSize"]))
                contracts = qty if closing else qty / lot
                self.check_range(contracts, "合约张数", pair["minSize"] if not closing else "0",
                                 pair["oneMaxPosition"] if not closing else "0", "1")
                if not closing:
                    caps = self.leverage(connector, exchange, symbol)
                    self.check_range(Decimal(row["leverage"]), "杠杆", caps["minimum"], caps["manual_max"])
            else:
                pair = rules["coinw_spot"]
                if qty is not None:
                    self.check_range(qty, "数量", pair.get("minBuyCount", "0") if row["side"] == "BUY" else "0",
                                     pair.get("maxBuyCount", "0") if row["side"] == "BUY" else "0",
                                     Decimal(1).scaleb(-int(pair["countPrecision"])))
                if row["side"] == "BUY" and quote is None:
                    quote = qty * price if limit else qty * Decimal(str(self.public(
                        "https://api.coinw.com/api/v1/public", {"command": "returnTicker"}
                    )["data"][symbol[:-4] + "_USDT"]["last"]))
                if quote is not None:
                    self.check_range(quote, "名义价值（USDT）", pair["minBuyAmount"])
            if limit:
                self.check_range(price, "限价", step=Decimal(1).scaleb(-int(pair["pricePrecision"])))
            if self.risk and not closing:
                mark = price
                if mark is None:
                    ticker = self.public("https://api.coinw.com/api/v1/public", {"command": "returnTicker"})
                    mark = Decimal(str(ticker["data"][symbol[:-4] + "_USDT"]["last"]))
                self.risk.check(market=market, notional=float((qty or 0) * mark),
                                leverage=int(row.get("leverage") or 1))
            return
        filters = rules["filters"]
        if limit:
            f = filters["PRICE_FILTER"]
            self.check_range(price, "限价", f["minPrice"], f["maxPrice"], f["tickSize"])
        if quote is not None:
            if not rules["quote_market"] or rules["quote_precision"] is None:
                raise ValueError("该交易对不支持按金额市价买入")
            self.check_range(quote, "买入金额", step=Decimal(1).scaleb(-int(rules["quote_precision"])))
        if not limit:
            if market == "spot":
                # Binance derives base quantity from quoteOrderQty on its side.
                price = Decimal(self.public("https://api.binance.com/api/v3/avgPrice", {"symbol": symbol})["price"])
            else:
                price = Decimal(self.public("https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": symbol})["markPrice"])
        if not price.is_finite() or price <= 0:
            raise ValueError("无法确认该交易对的参考价")
        lots = [filters["LOT_SIZE"]]
        if not limit and "MARKET_LOT_SIZE" in filters:
            lots.append(filters["MARKET_LOT_SIZE"])
        for f in lots:
            self.check_range(qty if qty is not None else quote / price, "数量",
                             f["minQty"], f["maxQty"], f["stepSize"] if qty is not None else "0")
        notional = quote if quote is not None else qty * price
        for low, high in self.binance_notional_limits(filters, limit=limit, market=market, closing=closing):
            self.check_range(notional, "名义价值（USDT）", low, high)
        if market == "futures" and not closing:
            caps = self.leverage(connector, exchange, symbol, notional=notional)
            allowed = caps["auto_max" if row.get("source") == "auto" else "manual_max"]
            if caps["current"] > allowed:
                raise ValueError(f"Binance 当前杠杆 {caps['current']}× 超过此订单的有效上限 {allowed}×")
        if self.risk and not closing:
            self.risk.check(market=market, notional=float(notional),
                            leverage=int(row.get("leverage") or 1))


class LiveExecution:
    """Durable order intents for mainnet; never uses simulator balances or fills."""

    def __init__(self, root: Path, connections: dict, *, preflight=None):
        self.path = root / "runtime" / "live_orders.sqlite3"
        self.connections = connections
        self.lock = threading.RLock()
        self.previews = {}
        self.preflight = preflight

    def _database(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=8)
        db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY, account TEXT NOT NULL, exchange TEXT NOT NULL,
                market TEXT NOT NULL, symbol TEXT NOT NULL, created INTEGER NOT NULL,
                status TEXT NOT NULL, record TEXT NOT NULL
            )
        """)
        return db

    def _context(self, exchange, market):
        connector = self.connections[exchange, market]
        keys = connector.load()
        if not keys.get("api_key") or not keys.get("api_secret"):
            raise ValueError("请先配置当前市场的 API 连接")
        identity = hashlib.sha256(keys["api_key"].encode()).hexdigest()
        return connector, identity




    def _new_client_id(self, row, account):
        return row["id"]

    def _lookup(self, order_id):
        with closing(self._database()) as db:
            result = db.execute("SELECT account, record FROM orders WHERE id=?", (order_id,)).fetchone()
        if result is None:
            return None
        return result[0], json.loads(result[1])

    def _write(self, row):
        row["updated_at"] = _now_iso()
        with closing(self._database()) as db, db:
            db.execute(
                "UPDATE orders SET status=?, record=? WHERE id=?",
                (row["status"], json.dumps(row, ensure_ascii=False), row["id"]),
            )
        return row

    def read_only_orders(self, exchange, market, symbol, account=None):
        """Read persisted orders without creating or writing the ledger.

        ``sqlite3`` is opened with ``mode=ro`` and the parent directory is never
        created, so calling this on a fresh install leaves no file behind.
        """
        result = {"readable": False, "rows": [], "fetched_at": _now_iso()}
        path = self.path
        if account and path.exists():
            placeholders = []
            params: list[Any] = []
            if account:
                placeholders.append("account=?")
                params.append(account)
            placeholders.extend(["exchange=?", "market=?", "symbol=?"])
            params.extend([exchange, market, symbol])
            try:
                uri = f"file:{path.as_posix()}?mode=ro"
                with closing(sqlite3.connect(uri, uri=True, timeout=4)) as db:
                    rows = db.execute(
                        "SELECT record FROM orders WHERE " + " AND ".join(placeholders),
                        tuple(params),
                    ).fetchall()
                result["readable"] = True
                result["rows"] = [json.loads(record) for record, in rows]
            except (sqlite3.Error, ValueError):
                result["readable"] = False
                result["rows"] = []
        return result

    def read_only_uncertain_symbols(self, exchange, market, account=None):
        """Sorted distinct symbols with a LIVE_UNCERTAIN order; read-only."""
        if not account or not self.path.exists():
            return []
        attention = tuple(LIVE_UNCERTAIN)
        placeholders = ",".join("?" for _ in attention)
        try:
            uri = f"file:{self.path.as_posix()}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=4)) as db:
                rows = db.execute(
                    "SELECT DISTINCT symbol FROM orders WHERE account=? AND exchange=? AND market=?"
                    f" AND status IN ({placeholders})",
                    (account, exchange, market, *attention),
                ).fetchall()
            return sorted({str(symbol) for symbol, in rows if symbol})
        except (sqlite3.Error, ValueError):
            return []

    @staticmethod
    def _require_reconciled(db, account, row):
        uncertain = tuple(LIVE_UNCERTAIN)
        placeholders = ",".join("?" for _ in uncertain)
        if db.execute(
            "SELECT 1 FROM orders WHERE account=? AND exchange=? AND market=? AND symbol=?"
            f" AND status IN ({placeholders}) LIMIT 1",
            (account, row["exchange"], row["market"], row["symbol"], *uncertain),
        ).fetchone():
            raise ValueError("该交易对还有状态待核对的订单，请先查单")

    @staticmethod
    def _number(value):
        return format(Decimal(str(value)), "f")

    def _request_spec(self, row, connector):
        exchange, market, symbol = (row[k] for k in ("exchange", "market", "symbol"))
        if exchange == "hyperliquid":
            return connector.order_spec(row)
        if self.preflight:
            try:
                self.preflight.validate(row, connector)
                self.preflight.ensure_trade(connector, exchange, market)
            except (KeyError, TypeError, ArithmeticError) as exc:
                raise ValueError("交易所规格或账户数据不完整，已停止预览与送单") from exc
        limit = row["type"] == "LIMIT"
        quantity = row.get("quantity")
        quote = row.get("quote")
        price = row.get("price")
        if market == "spot":
            if exchange == "binance":
                params = dict(symbol=symbol, side=row["side"], type=row["type"],
                              newClientOrderId=row.get("client_order_id", row["id"]), newOrderRespType="FULL")
                if limit:
                    params.update(timeInForce="GTC", price=price)
                params.update({"quoteOrderQty": quote} if quote else {"quantity": quantity})
                return "POST", "/api/v3/order", params
            params = dict(symbol=symbol[:-4]+"_USDT", type="0" if row["side"]=="BUY" else "1",
                          isMarket=str(not limit).lower(), out_trade_no=row["id"])
            params.update({"funds": quote} if quote else {"amount": quantity})
            if limit:
                params["rate"] = price
            return "POST", "doTrade", params

        action, position = row["action"], row["position"]
        if exchange == "coinw":
            if action == "CLOSE":
                positions = connector.positions([symbol[:-4]])
                match = next((p for p in positions if p["id"]==row["position_id"] and p["side"]==position), None)
                if not match or Decimal(str(match["qty"])) < Decimal(quantity):
                    raise ValueError("交易所仓位已变化，请刷新后重新确认平仓数量")
                params = {"id": row["position_id"], "closeNum": quantity, "positionType": "plan" if limit else "execute"}
                if limit:
                    params["orderPrice"] = price
                return "DELETE", "/v1/perpum/positions", params
            params = dict(instrument=symbol[:-4], direction=position.lower(), quantityUnit=2,
                          quantity=quantity, leverage=row["leverage"], positionModel=int(row["cross"]),
                          positionType="plan" if limit else "execute", thirdOrderId=row["id"])
            if limit:
                params["openPrice"] = price
            return "POST", "/v1/perpum/order", params

        position_mode = connector.signed("GET", "/fapi/v1/positionSide/dual")
        hedged = position_mode.get("dualSidePosition")
        if not isinstance(hedged, bool):
            raise ValueError("无法确认 Binance 合约持仓模式")
        current = connector.signed("GET", "/fapi/v3/positionRisk", {"symbol": symbol})
        if not isinstance(current, list):
            raise ValueError("无法确认 Binance 合约仓位")
        sides = {}
        for p in current:
            if p.get("symbol") == symbol:
                amount = Decimal(p["positionAmt"])
                side = p.get("positionSide")
                if side == "BOTH":
                    side = "LONG" if amount > 0 else "SHORT"
                sides[side] = abs(amount)
        if action == "CLOSE" and sides.get(position, Decimal(0)) < Decimal(quantity):
            raise ValueError("可平数量不足，请刷新真实仓位")
        opposite = "SHORT" if position == "LONG" else "LONG"
        if action == "OPEN" and not hedged and sides.get(opposite, 0) > 0:
            raise ValueError("Binance 单向模式已有反向仓位，请先平仓或在交易所切换为双向模式")
        params = dict(symbol=symbol, side=row["side"], type=row["type"], quantity=quantity,
                      newClientOrderId=row.get("client_order_id", row["id"]), newOrderRespType="RESULT")
        if hedged:
            params["positionSide"] = position
        elif action == "CLOSE":
            params["reduceOnly"] = "true"
        else:
            # A concurrent external order could turn a one-way "open" into a
            # reduction. Hedge mode encodes the requested intent at the exchange.
            raise ValueError("从终端开仓需要先在 Binance 合约启用双向持仓模式")
        if limit:
            params.update(timeInForce="GTC", price=price)
        return "POST", "/fapi/v1/order", params

    def _send(self, row, connector, request):
        if row["exchange"] == "hyperliquid":
            return connector.trade(request)
        method, path, params = request
        if row["exchange"] == "binance":
            return connector.signed(method, path, params)
        if row["market"] == "spot":
            return connector.signed(path, params)
        return connector._request(method, path, params)

    @staticmethod
    def _binance_result(row, data):
        if not isinstance(data, dict) or not data.get("orderId") or not isinstance(data.get("status"), str):
            raise ExchangeRequestError("缺少交易所订单状态，请查询确认", uncertain=True)
        if data.get("symbol") != row["symbol"]:
            raise ExchangeRequestError("交易所返回的交易对不一致", uncertain=True)
        known_id = row.get("order_id")
        matches = (str(data["orderId"]) == known_id if known_id
                   else data.get("origClientOrderId", data.get("clientOrderId")) == row.get("client_order_id", row["id"]))
        if not matches:
            raise ExchangeRequestError("交易所返回的订单号不一致", uncertain=True)
        status = {"EXPIRED_IN_MATCH":"EXPIRED", "PENDING_CANCEL":"CANCEL_PENDING",
                  "PENDING_NEW":"ACCEPTED"}.get(data["status"], data["status"])
        if status not in LIVE_FINAL | {"NEW", "ACCEPTED", "PARTIALLY_FILLED", "CANCEL_PENDING"}:
            raise ExchangeRequestError("交易所返回未知订单状态，请查询确认", uncertain=True)
        filled = data.get("executedQty")
        quote = data.get("cummulativeQuoteQty", data.get("cumulativeQuoteQty", data.get("cumQuote")))
        return {
            "order_id": str(data["orderId"]), "status": status,
            "filled": None if filled is None else str(filled),
            "quote_filled": None if quote is None else str(quote), "average_price": data.get("avgPrice"),
        }

    def _accepted(self, row, result):
        if row["exchange"] == "hyperliquid":
            return self.connections["hyperliquid", "futures"].accepted(row, result)
        if row["exchange"] == "binance":
            return self._binance_result(row, result)
        field = "orderNumber" if row["market"] == "spot" else "value"
        order_id = result.get(field) if isinstance(result, dict) else None
        if order_id is None or not str(order_id).isdigit():
            raise ExchangeRequestError("CoinW 缺少订单编号，请到交易所核对", uncertain=True)
        return {"order_id": str(order_id), "status": "ACCEPTED"}

    def preview(self, intent):
        with self.lock:
            connector, account = self._context(intent["exchange"], intent["market"])
            with closing(self._database()) as db:
                self._require_reconciled(db, account, intent)
            now = time.monotonic()
            self.previews = {k: v for k, v in self.previews.items() if v[0] > now}
            if len(self.previews) >= 40:
                raise ValueError("等待确认的操作太多，请稍后重试")
            if intent["market"] == "futures":
                long = intent["position"] == "LONG"
                intent["side"] = "BUY" if (long == (intent["action"] == "OPEN")) else "SELL"
            for key in ("quantity", "price", "quote"):
                if intent.get(key) is not None:
                    intent[key] = self._number(intent[key])
            order_id = "bq" + uuid4().hex
            row = {**intent, "id": order_id, "status": "SUBMITTING", "created_at": _now_iso(), "source": "manual"}
            with connector.lock:
                client_id = self._new_client_id(row, account)
                if client_id != order_id:
                    row["client_order_id"] = client_id
                request = self._request_spec(row, connector)
            self.previews[order_id] = (time.monotonic() + 60, account, row, request)
            return {"confirmation": order_id, "expires_in": 60, "order": row}

    def _commit(self, row, account, connector, request):
        with closing(self._database()) as db, db:
            # Serialize the guard and insert, including across SQLite connections.
            db.execute("BEGIN IMMEDIATE")
            self._require_reconciled(db, account, row)
            db.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
                (row["id"], account, row["exchange"], row["market"], row["symbol"],
                 time.time_ns(), row["status"], json.dumps(row, ensure_ascii=False)),
            )
        try:
            row.update(self._accepted(row, self._send(row, connector, request)))
        except ExchangeRequestError as exc:
            row.update(status="UNKNOWN" if exc.uncertain else "REJECTED", message=str(exc))
        return self._write(row)

    def recover_manual(self, confirmation):
        """Return the local receipt, or invalidate the unused preview before reporting no send."""
        with self.lock:
            prior = self._lookup(confirmation)
            entry = self.previews.get(confirmation)
            if prior or entry:
                account, row = prior if prior else (entry[1], entry[2])
                if row.get("source") != "manual" or self._context(row["exchange"], row["market"])[1] != account:
                    raise ValueError("无法核对该确认号，请检查原 API 账户")
                if prior:
                    return {"state": "recorded", "order": row}
                self.previews.pop(confirmation, None)
            # _commit writes the intent before contacting the venue; the submit
            # lock rules out a send between this lookup and preview invalidation.
            return {"state": "not_submitted"}

    def submit(self, confirmation):
        with self.lock:
            prior = self._lookup(confirmation)
            if prior is not None:
                account, row = prior
                if self._context(row["exchange"], row["market"])[1] != account:
                    raise ValueError("API 账户已变更")
                return row
            entry = self.previews.pop(confirmation, None)
            if entry is None or entry[0] <= time.monotonic():
                raise ValueError("订单确认已过期，请重新预览")
            _, account, row, previewed_request = entry
            connector, current = self._context(row["exchange"], row["market"])
            with connector.lock:
                if current != account or self._context(row["exchange"], row["market"])[1] != account:
                    raise ValueError("API 账户已变更，请重新预览")
                request = self._request_spec(row, connector)
                if request != previewed_request:
                    raise ValueError("交易所持仓模式已变化，请重新预览")
                return self._commit(row, account, connector, request)

    def strategy_order(self, intent, order_id, account):
        """Submit a persisted strategy intent once, keyed to its original account."""
        with self.lock:
            connector, current = self._context(intent["exchange"], intent["market"])
            with connector.lock:
                if current != account or self._context(intent["exchange"], intent["market"])[1] != account:
                    raise ValueError("策略的 API 账户已变更")
                previous = self._lookup(order_id)
                if previous is not None:
                    if previous[0] != account:
                        raise ValueError("策略订单属于其他账户")
                    return previous[1]
                row = {**intent, "id":order_id, "created_at":_now_iso(), "status":"SUBMITTING"}
                for key in ("quantity", "price", "quote"):
                    if row.get(key) is not None:
                        row[key] = self._number(row[key])
                if row["market"] == "futures":
                    row["side"] = "BUY" if (row["position"] == "LONG") == (row["action"] == "OPEN") else "SELL"
                client_id = self._new_client_id(row, account)
                if client_id != order_id:
                    row["client_order_id"] = client_id
                request = self._request_spec(row, connector)
                return self._commit(row, account, connector, request)

    def spot_fill_totals(self, row, account):
        connector, current = self._context(row["exchange"], "spot")
        if current != account or not row.get("order_id") or row["status"] != "FILLED":
            raise ValueError("现货订单尚未确认完整成交")
        with connector.lock:
            if self._context(row["exchange"], "spot")[1] != account:
                raise ValueError("策略的 API 账户已变更")
            if row["exchange"] == "coinw":
                item = connector.signed("returnOrderTrades", {"orderNumber":row["order_id"]})
                if not isinstance(item, dict) or str(item.get("tradeID")) != row["order_id"]:
                    raise ValueError("CoinW 成交明细尚未确认")
                qty = Decimal(str(item["success_total"]))
                quote = Decimal(str(item["success_amount"]))
                fee = Decimal(str(item["fee"]))
                if qty <= 0 or quote <= 0 or fee < 0:
                    raise ValueError("CoinW 成交数量或费用未确认")
                # Round the acquired quantity conservatively even if the
                # venue already reports the post-fee amount.
                return (qty, quote-fee) if row["side"] == "SELL" else (qty*(1-fee/quote), quote+fee)
            trades = connector.signed("GET", "/api/v3/myTrades",
                                      {"symbol":row["symbol"], "orderId":row["order_id"], "limit":1000})
            trades = [t for t in trades if str(t["orderId"]) == row["order_id"]]
            qty = sum((Decimal(t["qty"]) for t in trades), Decimal(0))
            if qty <= 0 or not row.get("filled") or qty != Decimal(row["filled"]):
                raise ValueError("Binance 成交明细尚未完整，暂停策略补单")
            quote = sum((Decimal(t["quoteQty"]) for t in trades), Decimal(0))
            base_fee = sum((Decimal(t["commission"]) for t in trades if t["commissionAsset"] == row["symbol"][:-4]), Decimal(0))
            quote_fee = sum((Decimal(t["commission"]) for t in trades if t["commissionAsset"] == "USDT"), Decimal(0))
            return (qty-base_fee, quote+quote_fee) if row["side"] == "BUY" else (qty+base_fee, quote-quote_fee)

    def list_orders(self, exchange, market, symbol):
        try:
            _, account = self._context(exchange, market)
        except ValueError:
            return []
        with closing(self._database()) as db:
            attention = tuple(LIVE_UNCERTAIN)
            placeholders = ",".join("?" for _ in attention)
            rows = db.execute(
                "SELECT record FROM orders WHERE account=? AND exchange=? AND market=? AND symbol=?"
                f" ORDER BY (status IN ({placeholders})) DESC, created DESC LIMIT 60",
                (account, exchange, market, symbol, *attention),
            ).fetchall()
        return [json.loads(record) for record, in rows]

    def _query(self, row, connector):
        if row["exchange"] == "hyperliquid":
            return connector.query_order(row)
        order_id, symbol = row.get("order_id"), row["symbol"]
        if row["exchange"] == "binance":
            path = "/api/v3/order" if row["market"] == "spot" else "/fapi/v1/order"
            identity = {"orderId":order_id} if order_id else {"origClientOrderId":row.get("client_order_id", row["id"])}
            raw = connector.signed("GET", path, {"symbol":symbol, **identity})
            return self._binance_result(row, raw)
        if row["market"] == "spot":
            if not order_id:
                raise ValueError("CoinW 尚未返回订单号，请先在交易所核对")
            params = {"orderNumber": order_id}
            raw = connector.signed("returnOrderStatus", params)
            if raw.get("currencyPair") != symbol[:-4]+"_USDT":
                raise ValueError("CoinW 返回的交易对不一致")
            status = {1:"NEW", 2:"PARTIALLY_FILLED", 3:"FILLED", 4:"CANCELED"}.get(int(raw["status"]), "UNKNOWN")
            result = {"status": status}
            if status in {"PARTIALLY_FILLED", "FILLED", "CANCELED"}:
                try:
                    fills = connector.signed("returnOrderTrades", params)
                except ExchangeRequestError:
                    if status == "CANCELED":
                        return result
                    raise
                if isinstance(fills, dict):
                    fills = [fills]
                actual = [x for x in (fills or []) if str(x.get("tradeID")) == order_id]
                if actual:
                    result["filled"] = str(sum(Decimal(str(x["success_total"])) for x in actual))
                    result["quote_filled"] = str(sum(Decimal(str(x["success_amount"])) for x in actual))
            return result

        base = symbol[:-4]
        kind = "plan" if row["type"] == "LIMIT" else "execute"
        if order_id:
            response = connector._request("GET", "/v1/perpum/order",
                                          {"instrument":base, "sourceIds":order_id, "positionType":kind})
            rows = response.get("rows", []) if isinstance(response, dict) else response
            match = next((x for x in (rows or []) if str(x.get("id")) == order_id), None)
            if match:
                return {"status": {"unFinish":"NEW", "part":"PARTIALLY_FILLED"}.get(match.get("orderStatus"), "UNKNOWN")}
        response = connector._request("GET", "/v1/perpum/orders/history",
                                      {"instrument":base, "positionType":kind, "pageSize":50})
        rows = response.get("rows", []) if isinstance(response, dict) else response
        match = next((x for x in (rows or []) if (order_id and str(x.get("id")) == order_id)
                      or x.get("thirdOrderId") == row["id"]), None)
        if not match:
            raise ValueError("CoinW 暂未返回匹配的成交记录，请在交易所核对")
        return {"order_id": str(match.get("id") or order_id),
                "status": {"Finish":"FILLED", "part":"PARTIALLY_FILLED", "Cancel":"CANCELED"}.get(match.get("orderStatus"), "UNKNOWN"),
                "quote_filled": match.get("completeUsdt")}

    def manage(self, order_id, action):
        with self.lock:
            found = self._lookup(order_id)
            if found is None:
                raise ValueError("未找到本终端提交的订单")
            account, row = found
            connector, current = self._context(row["exchange"], row["market"])
            with connector.lock:
                if current != account or self._context(row["exchange"], row["market"])[1] != account:
                    raise ValueError("订单属于其他 API 账户")
                if action == "acknowledge":
                    if row["status"] not in LIVE_UNCERTAIN:
                        raise ValueError("只有状态未确认的订单可以标记为已核对")
                    row.update(status="ACKNOWLEDGED", message="用户已在交易所核对；仅结束本地跟踪")
                    return self._write(row)
                if action == "query":
                    if row["status"] in {"REJECTED", "ACKNOWLEDGED"}:
                        return row
                    row.update(self._query(row, connector), message=None)
                    return self._write(row)
                if row["status"] in LIVE_FINAL:
                    return row
                if row["status"] not in {"NEW", "PARTIALLY_FILLED", "ACCEPTED"}:
                    raise ValueError("请先查询订单最新状态，再尝试撤单")
                return self._cancel(row, connector)

    def _cancel(self, row, connector):
        if row["exchange"] == "hyperliquid":
            self._write(dict(row, status="CANCEL_PENDING"))
            try:
                row.update(connector.cancel_order(row), message=None)
            except ExchangeRequestError as exc:
                row.update(status="CANCEL_UNKNOWN", message=str(exc))
            return self._write(row)
        native = row.get("order_id")
        if row["exchange"] == "binance":
            path = "/api/v3/order" if row["market"] == "spot" else "/fapi/v1/order"
            request = ("DELETE", path, {"symbol":row["symbol"], "origClientOrderId":row.get("client_order_id", row["id"])})
        elif not native:
            raise ValueError("CoinW 订单号尚未确认，请先查单")
        elif row["market"] == "spot":
            request = ("POST", "cancelOrder", {"orderNumber":native})
        else:
            request = ("DELETE", "/v1/perpum/order", {"id":native})
        self._write(dict(row, status="CANCEL_PENDING"))
        try:
            raw = self._send(row, connector, request)
            result = self._binance_result(row, raw) if row["exchange"] == "binance" else {"status":"CANCEL_PENDING"}
            row.update(result, message=None)
        except ExchangeRequestError as exc:
            row.update(status="CANCEL_UNKNOWN" if exc.uncertain else row["status"], message=str(exc))
        return self._write(row)
