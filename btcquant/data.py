"""Binance public-market-data access and local caching.

Only public endpoints are used here.  No API key is required and this module
cannot place orders.  The public market-data mirror ``data-api.binance.vision``
is tried first because it is usually reachable even when the trading API is
geo-restricted.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import time
from typing import Any

import pandas as pd
import requests

from .config import DataConfig, MarketConfig

LOGGER = logging.getLogger(__name__)

INTERVAL_MS: dict[str, int] = {
    "1s": 1000,
    "30s": 30_000,
    "1m": 60_000,
    "3m": 3 * 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "2h": 2 * 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "6h": 6 * 60 * 60_000,
    "8h": 8 * 60 * 60_000,
    "12h": 12 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
]


class BinanceDataError(RuntimeError):
    pass


def _now_ms() -> int:
    return int(time.time() * 1000)


def _parse_start(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _parse_end(value: str | None) -> pd.Timestamp:
    if value is None:
        return pd.Timestamp.now(tz="UTC")
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    # A bare date means "through the end of that day".
    if len(str(value).strip()) == 10:
        ts = ts + pd.Timedelta(days=1)
    return ts


def _request_json(cfg: DataConfig, path: str, params: dict[str, Any]) -> Any:
    last_error: Exception | None = None
    headers = {"User-Agent": "btc-quant-lab/0.1 (paper trading research)"}
    for base in cfg.base_urls:
        url = base.rstrip("/") + path
        try:
            resp = requests.get(url, params=params, timeout=cfg.timeout_seconds, headers=headers)
            if resp.status_code in (403, 451):
                last_error = BinanceDataError(f"{url} returned HTTP {resp.status_code}")
                continue
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, dict) and payload.get("code") not in (None, 0):
                last_error = BinanceDataError(f"{url}: {payload}")
                continue
            return payload
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            LOGGER.debug("request failed for %s: %s", url, exc)
    raise BinanceDataError(f"all Binance data endpoints failed: {last_error}")


def _rows_to_frame(rows: list[list[Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=KLINE_COLUMNS)
    frame = pd.DataFrame(rows, columns=KLINE_COLUMNS)
    numeric = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "trades",
        "taker_buy_base",
        "taker_buy_quote",
    ]
    for col in numeric:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["open_time"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    frame["close_time"] = pd.to_datetime(frame["close_time"], unit="ms", utc=True)
    frame["open_time_ms"] = (frame["open_time"].astype("int64") // 1_000_000).astype("int64")
    return frame


def _clean_klines(frame: pd.DataFrame, interval: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    frame = frame.copy()
    frame = frame.drop_duplicates(subset=["open_time"], keep="last")
    frame = frame.sort_values("open_time").reset_index(drop=True)
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    frame = frame[(frame["high"] >= frame["low"]) & (frame["close"] > 0) & (frame["open"] > 0)]
    # Never let an unclosed candle into a backtest.
    frame = frame[frame["close_time"] <= pd.Timestamp.now(tz="UTC")]
    frame = frame.reset_index(drop=True)

    interval_ms = INTERVAL_MS[interval]
    if len(frame) > 1:
        gaps = frame["open_time"].diff().dt.total_seconds() * 1000
        n_gaps = int((gaps > interval_ms * 1.5).sum())
        if n_gaps:
            LOGGER.warning("data contains %s gap(s) at %s", n_gaps, interval)
    return frame


def fetch_klines_range(
    data_cfg: DataConfig,
    market: MarketConfig,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    if market.interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval: {market.interval}")
    interval_ms = INTERVAL_MS[market.interval]
    start_ts = _parse_start(str(start)) if not isinstance(start, pd.Timestamp) else start
    end_ts = _parse_end(str(end)) if not isinstance(end, pd.Timestamp) else end
    start_ms = int(start_ts.timestamp() * 1000)
    end_ms = int(end_ts.timestamp() * 1000)
    now_ms = _now_ms()
    if start_ms >= end_ms:
        raise ValueError(f"start must be before end: {start_ts} >= {end_ts}")

    rows: list[list[Any]] = []
    cursor = start_ms
    while cursor < min(end_ms, now_ms):
        payload = _request_json(
            data_cfg,
            "/api/v3/klines",
            {
                "symbol": market.symbol,
                "interval": market.interval,
                "startTime": cursor,
                "endTime": min(end_ms, now_ms) - 1,
                "limit": 1000,
            },
        )
        if not isinstance(payload, list):
            raise BinanceDataError(f"unexpected kline payload: {type(payload)!r}")
        if not payload:
            break
        rows.extend(payload)
        last_open = int(payload[-1][0])
        cursor = last_open + interval_ms
        if len(payload) < 1000:
            break
        time.sleep(0.12)

    frame = _clean_klines(_rows_to_frame(rows), market.interval)
    if frame.empty:
        return frame
    return frame[(frame["open_time"] >= start_ts) & (frame["open_time"] < end_ts)].reset_index(drop=True)


def fetch_recent_klines(
    data_cfg: DataConfig,
    market: MarketConfig,
    limit: int = 400,
    interval: str | None = None,
) -> pd.DataFrame:
    interval = interval or market.interval
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval: {interval}")
    wanted = max(2, min(int(limit), 5000))
    if interval=='30s':
        from .short_candles import fetch_30s
        return fetch_30s(data_cfg,market,wanted)
    rows: list[list[Any]] = []
    end_time: int | None = None
    while len(rows) < wanted:
        batch = min(1000, wanted - len(rows))
        params: dict[str, Any] = {
            "symbol": market.symbol,
            "interval": interval,
            "limit": batch,
        }
        if end_time is not None:
            params["endTime"] = end_time
        payload = _request_json(data_cfg, "/api/v3/klines", params)
        if not isinstance(payload, list) or not payload:
            break
        rows = payload + rows
        if len(payload) < batch:
            break
        first_open = int(payload[0][0])
        end_time = first_open - 1
        if end_time <= 0:
            break
    if not rows:
        raise BinanceDataError("Binance returned no closed candles")
    frame = _clean_klines(_rows_to_frame(rows), interval)
    if frame.empty:
        raise BinanceDataError("Binance returned no closed candles")
    return frame


def fetch_ticker(data_cfg: DataConfig, symbol: str) -> dict[str, Any]:
    payload = _request_json(data_cfg, "/api/v3/ticker/price", {"symbol": symbol})
    if not isinstance(payload, dict) or "price" not in payload:
        raise BinanceDataError(f"unexpected ticker payload: {payload!r}")
    return {"symbol": payload["symbol"], "price": float(payload["price"])}


def fetch_24h_ticker(data_cfg: DataConfig, symbol: str) -> dict[str, Any]:
    payload = _request_json(data_cfg, "/api/v3/ticker/24hr", {"symbol": symbol})
    if not isinstance(payload, dict) or "lastPrice" not in payload:
        raise BinanceDataError(f"unexpected 24h ticker payload: {payload!r}")
    return {
        "symbol": payload["symbol"],
        "price": float(payload["lastPrice"]),
        "change_pct": float(payload.get("priceChangePercent", 0.0)),
        "high": float(payload.get("highPrice", 0.0)),
        "low": float(payload.get("lowPrice", 0.0)),
        "quote_volume": float(payload.get("quoteVolume", 0.0)),
    }


def cache_path(data_cfg: DataConfig, market: MarketConfig) -> Path:
    return data_cfg.dir / f"{market.symbol}_{market.interval}.csv"


def load_klines(
    data_cfg: DataConfig,
    market: MarketConfig,
    start: str | None = None,
    end: str | None = None,
    *,
    force: bool = False,
) -> pd.DataFrame:
    """Load a cached CSV, downloading/refreshing it when needed."""
    start = start or market.start
    start_ts = _parse_start(start)
    end_ts = _parse_end(end)
    path = cache_path(data_cfg, market)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and not force:
        cached = pd.read_csv(path, parse_dates=["open_time", "close_time"])
        cached["open_time"] = pd.to_datetime(cached["open_time"], utc=True)
        cached["close_time"] = pd.to_datetime(cached["close_time"], utc=True)
        if "open_time_ms" not in cached.columns:
            cached["open_time_ms"] = (cached["open_time"].astype("int64") // 1_000_000).astype("int64")
        enough_start = cached["open_time"].min() <= start_ts + pd.Timedelta(days=1)
        enough_end = cached["open_time"].max() >= min(end_ts, pd.Timestamp.now(tz="UTC")) - pd.Timedelta(
            days=2
        )
        if enough_start and enough_end:
            view = cached[(cached["open_time"] >= start_ts) & (cached["open_time"] < end_ts)]
            return view.reset_index(drop=True)

    LOGGER.info("downloading %s %s from %s to %s", market.symbol, market.interval, start, end or "now")
    frame = fetch_klines_range(data_cfg, market, start_ts, end_ts)
    if frame.empty:
        raise BinanceDataError("downloaded data is empty")
    # Atomic-ish write: write to a temporary file then replace.
    tmp = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(tmp, index=False)
    tmp.replace(path)
    return frame


def describe_data(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"rows": 0}
    return {
        "rows": int(len(frame)),
        "first": frame["open_time"].iloc[0].isoformat(),
        "last": frame["open_time"].iloc[-1].isoformat(),
        "first_close": float(frame["close"].iloc[0]),
        "last_close": float(frame["close"].iloc[-1]),
    }


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
