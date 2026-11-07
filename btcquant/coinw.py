"""CoinW futures read-only connector.

This module is intentionally read-only.  It can verify a user's CoinW API key
and display futures balances/positions, but it has no order endpoint.  Real
orders must be implemented separately with explicit risk controls.
"""
from __future__ import annotations
from .secret_store import restrict_file

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from .binance_spot import ASSETS, ExchangeRequestError
from urllib.parse import urlencode

import requests


BASE_URL = "https://api.coinw.com"


def _settings_path(root: Path) -> Path:
    return root / "runtime" / "coinw_readonly.json"


def _mask(value: str) -> str:
    value = str(value or "")
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def _int_code(code: Any) -> int | None:
    """Best-effort numeric venue code; never invent one from a message."""
    if isinstance(code, bool):
        return None
    if isinstance(code, int):
        return code
    if isinstance(code, str) and code.lstrip("-").isdigit():
        return int(code)
    return None


class CoinWReadOnly:
    def __init__(self, root: Path):
        self.root = root
        self.path = _settings_path(root)
        self.lock = threading.RLock()

    def load(self) -> dict[str, Any]:
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
        api_key = str(api_key or "").strip()
        api_secret = str(api_secret or "").strip()
        if not api_key or not api_secret:
            raise ValueError("API Key 和 Secret 都不能为空")
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"api_key": api_key, "api_secret": api_secret}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
            # Best effort on Windows; do not rely on this as encryption.
            try:
                restrict_file(self.path)
            except OSError:
                pass

    def clear(self) -> None:
        with self.lock:
            if self.path.exists():
                self.path.unlink()

    def configured(self) -> bool:
        data = self.load()
        return bool(data["api_key"] and data["api_secret"])

    def _request(self, method: str, api_path: str, params: dict[str, Any] | None = None) -> Any:
        data = self.load()
        if not data["api_key"] or not data["api_secret"]:
            raise ValueError("尚未配置 CoinW API")
        params = {k: v for k, v in (params or {}).items() if v is not None}
        timestamp = str(int(time.time() * 1000))
        method = method.upper()
        if method == "GET":
            query = urlencode(params)
            signed = f"{timestamp}{method}{api_path}" + (f"?{query}" if query else "")
        else:
            body = json.dumps(params)
            signed = f"{timestamp}{method}{api_path}{body}"
        signature = base64.b64encode(
            hmac.new(
                data["api_secret"].encode("utf-8"),
                signed.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
        ).decode("ascii")
        headers = {
            "sign": signature,
            "api_key": data["api_key"],
            "timestamp": timestamp,
            "Content-Type": "application/json",
            "User-Agent": "BTCQuantConsole/CoinWReadOnly",
        }
        url = f"{BASE_URL}{api_path}"
        try:
            if method == "GET":
                response = requests.get(url, params=params, headers=headers, timeout=12, allow_redirects=False)
            else:
                response = requests.request(method, url, headers=headers, data=body, timeout=12, allow_redirects=False)
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ExchangeRequestError("CoinW 合约响应未确认，请先查单", uncertain=True) from exc
        if not isinstance(payload, dict):
            raise ExchangeRequestError("CoinW 合约响应格式异常", uncertain=True)
        code = payload.get("code")
        if response.status_code != 200 or code not in (0, "0"):
            message = str(payload.get("msg") or payload.get("message") or "")[:240]
            uncertain = response.status_code >= 500 or response.status_code in (408, 429) or code is None
            raise ExchangeRequestError(
                f"CoinW 合约 {code or response.status_code} {message}".strip(),
                uncertain=uncertain,
                code=_int_code(code),
            )
        return payload.get("data", payload)

    def account(self) -> dict[str, Any]:
        data = self._request("GET", "/v1/perpum/account/getUserAssets", {"quote": "usdt"})
        return {
            "available_margin": float(data.get("availableMargin", 0) or 0),
            "available_usdt": float(data.get("availableUsdt", 0) or 0),
            "margin_used": float(data.get("alMargin", 0) or 0),
            "frozen": float(data.get("alFreeze", 0) or 0),
            "user_id": data.get("userId"),
            "time": data.get("time") or data.get("ts"),
        }

    def positions(self, instruments: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for instrument in instruments:
            try:
                data = self._request(
                    "GET", "/v1/perpum/positions", {"instrument": instrument}
                )
            except ValueError:
                continue
            if isinstance(data, dict):
                data = [data]
            for item in data or []:
                rows.append(
                    {
                        "id": str(item.get("id") or item.get("openId") or ""),
                        "instrument": item.get("instrument"),
                        "side": item.get("direction", "").upper(),
                        "qty": item.get("currentPiece", 0),
                        "entry": float(item.get("openPrice", 0) or 0),
                        "mark": float(item.get("indexPrice", 0) or 0),
                        "liq": float(item.get("liquidationPrice", 0) or 0),
                        "leverage": float(item.get("leverage", 0) or 0),
                        "margin": float(item.get("margin", 0) or 0),
                        "unrealized": float(item.get("profitUnreal", 0) or 0),
                        "mode": "CROSS" if int(item.get("positionModel", 0) or 0) == 1 else "ISOLATED",
                    }
                )
        return rows

    def snapshot(self) -> dict[str, Any]:
        data = self.load()
        if not data["api_key"]:
            return {"configured": False, "account": None, "positions": [], "error": None}
        try:
            account = self.account()
            positions = self.positions(["BTC", "ETH", "BNB", "XRP", "TRX", "DOGE"])
            return {
                "configured": True,
                "account": account,
                "positions": positions,
                "error": None,
            }
        except ValueError as exc:
            return {
                "configured": True,
                "account": None,
                "positions": [],
                "error": str(exc),
                "error_code": getattr(exc, "code", None),
            }

    def public_settings(self) -> dict[str, Any]:
        data = self.load()
        return {
            "configured": bool(data["api_key"] and data["api_secret"]),
            "api_key_masked": _mask(data["api_key"]),
            "read_only": True,
        }


class CoinWSpotReadOnly(CoinWReadOnly):
    """CoinW spot credentials and signing are independent of futures."""

    def __init__(self, root: Path):
        super().__init__(root)
        self.path = root / "runtime" / "coinw_spot_readonly.json"

    def signed(self, command: str, params=None):
        keys = self.load()
        if not keys.get("api_key") or not keys.get("api_secret"):
            raise ExchangeRequestError("请先配置 CoinW 现货 API")
        query = {**(params or {}), "api_key": keys["api_key"]}
        unsigned = "&".join(f"{key}={value}" for key, value in sorted(query.items()))
        unsigned += f"&secret_key={keys['api_secret']}"
        signature = hashlib.md5(unsigned.encode(), usedforsecurity=False).hexdigest().upper()
        try:
            response = requests.post(
                f"{BASE_URL}/api/v1/private",
                params={"command": command, **query, "sign": signature},
                timeout=12, allow_redirects=False,
            )
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ExchangeRequestError("CoinW 现货响应未确认，请先查单", uncertain=True) from exc
        if not isinstance(payload, dict):
            raise ExchangeRequestError("CoinW 现货响应格式异常", uncertain=True)
        code = payload.get("code")
        if response.status_code != 200 or code not in ("200", 200):
            message = str(payload.get("msg") or "")[:240]
            uncertain = response.status_code >= 500 or response.status_code in (408, 429) or code is None
            raise ExchangeRequestError(
                f"CoinW 现货 {code or response.status_code} {message}".strip(),
                uncertain=uncertain,
                code=_int_code(code),
            )
        return payload.get("data")

    def snapshot(self) -> dict[str, Any]:
        if not self.configured():
            return {"configured": False, "balances": [], "error": None}
        try:
            data = self.signed("returnBalances")
            if not isinstance(data, dict):
                raise ValueError("CoinW 现货余额格式异常")
            balances = [{"asset": asset, "free": data[asset]} for asset in ASSETS if asset in data]
            return {"configured": True, "balances": balances, "error": None}
        except ValueError as exc:
            return {
                "configured": True, "balances": [], "error": str(exc),
                "error_code": getattr(exc, "code", None),
            }
