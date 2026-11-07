"""Small Binance server-time cache used by signed REST requests."""
from __future__ import annotations

import threading
import time

import requests

_lock = threading.Lock()
_offsets: dict[str, int] = {}
_checked: dict[str, float] = {}


def server_time_ms(base: str) -> int:
    """Return Binance time adjusted by the cached server/client offset."""
    now = time.time()
    with _lock:
        if now - _checked.get(base, 0) > 300:
            endpoint = base.rstrip("/") + ("/fapi/v1/time" if "fapi." in base else "/api/v3/time")
            try:
                response = requests.get(endpoint, timeout=4)
                payload = response.json()
                if response.status_code == 200 and payload.get("serverTime"):
                    _offsets[base] = int(payload["serverTime"]) - int(now * 1000)
                    _checked[base] = now
            except (requests.RequestException, ValueError, TypeError):
                # Keep a previous offset if available; otherwise use local time.
                _checked[base] = now
        return int(time.time() * 1000) + int(_offsets.get(base, 0))
