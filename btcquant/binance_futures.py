"""Binance USDⓈ-M Futures account reader and signed request transport."""
from __future__ import annotations
from .secret_store import restrict_file

import json
import os
from pathlib import Path
import threading
from typing import Any


from .binance_spot import BASE_URL as SPOT_BASE_URL, binance_signed


BASE_URL = "https://fapi.binance.com"


def _mask(value: str) -> str:
    value = str(value or "")
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"


class BinanceFuturesReadOnly:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "runtime" / "binance_futures_readonly.json"
        self.lock = threading.RLock()

    def load(self) -> dict[str, str]:
        with self.lock:
            if not self.path.exists():
                return {"api_key": "", "api_secret": ""}
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                return {"api_key": str(data.get("api_key", "")), "api_secret": str(data.get("api_secret", ""))}
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
            "market": "futures",
        }

    def signed(self, method: str, path: str, params=None):
        return binance_signed(BASE_URL, self.load(), method, path, params)

    def key_permissions(self):
        return binance_signed(SPOT_BASE_URL, self.load(), "GET", "/sapi/v1/account/apiRestrictions")

    def _signed_get(self, path: str, params=None):
        return self.signed("GET", path, params)

    def snapshot(self) -> dict[str, Any]:
        if not self.load()["api_key"]:
            return {"configured": False, "account": None, "positions": [], "error": None}
        try:
            account = self._signed_get("/fapi/v2/account")
            raw_positions = self._signed_get("/fapi/v2/positionRisk")
            positions = []
            for item in raw_positions or []:
                amt = float(item.get("positionAmt", 0) or 0)
                if abs(amt) < 1e-12:
                    continue
                positions.append({
                    "symbol": item.get("symbol"),
                    "side": "LONG" if amt > 0 else "SHORT",
                    "qty": abs(amt),
                    "entry": float(item.get("entryPrice", 0) or 0),
                    "mark": float(item.get("markPrice", 0) or 0),
                    "liq": float(item.get("liquidationPrice", 0) or 0),
                    "leverage": float(item.get("leverage", 0) or 0),
                    "margin": float(item.get("isolatedMargin", 0) or 0),
                    "unrealized": float(item.get("unRealizedProfit", 0) or 0),
                    "mode": "ISOLATED" if item.get("marginType") == "isolated" else "CROSS",
                })
            return {
                "configured": True,
                "account": {
                    "available_usdt": float(account.get("availableBalance", 0) or 0),
                    "wallet": float(account.get("totalWalletBalance", 0) or 0),
                    "equity": float(account.get("totalMarginBalance", 0) or 0),
                    "unrealized": float(account.get("totalUnrealizedProfit", 0) or 0),
                    "can_trade": bool(account.get("canTrade")),
                    "can_withdraw": bool(account.get("canWithdraw")),
                },
                "positions": positions,
                "error": None,
            }
        except ValueError as exc:
            return {
                "configured": True, "account": None, "positions": [],
                "error": str(exc), "error_code": getattr(exc, "code", None),
            }
