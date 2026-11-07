"""FastAPI app and static-file server for the visual trading console."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Annotated, Any, Literal
import urllib.request

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .coinw import CoinWReadOnly, CoinWSpotReadOnly
from .account_audit import audit_account
from .capabilities import (
    CapabilityRegistry,
    MarketDataUnavailable,
    futures_market_snapshot,
    hint_for,
)
from .broker import HyperliquidTrading, LiveExecution, LivePreflight, _atomic_json, _load_json
from .binance_spot import BinanceSpotReadOnly
from .binance_futures import BinanceFuturesReadOnly
from .live_risk import LiveRisk
from .config import AppConfig, load_config
from .data import fetch_24h_ticker, fetch_recent_klines
from .data import _request_json,fetch_ticker
from .futures_paper import FuturesPaperService
from .grid_paper import GridPaperService, HyperliquidAuto, LiveStrategies
from .service import TradingService
from .research import ResearchService, EventInput, ResearchInterval
from .models import ModelRegistry, ModelSpec, ModelSignal, PROTOCOL, formula
from .testnet import TestnetError, TestnetNotConfigured


STATIC_DIR = Path(__file__).with_name("static")

SUPPORTED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "TRXUSDT", "DOGEUSDT")
SYMBOL_META = {
    "BTCUSDT": {"base": "BTC", "name": "Bitcoin"},
    "ETHUSDT": {"base": "ETH", "name": "Ethereum"},
    "BNBUSDT": {"base": "BNB", "name": "BNB"},
    "XRPUSDT": {"base": "XRP", "name": "XRP"},
    "TRXUSDT": {"base": "TRX", "name": "TRON"},
    "DOGEUSDT": {"base": "DOGE", "name": "Dogecoin"},
}


def normalize_symbol(value: str | None) -> str:
    symbol = str(value or "BTCUSDT").upper().strip()
    return symbol if symbol in SUPPORTED_SYMBOLS else "BTCUSDT"


def trading_symbol(symbol: str = "BTCUSDT") -> str:
    symbol = symbol.strip().upper()
    if symbol not in SUPPORTED_SYMBOLS:
        raise HTTPException(422, "不支持此交易对")
    return symbol


TradingSymbol = Annotated[str, Depends(trading_symbol)]


def _flat_error(
    status_code: int,
    detail: str,
    code: Any,
    reason: str,
    hint: str,
    retryable: bool,
) -> JSONResponse:
    """Error envelope that keeps ``detail`` a string (decision D6)."""
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": str(detail),
            "code": code,
            "reason": reason,
            "hint": hint,
            "retryable": bool(retryable),
        },
    )


_VALIDATION_HINT = "请检查请求参数格式与取值范围。"
_LOCAL_GUARD_HINT = "仅允许从本机控制台发起该请求。"


def _validation_detail(exc: RequestValidationError) -> str:
    """Chinese one-line summary of framework validation errors (D6: string only)."""
    parts: list[str] = []
    for error in list(exc.errors())[:3]:
        location = ".".join(
            str(part)
            for part in error.get("loc", ())
            if str(part) not in {"body", "query", "path"}
        )
        message = str(error.get("msg") or "参数无效")
        parts.append(f"{location}：{message}" if location else message)
    return "请求参数无效：" + "；".join(parts) if parts else "请求参数无效。"


def _string_detail(detail: Any) -> str:
    """Never leak a list/dict into ``detail``; the frontend contracts on a string."""
    if isinstance(detail, str) and detail.strip():
        return detail
    if isinstance(detail, (list, tuple, dict)):
        try:
            return json.dumps(detail, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(detail)
    return "请求失败"


def _http_error_meta(status_code: int, detail: str) -> tuple[Any, str, str, bool]:
    """Map a non-200 HTTP error to the closed reason enum used by the capability matrix."""
    if status_code == 403:
        if "PAPER" in detail:
            return None, "PAPER_MODE", hint_for("PAPER_MODE"), False
        return None, "OUT_OF_SCOPE", _LOCAL_GUARD_HINT, False
    if status_code == 502:
        if "行情" in detail:
            return None, "MARKET_DATA_UNAVAILABLE", hint_for("MARKET_DATA_UNAVAILABLE"), True
        return None, "VENUE_UNAVAILABLE", hint_for("VENUE_UNAVAILABLE"), True
    return None, "OUT_OF_SCOPE", _VALIDATION_HINT, False


_overview_cache: dict[str, Any] = {"time": 0.0, "data": None}
_overview_lock = threading.RLock()


def _futures_json(path: str, symbol: str, extra: str = "") -> Any:
    url = f"https://fapi.binance.com{path}?symbol={symbol}{extra}"
    req = urllib.request.Request(url, headers={"User-Agent": "btc-quant-lab/0.3"})
    with urllib.request.urlopen(req, timeout=4) as response:
        return json.loads(response.read().decode("utf-8"))


def market_overview(cfg: AppConfig, symbol: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    now = time.time()
    with _overview_lock:
        cached = _overview_cache.get("data")
        if cached and now - float(_overview_cache["time"]) < 5 and cached.get("symbol") == symbol:
            return cached
    watch = []
    for item in SUPPORTED_SYMBOLS:
        try:
            tick = fetch_24h_ticker(cfg.data, item)
        except Exception:
            tick = {"symbol": item, "price": 0.0, "change_pct": 0.0, "high": 0.0, "low": 0.0, "quote_volume": 0.0}
        watch.append({**tick, **SYMBOL_META[item]})
    flow = {"buy": 0.0, "sell": 0.0, "net": 0.0, "bars": []}
    try:
        market = replace(cfg.market, symbol=symbol)
        frame = fetch_recent_klines(cfg.data, market, limit=96, interval="15m")
        if not frame.empty:
            buy = float(frame["taker_buy_quote"].sum())
            total = float(frame["quote_volume"].sum())
            sell = max(0.0, total - buy)
            bars = []
            for _, row in frame.tail(16).iterrows():
                b = float(row.get("taker_buy_quote", 0.0))
                q = float(row.get("quote_volume", 0.0))
                bars.append({"buy": b, "sell": max(0.0, q - b)})
            flow = {"buy": buy, "sell": sell, "net": buy - sell, "bars": bars}
    except Exception as exc:
        flow["error"] = str(exc)
    leverage: dict[str, Any] = {
        "funding_rate": None,
        "open_interest": None,
        "long_short_ratio": None,
        "long_pct": None,
        "short_pct": None,
    }
    try:
        premium = _futures_json("/fapi/v1/premiumIndex", symbol)
        oi = _futures_json("/fapi/v1/openInterest", symbol)
        leverage["funding_rate"] = float(premium.get("lastFundingRate", 0.0))
        leverage["mark_price"] = float(premium.get("markPrice", 0.0))
        leverage["open_interest"] = float(oi.get("openInterest", 0.0))
        ratio = _futures_json(
            "/futures/data/globalLongShortAccountRatio", symbol, "&period=15m&limit=1"
        )
        if ratio and isinstance(ratio, list) and ratio:
            last = ratio[-1]
            leverage["long_short_ratio"] = float(last.get("longShortRatio", 0.0))
            leverage["long_pct"] = float(last.get("longAccount", 0.0))
            leverage["short_pct"] = float(last.get("shortAccount", 0.0))
    except Exception as exc:
        leverage["error"] = str(exc)
    payload = {
        "symbol": symbol,
        "base": SYMBOL_META[symbol]["base"],
        "watch": watch,
        "flow": flow,
        "leverage": leverage,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with _overview_lock:
        _overview_cache["time"] = now
        _overview_cache["data"] = payload
    return payload


def _suffixed(path: Path, suffix: str) -> Path:
    return path.with_name(f"{path.stem}{suffix}{path.suffix}")


def symbol_config(cfg: AppConfig, symbol: str) -> AppConfig:
    symbol = normalize_symbol(symbol)
    market = replace(cfg.market, symbol=symbol)
    if symbol == "BTCUSDT":
        return replace(cfg, market=market)
    suffix = f"_{symbol.lower()}"
    testnet = replace(
        cfg.testnet,
        state_file=_suffixed(cfg.testnet.state_file, suffix),
        paper_state_file=_suffixed(cfg.testnet.paper_state_file, suffix),
    )
    paper = replace(
        cfg.paper,
        state_file=_suffixed(cfg.paper.state_file, suffix),
        trades_file=_suffixed(cfg.paper.trades_file, suffix),
        equity_file=_suffixed(cfg.paper.equity_file, suffix),
        log_file=_suffixed(cfg.paper.log_file, suffix),
    )
    return replace(cfg, market=market, testnet=testnet, paper=paper)


def existing_console_url(host: str, port: int) -> str | None:
    """Return the URL if this port already serves the BTC Quant console."""
    target = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{target}:{port}"
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1.5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if payload.get("ok"):
            return url
    except Exception:
        return None
    return None


def load_env_file(path: Path) -> int:
    """Minimal .env loader; existing environment variables win."""
    if not path.exists():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Be forgiving if a user pasted "KEY==value" instead of "KEY=value".
        while value.startswith("="):
            value = value[1:].lstrip()
        if key and (key not in os.environ or not os.environ.get(key)):
            os.environ[key] = value
            loaded += 1
    return loaded


class CoinWSettingsRequest(BaseModel):
    api_key: str = Field(default="", max_length=200)
    api_secret: str = Field(default="", max_length=300)
class AutoRequest(BaseModel):
    enabled: bool
    interval: str | None = Field(default=None, pattern="^(1m|5m|15m|30m|1h|2h|4h|8h|12h|1d)$")
    risk_fraction: float | None = Field(default=None,ge=.001,le=.02,allow_inf_nan=False)
    model_id: str | None = None


class OrderRequest(BaseModel):
    side: str = Field(pattern="^(BUY|SELL|buy|sell)$")
    quote_amount: float | None = None
    reason: str = "manual"


class FuturesOrderRequest(BaseModel):
    side: str = Field(pattern="^(LONG|SHORT|long|short)$")
    action: str = Field(default="OPEN", pattern="^(OPEN|CLOSE|open|close)$")
    order_type: str = Field(default="MARKET", pattern="^(MARKET|LIMIT|market|limit)$")
    qty: float = Field(gt=0,allow_inf_nan=False)
    leverage: int = Field(default=10, ge=1, le=100)
    limit_price: float | None = Field(default=None,allow_inf_nan=False)
    take_profit: float | None = Field(default=None,allow_inf_nan=False)
    stop_loss: float | None = Field(default=None,allow_inf_nan=False)
    margin_mode: str = Field(default="ISOLATED", pattern="^(ISOLATED|CROSS|isolated|cross)$")


class CancelFuturesRequest(BaseModel):
    order_id: str


class FuturesAutoRequest(BaseModel):
    enabled: bool
    interval: str = Field(default="1h", pattern="^(1m|5m|15m|30m|1h|2h|4h|8h|12h|1d)$")
    leverage: int = Field(default=5, ge=1, le=10)
    risk_fraction: float = Field(default=.005,ge=.001,le=.02,allow_inf_nan=False)
    model_id: str | None = None


class TrailingRequest(BaseModel):
    position_id: str
    enabled: bool
    activation_pct: float = Field(default=.01,ge=.001,le=.5,allow_inf_nan=False)
    distance_pct: float = Field(default=.005,ge=.0005,le=.2,allow_inf_nan=False)


class FuturesTpslRequest(BaseModel):
    take_profit: float | None = Field(default=None,allow_inf_nan=False)
    stop_loss: float | None = Field(default=None,allow_inf_nan=False)
    position_id: str | None = None


class GridStartRequest(BaseModel):
    lower: float = Field(gt=0)
    upper: float = Field(gt=0)
    grids: int = Field(default=20, ge=2, le=100)
    investment: float = Field(gt=0)
    mode: str = Field(default="arithmetic", pattern="^(arithmetic|geometric)$")
    initial_ratio: float = Field(default=0.5, ge=0, le=1)
    stop_loss_pct: float = Field(default=0.08, ge=0, le=0.5)


class GridStopRequest(BaseModel):
    liquidate: bool = False


class ResearchRequest(BaseModel):
    symbol: str = Field(default="BTCUSDT", pattern="^(BTC|ETH|BNB|XRP|TRX|DOGE)USDT$")
    interval: ResearchInterval = "4h"
    bars: int = Field(default=3000, ge=1000, le=5000)
    fee_bps: float = Field(default=10, ge=0, le=100, allow_inf_nan=False)
    slippage_bps: float = Field(default=2, ge=0, le=100, allow_inf_nan=False)


class DeepSeekSettings(BaseModel):
    api_key: str = Field(default="", max_length=256)
    model: str = Field(default="deepseek-flash", pattern=r"^[a-zA-Z0-9._-]{1,80}$")


class LiveOrderRequest(BaseModel):
    exchange: Literal["binance", "coinw"]
    market: Literal["spot", "futures"]
    symbol: str = Field(pattern=r"^(BTC|ETH|BNB|XRP|TRX|DOGE)USDT$")
    side: Literal["BUY", "SELL"] | None = None
    type: Literal["MARKET", "LIMIT"]
    quantity: Decimal | None = Field(None, gt=0, max_digits=18, decimal_places=12)
    quote: Decimal | None = Field(None, gt=0, max_digits=18, decimal_places=12)
    price: Decimal | None = Field(None, gt=0, max_digits=18, decimal_places=12)
    position: Literal["LONG", "SHORT"] | None = None
    action: Literal["OPEN", "CLOSE"] | None = None
    position_id: str | None = Field(None, pattern=r"^[0-9]{1,24}$")
    leverage: int = Field(1, ge=1, le=LivePreflight.MANUAL_LEVERAGE)
    cross: bool = False

    def validate_intent(self):
        if (self.price is not None) != (self.type == "LIMIT"):
            raise ValueError("限价单需要指定价格，市价单使用交易所撮合")
        if self.market == "spot":
            if self.side is None or self.position is not None or self.action is not None:
                raise ValueError("现货请指定买入或卖出")
            if (self.quantity is None) == (self.quote is None):
                raise ValueError("只填写一种下单数量")
            if self.quote is not None and (self.side != "BUY" or self.type != "MARKET"):
                raise ValueError("按 USDT 金额下单仅适用于现货市价买入")
        elif self.position is None or self.action is None or self.quantity is None or self.quote is not None:
            raise ValueError("合约请填写多空方向、开平仓和数量")
        elif self.exchange == "coinw" and self.action == "CLOSE" and self.position_id is None:
            raise ValueError("请先选择需要平仓的 CoinW 仓位")


class ManualRiskRequest(BaseModel):
    max_spot_notional: float | None = Field(default=None, gt=0, le=100_000)
    max_futures_margin: float | None = Field(default=None, gt=0, le=100_000)
    max_daily_loss: float | None = Field(default=None, gt=0, le=100_000)
    max_leverage: int | None = Field(default=None, ge=1, le=100)
    test_mode: bool | None = None


class LiveConfirmation(BaseModel):
    confirmation: str = Field(pattern=r"^bq[0-9a-f]{32}$")




class HyperliquidWalletRequest(BaseModel):
    network: Literal["mainnet", "testnet"]
    address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")


class HyperliquidAgentRequest(BaseModel):
    address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    chain_id: int = Field(ge=1, le=9_007_199_254_740_991)


class HyperliquidAgentSignature(BaseModel):
    token: str = Field(pattern=r"^[0-9a-f]{32}$")
    signature: str = Field(pattern=r"^0x[0-9a-fA-F]{130}$")


class HyperliquidOrderRequest(BaseModel):
    network: Literal["testnet"]
    address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    symbol: str = Field(pattern=r"^(BTC|ETH|BNB|XRP|TRX|DOGE)USDT$")
    position: Literal["LONG", "SHORT"]
    action: Literal["OPEN", "CLOSE"]
    type: Literal["MARKET", "LIMIT"]
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=6)
    price: Decimal | None = Field(None, gt=0, max_digits=18, decimal_places=6)


class HyperliquidAutoRequest(BaseModel):
    address: str = Field(pattern=r"^0x[0-9a-fA-F]{40}$")
    symbol: str = Field(pattern=r"^(BTC|ETH|BNB|XRP|TRX|DOGE)USDT$")
    interval: Literal["1h", "4h"]
    amount: Decimal = Field(ge=11, max_digits=12, decimal_places=4)
    model_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,48}$")




class LiveOrderManagement(BaseModel):
    id: str = Field(pattern=r"^bq[0-9a-f]{32}$")


class LiveStrategyRequest(BaseModel):
    kind: Literal["grid", "auto"]
    exchange: Literal["binance", "coinw"]
    market: Literal["spot", "futures"]
    symbol: str = Field(pattern=r"^(BTC|ETH|BNB|XRP|TRX|DOGE)USDT$")
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    interval: str = Field(default="1h", pattern=r"^(1m|5m|15m|30m|1h|2h|4h|8h|12h|1d)$")
    model_id: str = Field(default="spot-trend-v2", pattern=r"^[a-z][a-z0-9_-]{2,48}$")
    lower: Decimal | None = Field(None, gt=0, max_digits=18, decimal_places=12)
    upper: Decimal | None = Field(None, gt=0, max_digits=18, decimal_places=12)
    levels: int = Field(6, ge=2, le=12)
    distribution: Literal["arithmetic", "geometric"] = "arithmetic"
    initial_ratio: Decimal = Field(default=0, ge=0, le=0.5, decimal_places=4)
    stop_loss_pct: Decimal = Field(default=0, ge=0, le=50, decimal_places=2)


class LiveRiskRequest(BaseModel):
    max_order: Decimal = Field(gt=0, le=10_000_000, decimal_places=2)
    max_symbol: Decimal = Field(gt=0, le=10_000_000, decimal_places=2)
    max_margin: Decimal = Field(gt=0, le=10_000_000, decimal_places=2)
    max_daily_loss: Decimal = Field(gt=0, le=10_000_000, decimal_places=2)
    max_losses: int = Field(ge=1, le=30)


class LiveStrategyConfirmation(BaseModel):
    confirmation: str = Field(pattern=r"^[0-9a-f]{32}$")
    initial_confirm: str | None = Field(default=None, max_length=8)


class LiveStrategyControl(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    action: Literal["stop", "resume", "retire"]


def create_app(cfg: AppConfig, *, force_paper: bool = False, disable_live: bool = False) -> FastAPI:
    research = ResearchService(cfg)
    models = ModelRegistry(cfg.root,cfg.web.port)
    spot_services: dict[str, TradingService] = {}
    futures_services: dict[str, FuturesPaperService] = {}
    grid_services: dict[str, GridPaperService] = {}
    chart_readers: dict[str, TradingService] = {}
    services_lock = threading.RLock()

    def spot_for(symbol: str) -> TradingService:
        symbol = normalize_symbol(symbol)
        with services_lock:
            if symbol not in spot_services:
                item = TradingService(
                    symbol_config(cfg, symbol),
                    # Spot Testnet execution is BTC-only in this build.  Alt
                    # pairs use the local paper account so the UI is honest.
                    force_paper=force_paper,
                )
                item.start()
                spot_services[symbol] = item
            return spot_services[symbol]

    def chart_for(symbol:str,interval:str):
        symbol=normalize_symbol(symbol)
        key=f'{symbol}:{interval}'
        with services_lock:
            if key not in chart_readers:
                # A passive reader has no polling thread. Heavy historical
                # aggregation never shares a trading service's execution lock.
                chart_readers[key]=TradingService(symbol_config(cfg,symbol),force_paper=True)
            return chart_readers[key]

    def futures_for(symbol: str) -> FuturesPaperService:
        symbol = normalize_symbol(symbol)
        with services_lock:
            if symbol not in futures_services:
                item = FuturesPaperService(symbol_config(cfg, symbol))
                item.start()
                futures_services[symbol] = item
            return futures_services[symbol]

    def grid_for(symbol: str) -> GridPaperService:
        symbol = normalize_symbol(symbol)
        with services_lock:
            if symbol not in grid_services:
                item = GridPaperService(symbol_config(cfg, symbol))
                item.start()
                grid_services[symbol] = item
            return grid_services[symbol]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        spot_for("BTCUSDT")
        futures_for("BTCUSDT")
        grid_for("BTCUSDT")
        yield
        hyperliquid_auto.shutdown()
        live_strategies.shutdown()
        research.close()
        with services_lock:
            for item in list(grid_services.values()):
                item.stop()
            for item in list(futures_services.values()):
                item.stop()
            for item in list(spot_services.values()):
                item.stop()
            for item in list(chart_readers.values()):
                item.stop()
            grid_services.clear()
            futures_services.clear()
            spot_services.clear()

    app = FastAPI(
        version="1.0.0",
        title="BTC Quant Testnet Console",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
    )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(_request: Request, exc: RequestValidationError):
        """Framework 422 must keep the flat envelope and a string ``detail`` (D6)."""
        return _flat_error(
            422, _validation_detail(exc), None, "OUT_OF_SCOPE", _VALIDATION_HINT, False
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_request: Request, exc: StarletteHTTPException):
        status_code = int(exc.status_code)
        detail = _string_detail(exc.detail)
        if status_code in {403, 422, 502}:
            code, reason, hint, retryable = _http_error_meta(status_code, detail)
            return _flat_error(status_code, detail, code, reason, hint, retryable)
        # 400/404/409 keep their historical body: only ``detail`` is guaranteed a string.
        return JSONResponse({"detail": detail}, status_code=status_code)

    app.state.spot_services = spot_services
    app.state.futures_services = futures_services
    app.state.grid_services = grid_services
    app.state.cfg = cfg
    app.state.research = research
    coinw = CoinWReadOnly(cfg.root)
    coinw_spot = CoinWSpotReadOnly(cfg.root)
    binance_spot = BinanceSpotReadOnly(cfg.root)
    binance_futures = BinanceFuturesReadOnly(cfg.root)
    hyperliquid = HyperliquidTrading()
    live_risk = LiveRisk(cfg.root)
    preflight = LivePreflight(live_risk)

    def connection_state(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            **payload,
            "mode": "read_only" if disable_live else "live",
            "read_only": bool(disable_live),
            "live_available": not disable_live,
        }
    live_execution = LiveExecution(cfg.root, {
        ("binance", "spot"): binance_spot, ("binance", "futures"): binance_futures,
        ("coinw", "spot"): coinw_spot, ("coinw", "futures"): coinw,
        ("hyperliquid", "futures"): hyperliquid,
    }, preflight=preflight)
    app.state.live_execution = live_execution
    capability_registry = CapabilityRegistry(
        cfg,
        dict(live_execution.connections),
        disable_live=disable_live,
        preflight=preflight,
        ledger=live_execution,
    )
    app.state.capabilities = capability_registry
    live_strategies = LiveStrategies(cfg.root, live_execution, models)
    hyperliquid_auto = HyperliquidAuto(hyperliquid, live_execution, models)
    app.state.live_strategies = live_strategies

    @app.middleware("http")
    async def static_no_cache(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            # The console is shipped as a local, frequently updated folder.
            # Never let a stale app.js/layout.js survive a package upgrade.
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @app.middleware("http")
    async def paper_live_guard(request, call_next):
        path = request.url.path
        if disable_live and path.startswith("/api/live/") and path != "/api/live/features":
            return _flat_error(
                403, "PAPER 模式已禁用实盘接口", None, "PAPER_MODE",
                hint_for("PAPER_MODE"), False,
            )
        return await call_next(request)

    @app.middleware("http")
    async def private_api_origin_guard(request, call_next):
        path = request.url.path
        live = path.startswith("/api/live/")
        exchange = path.startswith("/api/exchange/")
        protected = live or exchange or (
            path.startswith(("/api/research", "/api/models")) and request.method == "POST"
        )
        if protected:
            origin = request.headers.get("origin")
            same_origin = origin is None or origin == str(request.base_url).rstrip("/")
            if request.headers.get("sec-fetch-site") == "cross-site" or not same_origin:
                return _flat_error(
                    403, "只允许从本地控制台发起该操作", None, "OUT_OF_SCOPE",
                    _LOCAL_GUARD_HINT, False,
                )
        if live:
            if (request.url.hostname not in {"localhost", "127.0.0.1", "::1"}
                or request.client is None or request.client.host not in {"127.0.0.1", "::1"}):
                return _flat_error(
                    403, "实盘交易仅在本机浏览器可用", None, "OUT_OF_SCOPE",
                    _LOCAL_GUARD_HINT, False,
                )
            if request.method != "GET" and request.headers.get("origin") != str(request.base_url).rstrip("/"):
                return _flat_error(
                    403, "请从本机实盘页面确认操作", None, "OUT_OF_SCOPE",
                    _LOCAL_GUARD_HINT, False,
                )
        if exchange and request.url.hostname not in {"localhost", "127.0.0.1", "::1", "testserver"}:
            return _flat_error(
                403, "交易所 API 仅在本机地址可用", None, "OUT_OF_SCOPE",
                _LOCAL_GUARD_HINT, False,
            )
        response = await call_next(request)
        if exchange or live:
            response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/futures", include_in_schema=False)
    def futures_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "futures.html")

    @app.get("/grid", include_in_schema=False)
    def grid_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "grid.html")

    @app.get("/research", include_in_schema=False)
    def research_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "research.html")

    @app.get("/models",include_in_schema=False)
    def models_page():
        return FileResponse(STATIC_DIR/"models.html")

    @app.get("/api/models")
    def models_list(scope: str | None = None):
        if scope is not None and scope not in {"spot","futures"}:
            raise HTTPException(400,"scope 必须是 spot 或 futures")
        return {"protocol":PROTOCOL,"models":models.cards(scope),"ai_configured":bool(os.getenv("DEEPSEEK_API_KEY"))}

    @app.get("/api/models/schema")
    def models_schema():
        return {"protocol":PROTOCOL,"registration":ModelSpec.model_json_schema(),"prediction":ModelSignal.model_json_schema()}

    @app.post("/api/models/compile")
    def models_compile(spec:ModelSpec):
        return {"formula":formula(spec),"model":spec.model_dump()}

    @app.post("/api/models")
    def models_save(spec:ModelSpec):
        for svc in spot_services.values():
            if svc.auto_enabled and svc.model_id==spec.id:
                raise HTTPException(409,"该模型正在现货 AUTO 中运行，请先关闭 AUTO")
        for svc in futures_services.values():
            if svc.state.get("auto_enabled") and svc.state.get("auto_model_id")==spec.id:
                raise HTTPException(409,"该模型正在合约 AUTO 中运行，请先关闭 AUTO")
        try:
            models.save(spec)
            return {"ok":True,"model":spec.model_dump(),"formula":formula(spec)}
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc

    @app.post("/api/models/{model_id}/preview")
    def models_preview(model_id:str,symbol:str="BTCUSDT",interval:str="1h",scope:str="futures"):
        if interval not in {"1m","5m","15m","30m","1h","2h","4h","8h","12h","1d"} or scope not in {"spot","futures"}:
            raise HTTPException(400,"预览支持 1M–1D 与 spot/futures")
        try:
            spec=models.get(model_id)
            if spec.scope != scope:
                raise HTTPException(400,f"该模型只适用于{'现货' if spec.scope=='spot' else '合约'}")
            frame=fetch_recent_klines(cfg.data,replace(cfg.market,symbol=normalize_symbol(symbol)),limit=400,interval=interval)
            frame.attrs["symbol"]=normalize_symbol(symbol)
            signals=models.evaluate(frame,model_id,interval,scope,allow_remote=True)
            from .strategies import strategy_snapshot
            return {"model":spec.model_dump(),"signal":strategy_snapshot(signals,"trend_v2"),"orders_sent":False}
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(400,"模型预览失败，请检查行情、模型设置和接口连接") from exc

    @app.get("/api/models/{model_id}/context")
    def models_context(model_id:str,symbol:str="BTCUSDT",interval:str="1h"):
        if interval not in {"1m","5m","15m","30m","1h","2h","4h","8h","12h","1d"}:
            raise HTTPException(400,"不支持的执行周期")
        try:
            from .ai_context import build_context
            spec=models.get(model_id)
            symbol=normalize_symbol(symbol)
            frame=chart_for(symbol,interval).broker.klines(interval,limit=400)
            context,guard=build_context(cfg.root,frame,symbol,interval,events=spec.ai_events,ml=spec.ai_ml)
            return {"context":context,"guard":guard,"orders_sent":False,"ai_called":False}
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc

    @app.get("/api/research/status")
    def research_status():
        return research.status()

    @app.post("/api/research/outlook")
    def research_outlook(symbol: str = "BTCUSDT", interval: str = "4h"):
        try:
            return research.outlook(symbol, interval)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/research/run", status_code=202)
    def research_run(req: ResearchRequest):
        try:
            return research.start(**req.model_dump())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/research/events")
    def research_event(req: EventInput):
        try:
            return {"added": research.store.add(req)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/research/events/{action}", status_code=202)
    def research_event_action(action: str):
        if action not in {"sync", "analyze"}:
            raise HTTPException(404, "unknown event action")
        try:
            return research.event_task(action)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/research/settings")
    def research_settings(req: DeepSeekSettings):
        key = req.api_key.strip()
        if key and (not key.isascii() or any(ch.isspace() for ch in key) or not all(ch.isalnum() or ch in "_-" for ch in key)):
            raise HTTPException(400, "API Key 格式无效")
        with research.lock:
            path = cfg.root / ".env"
            lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
            updates = {"DEEPSEEK_MODEL": req.model}
            if key:
                updates["DEEPSEEK_API_KEY"] = key
            retained = [line for line in lines if line.split("=", 1)[0].strip() not in updates]
            # Write only to the existing local configuration, never to browser storage.
            temp = path.with_name(".env.research-tmp")
            temp.write_text("\n".join(retained + [f"{k}={v}" for k,v in updates.items()])+"\n", encoding="utf-8")
            temp.replace(path)
            os.environ.update(updates)
        return {"ok": True, "configured": bool(os.getenv("DEEPSEEK_API_KEY")), "model": req.model}

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "mode": spot_for("BTCUSDT").broker.mode,
            "futures": "paper",
            "grid": "paper",
            "symbols": list(SUPPORTED_SYMBOLS),
        }

    @app.get("/api/exchange/coinw")
    def coinw_settings() -> dict[str, Any]:
        return connection_state(coinw.public_settings())

    @app.post("/api/exchange/coinw/settings")
    def coinw_save(req: CoinWSettingsRequest) -> dict[str, Any]:
        try:
            live_strategies.credentials_change("coinw", "futures", coinw.save, req.api_key, req.api_secret)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return connection_state(coinw.public_settings())

    @app.post("/api/exchange/coinw/test")
    def coinw_test() -> dict[str, Any]:
        result = coinw.snapshot()
        return connection_state({**result, "order_enabled": False})

    @app.delete("/api/exchange/coinw")
    def coinw_clear() -> dict[str, Any]:
        try:
            live_strategies.credentials_change("coinw", "futures", coinw.clear)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return connection_state(coinw.public_settings())

    @app.get("/api/exchange/coinw-spot")
    def coinw_spot_settings() -> dict[str, Any]:
        return connection_state(coinw_spot.public_settings())

    @app.post("/api/exchange/coinw-spot/settings")
    def coinw_spot_save(req: CoinWSettingsRequest) -> dict[str, Any]:
        try:
            live_strategies.credentials_change("coinw", "spot", coinw_spot.save, req.api_key, req.api_secret)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return coinw_spot_settings()

    @app.post("/api/exchange/coinw-spot/test")
    def coinw_spot_test() -> dict[str, Any]:
        return connection_state({**coinw_spot.snapshot(), "order_enabled": False})

    @app.delete("/api/exchange/coinw-spot")
    def coinw_spot_clear():
        try:
            live_strategies.credentials_change("coinw", "spot", coinw_spot.clear)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return coinw_spot_settings()

    @app.get("/api/exchange/binance")
    def binance_settings() -> dict[str, Any]:
        return connection_state(binance_spot.public_settings())

    @app.post("/api/exchange/binance/settings")
    def binance_save(req: CoinWSettingsRequest) -> dict[str, Any]:
        try:
            live_strategies.credentials_change("binance", "spot", binance_spot.save, req.api_key, req.api_secret)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return connection_state(binance_spot.public_settings())

    @app.post("/api/exchange/binance/test")
    def binance_test() -> dict[str, Any]:
        result = binance_spot.snapshot()
        return connection_state({**result, "order_enabled": False})

    @app.delete("/api/exchange/binance")
    def binance_clear() -> dict[str, Any]:
        try:
            live_strategies.credentials_change("binance", "spot", binance_spot.clear)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return connection_state(binance_spot.public_settings())

    @app.get("/api/exchange/binance-futures")
    def binance_futures_settings() -> dict[str, Any]:
        return connection_state(binance_futures.public_settings())

    @app.post("/api/exchange/binance-futures/settings")
    def binance_futures_save(req: CoinWSettingsRequest) -> dict[str, Any]:
        try:
            live_strategies.credentials_change("binance", "futures", binance_futures.save, req.api_key, req.api_secret)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return connection_state(binance_futures.public_settings())

    @app.post("/api/exchange/binance-futures/test")
    def binance_futures_test() -> dict[str, Any]:
        result = binance_futures.snapshot()
        return connection_state({**result, "order_enabled": False})

    @app.delete("/api/exchange/binance-futures")
    def binance_futures_clear() -> dict[str, Any]:
        try:
            live_strategies.credentials_change("binance", "futures", binance_futures.clear)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return connection_state(binance_futures.public_settings())

    @app.get("/api/capabilities")
    def api_capabilities(exchange: str | None = None, market: str | None = None, refresh: int = 0):
        try:
            return capability_registry.matrix(
                exchange=exchange, market=market, refresh=bool(refresh)
            )
        except ValueError as exc:
            return _flat_error(
                422, str(exc), "VALIDATION_ERROR", "OUT_OF_SCOPE",
                "请检查 exchange/market 参数是否在覆盖范围内。", False,
            )

    @app.get("/api/exchange/health")
    def api_exchange_health(exchange: str = "", market: str = ""):
        ex = exchange.strip().lower()
        mk = market.strip().lower()
        if ex == "hyperliquid":
            return _flat_error(
                422,
                "Hyperliquid 为地址只读接入，无 API Key 可诊断",
                "NOT_IMPLEMENTED",
                "NOT_IMPLEMENTED",
                hint_for(
                    "NOT_IMPLEMENTED", exchange="hyperliquid",
                    market=mk or "futures", capability="account_read",
                ),
                False,
            )
        if ex not in {"binance", "coinw"} or mk not in {"spot", "futures"}:
            return _flat_error(
                422,
                "exchange 必须是 binance/coinw，market 必须是 spot/futures",
                "VALIDATION_ERROR",
                "OUT_OF_SCOPE",
                "请检查 exchange 与 market 参数。",
                False,
            )
        return capability_registry.health(ex, mk)

    @app.get("/api/futures/market")
    def api_futures_market(
        exchange: str = "binance",
        symbol: str = Query(default="BTCUSDT", pattern=r"^[A-Za-z0-9]{1,24}$"),
    ):
        ex = exchange.strip().lower()
        sym = symbol.strip().upper()
        if ex != "binance":
            return _flat_error(
                422,
                "首批仅接入 Binance 合约原生行情",
                "NOT_IMPLEMENTED",
                "NOT_IMPLEMENTED",
                hint_for("NOT_IMPLEMENTED", exchange=ex, market="futures", capability="market_data"),
                False,
            )
        if sym not in SUPPORTED_SYMBOLS:
            return _flat_error(
                422, "不支持此交易对", "VALIDATION_ERROR", "OUT_OF_SCOPE",
                "请使用 BTCUSDT/ETHUSDT 等受支持交易对。", False,
            )
        try:
            return futures_market_snapshot(
                sym,
                connector=binance_futures,
                paper=disable_live,
            )
        except MarketDataUnavailable:
            return _flat_error(
                502,
                "合约行情暂不可用",
                "MARKET_DATA_UNAVAILABLE",
                "MARKET_DATA_UNAVAILABLE",
                hint_for("MARKET_DATA_UNAVAILABLE"),
                True,
            )

    @app.get("/api/live/features")
    def live_features():
        return capability_registry.features()

    @app.get("/api/live/account/audit")
    def live_account_audit(
        exchange: Literal["binance", "coinw"],
        market: Literal["spot", "futures"],
        symbol: str = Query(pattern=r"^(BTC|ETH|BNB|XRP|TRX|DOGE)USDT$"),
    ):
        connector = live_execution.connections[exchange, market]
        return audit_account(
            cfg.root, exchange, market, symbol,
            connector=connector,
            env="paper" if disable_live else "live",
            ledger=live_execution,
        )

    @app.get("/api/live/account")
    def live_account(exchange: Literal["binance", "coinw"], market: Literal["spot", "futures"], symbol: TradingSymbol):
        connector = live_execution.connections[exchange, market]
        with connector.lock:
            result = preflight.account(connector, exchange, market)
            if market == "futures" and (exchange == "coinw" or result.get("configured") and not result.get("error")):
                try:
                    result["leverage"] = preflight.leverage(connector, exchange, symbol)
                except (ValueError, KeyError, TypeError) as exc:
                    result["leverage_error"] = str(exc)
            return result

    @app.get("/api/live/rules")
    def live_rules(exchange: Literal["binance", "coinw"], market: Literal["spot", "futures"], symbol: TradingSymbol):
        try:
            rules = preflight.rules(exchange, market, symbol)
            if exchange == "binance":
                filters = rules["filters"]
                lot = filters.get("LOT_SIZE", {})
                notional = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))
                price_url = ("https://api.binance.com/api/v3/avgPrice" if market == "spot"
                             else "https://fapi.binance.com/fapi/v1/premiumIndex")
                ticker = preflight.public(price_url, {"symbol": symbol})
                price = ticker.get("price") or ticker.get("markPrice")
                return {
                    "exchange": exchange, "market": market, "symbol": symbol,
                    "base": symbol[:-4], "step": lot.get("stepSize", "0"),
                    "min_qty": lot.get("minQty", "0"), "max_qty": lot.get("maxQty", "0"),
                    "min_notional": notional.get("minNotional", notional.get("notional", "0")),
                    "price": price,
                    **(preflight.binance_spot_ticket_rules(rules) if market == "spot" else {}),
                }
            if market == "spot":
                pair = rules["coinw_spot"]
                decimals = max(0, int(pair.get("countPrecision", 0)))
                ticker = preflight.public("https://api.coinw.com/api/v1/public", {"command": "returnTicker"})
                price = ticker["data"][symbol[:-4] + "_USDT"]["last"]
                return {
                    "exchange": exchange, "market": market, "symbol": symbol,
                    "base": symbol[:-4], "step": str(10 ** -decimals),
                    "min_qty": pair.get("minBuyCount", "0"), "max_qty": pair.get("maxBuyCount", "0"),
                    "min_notional": pair.get("minBuyAmount", "0"), "price": price,
                }
            return {"exchange": exchange, "market": market, "symbol": symbol,
                    "base": symbol[:-4], "step": "1", "min_qty": "1", "max_qty": "0",
                    "min_notional": "0", "price": None}
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/live/depth")
    def live_depth(market: Literal["spot", "futures"], symbol: TradingSymbol):
        try:
            url = ("https://api.binance.com/api/v3/depth" if market == "spot"
                   else "https://fapi.binance.com/fapi/v1/depth")
            raw = preflight.public(url, {"symbol": symbol, "limit": 20})
            return {"symbol": symbol, "market": market, "bids": raw.get("bids", []),
                    "asks": raw.get("asks", []),
                    "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/live/risk")
    def live_risk_settings():
        return live_strategies.risk_settings()

    @app.post("/api/live/risk")
    def save_live_risk(req: LiveRiskRequest):
        return live_strategies.risk_settings(req.model_dump(mode="json"))

    @app.get("/api/live/manual-risk")
    def manual_risk():
        return live_risk.public()

    @app.post("/api/live/manual-risk")
    def save_manual_risk(req: ManualRiskRequest):
        return live_risk.save(**req.model_dump(exclude_none=True))

    @app.post("/api/live/flatten/preview")
    def live_flatten_preview():
        try:
            return live_strategies.preview_flatten()
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/live/flatten")
    def live_flatten(req: LiveStrategyConfirmation):
        try:
            return live_strategies.flatten(req.confirmation)
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/live/dex/hyperliquid/market")
    def hyperliquid_market(
        coin: Literal["BTC", "ETH", "BNB", "XRP", "TRX", "DOGE"],
        network: Literal["mainnet", "testnet"] = "mainnet",
        interval: Literal["1h", "4h"] = "1h",
    ):
        try:
            return hyperliquid.market(network, coin, interval)
        except LookupError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/live/dex/hyperliquid/wallet")
    def hyperliquid_wallet(req: HyperliquidWalletRequest):
        try:
            return hyperliquid.wallet(req.network, req.address)
        except ValueError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/live/dex/hyperliquid/agent")
    def hyperliquid_agent_status():
        return hyperliquid.status()

    @app.post("/api/live/dex/hyperliquid/agent/prepare")
    def hyperliquid_prepare_agent(req: HyperliquidAgentRequest):
        try:
            return hyperliquid.prepare_agent(req.address, req.chain_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/live/dex/hyperliquid/agent/enable")
    def hyperliquid_enable_agent(req: HyperliquidAgentSignature):
        try:
            return hyperliquid.enable_agent(req.token, req.signature)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/live/dex/hyperliquid/agent/disable")
    def hyperliquid_disable_agent():
        return hyperliquid_auto.disable_agent()

    @app.get("/api/live/dex/hyperliquid/orders")
    def hyperliquid_orders(symbol: str = Query(pattern=r"^(BTC|ETH|BNB|XRP|TRX|DOGE)USDT$")):
        return {"orders": live_execution.list_orders("hyperliquid", "futures", symbol)}

    @app.post("/api/live/dex/hyperliquid/preview")
    def hyperliquid_order_preview(req: HyperliquidOrderRequest):
        if (req.price is not None) != (req.type == "LIMIT"):
            raise HTTPException(422, "限价单需要指定价格")
        intent = req.model_dump(exclude={"network", "address"})
        intent.update(exchange="hyperliquid", market="futures", dex_owner=req.address.lower())
        try:
            return live_execution.preview(intent)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/live/dex/hyperliquid/auto")
    def hyperliquid_auto_status():
        return hyperliquid_auto.status()

    @app.post("/api/live/dex/hyperliquid/auto/preview")
    def hyperliquid_auto_preview(req: HyperliquidAutoRequest):
        try:
            return hyperliquid_auto.prepare(req.model_dump(mode="json"))
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/live/dex/hyperliquid/auto/start")
    def hyperliquid_auto_start(req: LiveStrategyConfirmation):
        try:
            return hyperliquid_auto.start(req.confirmation)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/live/dex/hyperliquid/auto/pause")
    def hyperliquid_auto_pause():
        return hyperliquid_auto.pause()




    @app.get("/api/live/orders")
    def live_orders(exchange: Literal["binance", "coinw"], market: Literal["spot", "futures"],
                    symbol: str = Query(pattern=r"^(BTC|ETH|BNB|XRP|TRX|DOGE)USDT$")):
        return {"orders": live_execution.list_orders(exchange, market, symbol)}

    @app.post("/api/live/preview")
    def live_preview(req: LiveOrderRequest):
        try:
            req.validate_intent()
            return live_execution.preview(req.model_dump())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/live/order")
    def live_order(req: LiveConfirmation):
        try:
            entry = live_execution.previews.get(req.confirmation)
            if entry and entry[2]["exchange"] == "hyperliquid":
                with hyperliquid_auto.lock:
                    if hyperliquid_auto.blocks_manual(entry[2]["symbol"]):
                        raise ValueError("该交易对正在运行 AUTO，请先暂停")
                    return live_execution.submit(req.confirmation)
            return live_strategies.submit_manual(req.confirmation)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/live/order/query")
    def live_query(req: LiveOrderManagement):
        try:
            return live_execution.manage(req.id, "query")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/live/order/recover")
    def live_recover(req: LiveConfirmation):
        try:
            return live_execution.recover_manual(req.confirmation)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/live/order/cancel")
    def live_cancel(req: LiveOrderManagement):
        try:
            return live_execution.manage(req.id, "cancel")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/live/order/acknowledge")
    def live_acknowledge(req: LiveOrderManagement):
        try:
            return live_execution.manage(req.id, "acknowledge")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/live/strategy")
    def live_strategy(exchange: Literal["binance", "coinw"], market: Literal["spot", "futures"],
                      kind: Literal["grid", "auto"], symbol: str = Query(pattern=r"^(BTC|ETH|BNB|XRP|TRX|DOGE)USDT$")):
        return live_strategies.status(exchange, market, symbol, kind)

    @app.post("/api/live/strategy/preview")
    def live_strategy_preview(req: LiveStrategyRequest):
        if req.kind == "grid" and (req.market != "spot" or req.lower is None or req.upper is None or req.lower >= req.upper):
            raise HTTPException(400, "实盘网格需要现货市场和有效上下限")
        if req.kind == "auto" and req.exchange != "binance":
            raise HTTPException(400, "当前实盘 AUTO 支持 Binance 现货及合约")
        try:
            return live_strategies.preview(req.model_dump(mode="json"))
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/live/strategy/start")
    def live_strategy_start(req: LiveStrategyConfirmation):
        try:
            return live_strategies.start(req.confirmation, req.initial_confirm)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/live/strategy/control")
    def live_strategy_control(req: LiveStrategyControl):
        try:
            return live_strategies.control(req.id, req.action)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/symbols")
    def symbols() -> dict[str, Any]:
        return {
            "symbols": [
                {"symbol": s, **SYMBOL_META[s]} for s in SUPPORTED_SYMBOLS
            ]
        }

    @app.get("/api/market/overview")
    def market(symbol: str = "BTCUSDT") -> dict[str, Any]:
        try:
            return market_overview(cfg, symbol)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/market/depth")
    def market_depth(symbol:str="BTCUSDT"):
        try:
            raw=_request_json(replace(cfg.data,timeout_seconds=4),"/api/v3/depth",{"symbol":normalize_symbol(symbol),"limit":20})
            return {"symbol":normalize_symbol(symbol),"bids":raw.get("bids",[]),"asks":raw.get("asks",[]),"received_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
        except Exception as exc:
            raise HTTPException(502,"盘口快照暂不可用") from exc

    @app.get("/api/market/price")
    def market_price(symbol:str='BTCUSDT'):
        try:
            return fetch_ticker(replace(cfg.data,timeout_seconds=4),normalize_symbol(symbol))
        except Exception as exc:
            raise HTTPException(502,"行情暂不可用") from exc

    # ------------------------------------------------------ futures simulator
    @app.get("/api/futures/status")
    def futures_status(symbol: str = "BTCUSDT") -> dict[str, Any]:
        return futures_for(symbol).status()

    @app.post("/api/futures/order")
    def futures_order(req: FuturesOrderRequest, symbol: TradingSymbol) -> dict[str, Any]:
        svc = futures_for(symbol)
        try:
            order = svc.place_order(
                side=req.side,
                action=req.action,
                order_type=req.order_type,
                qty=req.qty,
                leverage=req.leverage,
                limit_price=req.limit_price,
                take_profit=req.take_profit,
                stop_loss=req.stop_loss,
                margin_mode=req.margin_mode,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "order": order, "status": svc.status()}

    @app.post("/api/futures/close")
    def futures_close(symbol: TradingSymbol, position_id: str | None = None) -> dict[str, Any]:
        svc = futures_for(symbol)
        try:
            order = svc.close_position(reason="manual", position_id=position_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "order": order, "status": svc.status()}

    @app.post("/api/futures/cancel")
    def futures_cancel(req: CancelFuturesRequest, symbol: TradingSymbol) -> dict[str, Any]:
        svc = futures_for(symbol)
        try:
            svc.cancel_order(req.order_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "status": svc.status()}

    @app.post("/api/futures/reset")
    def futures_reset(symbol: TradingSymbol) -> dict[str, Any]:
        svc = futures_for(symbol)
        svc.reset()
        return svc.status()

    @app.post("/api/futures/auto")
    def futures_auto(req: FuturesAutoRequest, symbol: TradingSymbol) -> dict[str, Any]:
        try:
            return futures_for(symbol).set_auto(req.enabled, req.interval, req.leverage,req.risk_fraction,req.model_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/futures/tpsl")
    def futures_tpsl(req: FuturesTpslRequest, symbol: TradingSymbol) -> dict[str, Any]:
        try:
            return futures_for(symbol).set_tpsl(
                req.take_profit, req.stop_loss, req.position_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/futures/trailing")
    def futures_trailing(req: TrailingRequest, symbol: TradingSymbol):
        try:
            return futures_for(symbol).set_trailing(req.position_id,req.enabled,req.activation_pct,req.distance_pct)
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc

    # ---------------------------------------------------------- grid simulator
    @app.get("/api/grid/status")
    def grid_status(symbol: str = "BTCUSDT") -> dict[str, Any]:
        return grid_for(symbol).status()

    @app.post("/api/grid/start")
    def grid_start(req: GridStartRequest, symbol: TradingSymbol) -> dict[str, Any]:
        try:
            return grid_for(symbol).start_grid(req.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/grid/stop")
    def grid_stop(req: GridStopRequest, symbol: TradingSymbol) -> dict[str, Any]:
        return grid_for(symbol).stop_grid(liquidate=req.liquidate, reason="manual")

    @app.post("/api/grid/reset")
    def grid_reset(symbol: TradingSymbol) -> dict[str, Any]:
        return grid_for(symbol).reset()

    @app.get("/api/status")
    def status(symbol: str = "BTCUSDT") -> dict[str, Any]:
        return spot_for(symbol).status()

    @app.get("/api/config")
    def config(symbol: str = "BTCUSDT") -> dict[str, Any]:
        symbol = normalize_symbol(symbol)
        item = symbol_config(cfg, symbol)
        return {
            "symbol": symbol,
            "base": SYMBOL_META[symbol]["base"],
            "interval": item.market.interval,
            "strategy": item.strategy_name,
            "params": item.strategy_params_for(item.strategy_name),
            "mode": spot_for(symbol).broker.mode,
            "testnet_base": item.testnet.base_url,
            "api_key_env": item.testnet.api_key_env,
            "api_secret_env": item.testnet.api_secret_env,
            "poll_seconds": item.testnet.poll_seconds,
            "quote_amount": item.testnet.quote_amount,
            "fee_rate": item.paper.fee_rate,
            "slippage_bps": item.paper.slippage_bps,
            "max_fraction": item.paper.max_fraction,
            "risk_per_trade": item.paper.risk_per_trade,
        }

    @app.get("/api/candles")
    def candles(
        interval: str = Query(default=cfg.market.interval),
        limit: int = Query(default=240, ge=50, le=3000),
        symbol: str = "BTCUSDT",
    ) -> dict[str, Any]:
        if interval not in {"30s","1m","3m","5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"}:
            raise HTTPException(status_code=400, detail="unsupported interval")
        try:
            result=chart_for(symbol,interval).candles(interval, limit)
            result['data_source']='Binance 1s → 合成30秒' if interval=='30s' else 'Binance Spot 原生 K 线'
            return result
        except TestnetError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/model")
    def model(interval: str = "4h", symbol: str = "BTCUSDT") -> dict[str, Any]:
        try:
            return spot_for(symbol).model_snapshot(interval)
        except TestnetError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/trades")
    def trades(symbol: str = "BTCUSDT") -> dict[str, Any]:
        return {"orders": spot_for(symbol).broker.recent_orders(50)}

    @app.post("/api/auto")
    def set_auto(req: AutoRequest, symbol: TradingSymbol) -> dict[str, Any]:
        svc = spot_for(symbol)
        try:
            svc.set_auto(req.enabled,req.interval,req.risk_fraction,req.model_id)
        except ValueError as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc
        except TestnetNotConfigured as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TestnetError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return svc.status()

    @app.post("/api/order")
    def order(req: OrderRequest, symbol: TradingSymbol) -> dict[str, Any]:
        svc = spot_for(symbol)
        try:
            order = svc.manual_order(
                req.side.upper(),
                quote_amount=req.quote_amount,
                reason=req.reason or "manual",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TestnetNotConfigured as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TestnetError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "order": order, "status": svc.status()}

    @app.post("/api/reset")
    def reset(symbol: TradingSymbol, amount: float | None = None) -> dict[str, Any]:
        if amount is not None and not 100 <= amount <= 100_000_000:
            raise HTTPException(status_code=400, detail="virtual balance must be 100..100,000,000")
        try:
            return spot_for(symbol).reset_paper(initial_cash=amount)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/reconnect")
    def reconnect(symbol: TradingSymbol) -> dict[str, Any]:
        svc = spot_for(symbol)
        try:
            return svc.reconnect()
        except TestnetNotConfigured as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TestnetError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app
