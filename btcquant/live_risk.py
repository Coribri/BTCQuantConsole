"""Local, server-side limits for mainnet order intents.

The 10 USDT values are deliberately only the initial test defaults.  They are
persisted locally and can be raised later through an explicit settings change;
the browser never becomes the source of truth for a limit.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from typing import Any


class LiveRisk:
    DEFAULTS = {
        "max_spot_notional": 10.0,
        "max_futures_margin": 10.0,
        "max_grid_budget": 50.0,
        "max_daily_loss": 50.0,
        "max_leverage": 10,
        "test_mode": True,
    }

    def __init__(self, root: Path):
        self.path = root / "runtime" / "live_risk.json"
        self.lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self.lock:
            data = dict(self.DEFAULTS)
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    for key in data:
                        if key in raw:
                            data[key] = raw[key]
                except Exception:
                    pass
            return data

    def public(self) -> dict[str, Any]:
        data = self.load()
        return {**data, "currency": "USDT", "scope": "per_order"}

    def save(self, **updates) -> dict[str, Any]:
        with self.lock:
            data = self.load()
            for key in self.DEFAULTS:
                if key not in updates:
                    continue
                value = updates[key]
                if key == "test_mode":
                    data[key] = bool(value)
                elif key == "max_leverage":
                    data[key] = max(1, min(100, int(value)))
                else:
                    data[key] = max(0.0, min(100_000.0, float(value)))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            return self.public()

    def check(self, *, market: str, notional: float, leverage: int = 1, closing: bool = False) -> None:
        if closing:
            return
        data = self.load()
        if market == "spot":
            cap = float(data["max_spot_notional"])
            if notional > cap + 1e-9:
                raise ValueError(f"单笔现货名义价值 {notional:.2f} USDT 超过本机上限 {cap:.2f} USDT")
        else:
            cap = float(data["max_futures_margin"])
            margin = notional / max(1, int(leverage))
            if margin > cap + 1e-9:
                raise ValueError(
                    f"单笔合约保证金约 {margin:.2f} USDT 超过本机上限 {cap:.2f} USDT（名义价值 {notional:.2f}）"
                )
            lev_cap = int(data["max_leverage"])
            if int(leverage) > lev_cap:
                raise ValueError(f"杠杆 {leverage}x 超过本机上限 {lev_cap}x")

    def check_grid(self, budget: float) -> None:
        data = self.load()
        cap = float(data["max_grid_budget"])
        if budget > cap + 1e-9:
            raise ValueError(f"网格总预算 {budget:.2f} USDT 超过本机网格预算上限 {cap:.2f} USDT")
