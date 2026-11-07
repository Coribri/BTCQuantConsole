"""Binance Spot mainnet account reader and signed request transport."""
from __future__ import annotations
from .secret_store import restrict_file

from .binance_time import server_time_ms

import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time
from typing import Any
from urllib.parse import urlencode

import requests


BASE_URL = "https://api.binance.com"
ASSETS = ("USDT", "BTC", "ETH", "BNB", "XRP", "TRX", "DOGE")


def _mask(value: str) -> str:
    value = str(value or "")
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"


class ExchangeRequestError(ValueError):
    """Exchange failure with an explicit unknown-outcome flag."""

    def __init__(self, message: str, *, uncertain: bool = False, code: int | None = None):
        super().__init__(message)
        self.uncertain = uncertain
        self.code = code


def binance_signed(base: str, keys: dict, method: str, path: str, params=None):
    if not keys.get("api_key") or not keys.get("api_secret"):
        raise ExchangeRequestError("请先配置该交易所的 API 连接")
    query = {**(params or {}), "timestamp": server_time_ms(base), "recvWindow": 10000}
    query["signature"] = hmac.new(keys["api_secret"].encode(), urlencode(query).encode(), hashlib.sha256).hexdigest()
    try:
        response = requests.request(
            method, base + path, params=query, headers={"X-MBX-APIKEY": keys["api_key"]},
            timeout=12, allow_redirects=False,
        )
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        reading = method == "GET"
        raise ExchangeRequestError(
            "Binance 读取超时或响应异常，请检查网络后重试" if reading else "Binance 响应未确认，请先查单",
            uncertain=not reading,
        ) from exc
    code = body.get("code") if isinstance(body, dict) else None
    if response.status_code != 200 or isinstance(code, int) and code < 0:
        message = str(body.get("msg", ""))[:240] if isinstance(body, dict) else ""
        uncertain = (
            response.status_code < 400 or response.status_code >= 500
            or response.status_code in (408, 418, 429) or code in (-1000, -1001, -1006, -1007)
        )
        raise ExchangeRequestError(
            f"Binance {code or response.status_code} {message}".strip(), uncertain=uncertain,
            code=code if isinstance(code, int) else None,
        )
    return body


class BinanceSpotReadOnly:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "runtime" / "binance_readonly.json"
        self.lock = threading.RLock()

    def load(self) -> dict[str, str]:
        with self.lock:
            if not self.path.exists():
                return {"api_key": "", "api_secret": ""}
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                return {
                    "api_key": str(data.get("api_key", "")),
                    "api_secret": str(data.get("api_secret", "")),
                }
            except Exception:
                return {"api_key": "", "api_secret": ""}

    def save(self, api_key: str, api_secret: str) -> None:
        api_key, api_secret = str(api_key or "").strip(), str(api_secret or "").strip()
        if not api_key or not api_secret:
            raise ValueError("API Key 和 Secret 都不能为空")
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"api_key": api_key, "api_secret": api_secret}), encoding="utf-8")
            os.replace(tmp, self.path)
            try:
                restrict_file(self.path)
            except OSError:
                pass

    def clear(self) -> None:
        with self.lock:
            if self.path.exists():
                self.path.unlink()

    def public_settings(self) -> dict[str, Any]:
        data = self.load()
        return {
            "configured": bool(data["api_key"] and data["api_secret"]),
            "api_key_masked": _mask(data["api_key"]),
            "read_only": True,
            "network": "mainnet",
        }

    def signed(self, method: str, path: str, params=None):
        return binance_signed(BASE_URL, self.load(), method, path, params)

    def key_permissions(self):
        return self.signed("GET", "/sapi/v1/account/apiRestrictions")

    def _account(self) -> dict[str, Any]:
        return self.signed("GET", "/api/v3/account")

    def snapshot(self) -> dict[str, Any]:
        keys = self.load()
        if not (keys["api_key"] and keys["api_secret"]):
            return {"configured": False, "balances": [], "error": None}
        try:
            account = self._account()
            balances = []
            for item in account.get("balances", []):
                asset = str(item.get("asset", ""))
                if asset not in ASSETS:
                    continue
                free = float(item.get("free", 0) or 0)
                locked = float(item.get("locked", 0) or 0)
                total = free + locked
                if total > 0 or asset == "USDT":
                    balances.append({"asset": asset, "free": free, "locked": locked, "total": total})
            return {
                "configured": True,
                "account_type": account.get("accountType"),
                "can_trade": bool(account.get("canTrade")),
                "can_withdraw": bool(account.get("canWithdraw")),
                "balances": balances,
                "error": None,
            }
        except ExchangeRequestError as exc:
            return {"configured": True, "balances": [], "error": str(exc), "error_code": exc.code}
        except (TypeError, ValueError, AttributeError):
            return {"configured": True, "balances": [], "error": "Binance 返回的账户数据不完整"}
