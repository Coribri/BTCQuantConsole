"""Binance Spot Testnet client.

Only signed endpoints needed for a simple spot long-only account are wrapped:
account, market order, open orders and my trades.  The client never uses a
withdrawal endpoint and never logs the API secret.

Docs: https://developers.binance.com/docs/binance-spot-api-docs/testnet
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import pandas as pd
import requests

from .data import INTERVAL_MS, _rows_to_frame, _clean_klines


TESTNET_BASE = "https://testnet.binance.vision"


class TestnetError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, code: int | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class TestnetNotConfigured(TestnetError):
    pass


@dataclass(frozen=True)
class SymbolFilters:
    step_size: float
    min_qty: float
    min_notional: float
    tick_size: float = 0.01

    def round_qty(self, qty: float) -> float:
        return float(
            (Decimal(str(qty)) / Decimal(str(self.step_size))).to_integral_value(
                rounding=ROUND_DOWN
            )
            * Decimal(str(self.step_size))
        )

    def fmt_qty(self, qty: float) -> str:
        value = Decimal(str(self.round_qty(qty))).normalize()
        # Avoid scientific notation for very small quantities.
        return format(value, "f")


def round_down(value: float, step: float) -> float:
    return float(
        (Decimal(str(value)) / Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN)
        * Decimal(str(step))
    )


def sign_query(query: str, secret: str) -> str:
    """Binance's HMAC-SHA256 signature over the exact query string."""
    return hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()


class TestnetAPI:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = TESTNET_BASE,
        timeout: float = 20.0,
        recv_window: int = 5000,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._api_secret = api_secret
        self.timeout = timeout
        self.recv_window = recv_window
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-MBX-APIKEY": api_key,
                "User-Agent": "btc-quant-lab/0.2 testnet",
            }
        )
        self._time_offset_ms = 0

    # ------------------------------------------------------------- transport
    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        signed: bool = False,
    ) -> Any:
        params = dict(params or {})
        if signed:
            params["timestamp"] = self.timestamp_ms()
            params["recvWindow"] = self.recv_window
            query = urlencode(params, doseq=False)
            params["signature"] = sign_query(query, self._api_secret)
        try:
            resp = self.session.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise TestnetError(f"testnet network error: {exc}") from exc
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except ValueError:
                payload = {"msg": resp.text[:500]}
            raise TestnetError(
                f"testnet {method} {path}: {payload.get('msg', 'request failed')}",
                status=resp.status_code,
                code=payload.get("code"),
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise TestnetError(f"invalid JSON from testnet: {resp.text[:200]}") from exc

    def sync_time(self) -> int:
        payload = self._request("GET", "/api/v3/time")
        server_time = int(payload["serverTime"])
        self._time_offset_ms = server_time - int(time.time() * 1000)
        return server_time

    def timestamp_ms(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    # --------------------------------------------------------------- public
    def ping(self) -> bool:
        self._request("GET", "/api/v3/ping")
        return True

    def klines(self, symbol: str, interval: str, limit: int = 400) -> pd.DataFrame:
        if interval not in INTERVAL_MS:
            raise ValueError(f"unsupported interval: {interval}")
        payload = self._request(
            "GET",
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": max(2, min(limit, 1000))},
        )
        if not isinstance(payload, list):
            raise TestnetError("unexpected kline payload")
        frame = _clean_klines(_rows_to_frame(payload), interval)
        if frame.empty:
            raise TestnetError("testnet returned no closed candles")
        return frame

    def price(self, symbol: str) -> float:
        payload = self._request("GET", "/api/v3/ticker/price", {"symbol": symbol})
        try:
            return float(payload["price"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TestnetError("unexpected ticker payload") from exc

    def symbol_filters(self, symbol: str) -> SymbolFilters:
        payload = self._request("GET", "/api/v3/exchangeInfo", {"symbol": symbol})
        symbols = payload.get("symbols") or []
        if not symbols:
            raise TestnetError(f"symbol not found on testnet: {symbol}")
        raw = symbols[0]
        filters = {item["filterType"]: item for item in raw.get("filters", [])}
        lot = filters.get("LOT_SIZE", {})
        notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
        price_filter = filters.get("PRICE_FILTER", {})
        return SymbolFilters(
            step_size=float(lot.get("stepSize", "0.00001")),
            min_qty=float(lot.get("minQty", "0.00001")),
            min_notional=float(notional.get("minNotional", notional.get("notional", "5"))),
            tick_size=float(price_filter.get("tickSize", "0.01")),
        )

    # --------------------------------------------------------------- signed
    def account(self) -> dict[str, Any]:
        payload = self._request("GET", "/api/v3/account", signed=True)
        balances: dict[str, dict[str, float]] = {}
        for item in payload.get("balances", []):
            asset = item.get("asset")
            if not asset:
                continue
            free = float(item.get("free", 0) or 0)
            locked = float(item.get("locked", 0) or 0)
            if free or locked:
                balances[asset] = {"free": free, "locked": locked, "total": free + locked}
        return {
            "balances": balances,
            "account_type": payload.get("accountType"),
            "can_trade": payload.get("canTrade"),
            "can_withdraw": payload.get("canWithdraw"),
            "update_time": payload.get("updateTime"),
        }

    def market_buy(self, symbol: str, quote_amount: float) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": "BUY",
                "type": "MARKET",
                "quoteOrderQty": f"{quote_amount:.2f}",
                "newOrderRespType": "FULL",
            },
            signed=True,
        )

    def market_sell(self, symbol: str, quantity: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": "SELL",
                "type": "MARKET",
                "quantity": quantity,
                "newOrderRespType": "FULL",
            },
            signed=True,
        )

    def open_orders(self, symbol: str) -> list[dict[str, Any]]:
        payload = self._request("GET", "/api/v3/openOrders", {"symbol": symbol}, signed=True)
        return payload if isinstance(payload, list) else []

    def my_trades(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        payload = self._request(
            "GET",
            "/api/v3/myTrades",
            {"symbol": symbol, "limit": max(1, min(limit, 100))},
            signed=True,
        )
        return payload if isinstance(payload, list) else []


def parse_order_fills(order: dict[str, Any]) -> tuple[float, float, float]:
    """Return (filled_qty, average_price, commission_in_quote_asset)."""
    fills = order.get("fills") or []
    qty = 0.0
    quote = 0.0
    fees = 0.0
    for fill in fills:
        q = float(fill.get("qty", 0) or 0)
        p = float(fill.get("price", 0) or 0)
        qty += q
        quote += q * p
        commission = float(fill.get("commission", 0) or 0)
        asset = str(fill.get("commissionAsset", ""))
        # Most BTCUSDT fills pay commission in USDT or BNB.  Value BNB at fill
        # price only as a rough fallback; BNB-specific valuation is not needed
        # for account equity, which comes from balances.
        if asset in {"USDT", "BUSD", "FDUSD"}:
            fees += commission
        elif asset == "BNB":
            fees += commission * p
    avg = quote / qty if qty > 0 else 0.0
    return qty, avg, fees
