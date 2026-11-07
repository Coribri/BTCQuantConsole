"""Capability contract, health probes and native futures public market data.

This module is deliberately read-only.  It never creates the local order
ledger, never writes ``runtime/`` and never sends an order/cancel request.
The only non-GET call it can make upstream is Hyperliquid's public ``POST
/info`` endpoint, which carries no credentials and is not exposed as a
console route.

The four state values are decided by ``reason`` only (see api_spec.md §1).
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Callable

import requests

from .binance_spot import _mask


SCHEMA_VERSION = 1

CAPABILITY_IDS = ("market_data", "account_read", "manual_order", "auto_strategy", "grid")
ACCOUNT_CAPABILITIES = ("account_read", "manual_order", "auto_strategy", "grid")
TRADE_CAPABILITIES = ("manual_order", "auto_strategy", "grid")

EXCHANGES = ("binance", "coinw", "hyperliquid")
MARKETS = ("spot", "futures")

# (exchange, market) -> support for the five capabilities, in contract order.
SUPPORT: dict[tuple[str, str], dict[str, str]] = {
    ("binance", "spot"): {
        "market_data": "supported", "account_read": "supported",
        "manual_order": "supported", "auto_strategy": "supported", "grid": "supported",
    },
    ("binance", "futures"): {
        "market_data": "supported", "account_read": "supported",
        "manual_order": "supported", "auto_strategy": "supported", "grid": "unsupported",
    },
    ("coinw", "spot"): {
        "market_data": "supported", "account_read": "supported",
        "manual_order": "supported", "auto_strategy": "unsupported", "grid": "supported",
    },
    ("coinw", "futures"): {
        "market_data": "supported", "account_read": "supported",
        "manual_order": "supported", "auto_strategy": "unsupported", "grid": "unsupported",
    },
    ("hyperliquid", "futures"): {
        "market_data": "supported", "account_read": "supported",
        "manual_order": "testnet_only", "auto_strategy": "testnet_only", "grid": "unsupported",
    },
}

SCOPES: tuple[tuple[str, str], ...] = tuple(SUPPORT)

# reason -> (state, retryable).  This is the single closed enum.
REASONS: dict[str, tuple[str, bool]] = {
    "OK": ("READY", False),
    "UNVERIFIED": ("UNKNOWN", True),
    "PAPER_MODE": ("BLOCKED", False),
    "NOT_CONFIGURED": ("BLOCKED", True),
    "AUTH_REJECTED": ("BLOCKED", True),
    "AUTH_INVALID_KEY": ("BLOCKED", True),
    "SIGNATURE_INVALID": ("BLOCKED", True),
    "TIME_SKEW": ("BLOCKED", True),
    "PERMISSION_DENIED": ("BLOCKED", True),
    "PENDING_RECONCILE": ("PARTIAL", True),
    "VENUE_UNAVAILABLE": ("UNKNOWN", True),
    "PROBE_TIMEOUT": ("UNKNOWN", True),
    "MARKET_DATA_UNAVAILABLE": ("UNKNOWN", True),
    "NOT_IMPLEMENTED": ("BLOCKED", False),
    "OUT_OF_SCOPE": ("BLOCKED", False),
}

# Fixed user-facing hints.  Values containing {placeholders} are formatted by
# ``hint_for`` and must otherwise stay byte-for-byte identical to the contract.
_HINTS = {
    "OK": "已探测可用。",
    "UNVERIFIED": "尚未核验该能力或交易权限，状态未知。点击“重新检测”获取实时结果。",
    "PAPER_MODE": "当前为 PAPER 模式，实盘接口按设计禁用。切换到实盘模式后重新检测。",
    "NOT_CONFIGURED": "尚未配置该市场的 API 连接。保存 Key 与 Secret 后可重新检测。",
    "AUTH_REJECTED": (
        "Binance 拒绝认证（-2015）。请依次核对：① API Key 是否有效且未被删除；"
        "② 当前出口 IP 是否在 Key 的白名单内；③ Key 是否具备所需读取/交易权限。"
        "该错误码无法区分以上三类原因，本机不会自动重试或恢复 AUTO。"
    ),
    "AUTH_INVALID_KEY": (
        "Binance API Key 无效或格式错误（-2014）。请在 Binance 后台重新生成并保存 Key 与 Secret。"
    ),
    "SIGNATURE_INVALID": (
        "交易所签名校验失败（-1022/6001）。请确认 Secret 与 Key 对应；仍失败时请重新保存连接。"
    ),
    "TIME_SKEW": (
        "Binance 拒绝请求时间戳（-1021）。请先同步系统时间，再手动重新检测；本机不会自动重签重试。"
    ),
    "PERMISSION_DENIED": (
        "读取成功，但该 API Key 或账户没有当前市场的交易权限。请到交易所后台开启对应权限。"
    ),
    "PENDING_RECONCILE": (
        "存在待核对（-2013 或回执未确认）的订单，相关交易对禁止开新单。"
        "请按原确认号核对，系统不会重复提交，也不会据此清账。"
    ),
    "VENUE_UNAVAILABLE": "交易所暂时不可用或限流（{code}），结果未知。请稍后手动重新检测。",
    "PROBE_TIMEOUT": (
        "读取失败或超时，结果未知。请检查网络后手动重新检测；"
        "已提交的订单请继续按确认号核对，不要重复提交。"
    ),
    "MARKET_DATA_UNAVAILABLE": "公开行情读取失败，无法确认该市场状态。请稍后手动重新检测。",
    "NOT_IMPLEMENTED": "当前版本不支持该组合（{exchange} {market} {capability}）。如需接入请单独立项。",
    "OUT_OF_SCOPE": "本迭代不覆盖该能力（如 USDC 计价）。",
}

# Exchange error codes that have an unambiguous capability reason.
_CODE_REASONS = {
    -2015: "AUTH_REJECTED",
    -2014: "AUTH_INVALID_KEY",
    -1022: "SIGNATURE_INVALID",
    6001: "SIGNATURE_INVALID",
    -1021: "TIME_SKEW",
    6003: "PERMISSION_DENIED",
    -2010: "PERMISSION_DENIED",
    -1000: "VENUE_UNAVAILABLE",
    -1001: "VENUE_UNAVAILABLE",
    -1006: "VENUE_UNAVAILABLE",
    -1007: "VENUE_UNAVAILABLE",
    -2013: "VENUE_UNAVAILABLE",
}

_FIXED_ATTRIBUTION = {
    "api_key_invalid": (
        "API Key 无效或已删除",
        "Binance 后台 → API 管理：Key 是否启用、是否与 Secret 配对",
    ),
    "ip_not_whitelisted": (
        "出口 IP 不在白名单",
        "核对 Binance API 的 IP 白名单与本机公网出口 IP",
    ),
    "permission_missing": (
        "读取/交易权限不足",
        "核对 Key 是否开启现货/合约交易与读取权限",
    ),
    "clock_skew": ("本机时间与交易所时间偏差过大", "同步系统时间后重新检测"),
    "network_or_venue": ("网络不通或交易所不可达", "检查网络/代理后手动重新检测"),
}


class MarketDataUnavailable(RuntimeError):
    """Raised when the public futures data cannot be used for a native view."""


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_iso() -> str:
    """Seam kept as a module function so tests can freeze it."""
    return utc_now()


def hint_for(
    reason: str,
    *,
    exchange: str = "",
    market: str = "",
    capability: str = "",
    code: Any = None,
) -> str:
    template = _HINTS[reason]
    if reason == "VENUE_UNAVAILABLE":
        return template.format(code="未知" if code is None else code)
    if reason == "NOT_IMPLEMENTED":
        return template.format(
            exchange=exchange or "-", market=market or "-", capability=capability or "-"
        )
    return template


def state_for(reason: str) -> tuple[str, bool]:
    return REASONS[reason]


def reason_from_code(code: Any) -> str | None:
    if isinstance(code, bool):  # bool is an int subclass; never a venue code
        return None
    if isinstance(code, str):
        try:
            code = int(code)
        except ValueError:
            return None
    if isinstance(code, int):
        return _CODE_REASONS.get(code, "VENUE_UNAVAILABLE")
    return None


def reason_from_snapshot(
    *,
    configured: bool,
    snapshot: dict[str, Any] | None,
    trade_permission: str = "unavailable",
) -> str:
    """Map one account probe to the closed reason enum (no message regex)."""
    snapshot = snapshot or {}
    if not configured:
        return "NOT_CONFIGURED"
    code_reason = reason_from_code(snapshot.get("error_code"))
    if code_reason:
        return code_reason
    if snapshot.get("error"):
        return "PROBE_TIMEOUT"
    if trade_permission == "denied":
        return "PERMISSION_DENIED"
    if trade_permission == "confirmed":
        return "OK"
    return "UNVERIFIED"


def _sorted_symbols(symbols: Any) -> list[str]:
    """Deduplicate and sort ledger-derived symbols without mutating the input."""
    return sorted({str(item) for item in (symbols or []) if item})


def make_node(
    reason: str,
    *,
    support: str = "supported",
    evidence: str = "config",
    code: Any = None,
    checked_at: str | None = None,
    exchange: str = "",
    market: str = "",
    capability: str = "",
    blocked_symbols: list[str] | None = None,
) -> dict[str, Any]:
    if reason not in REASONS:
        raise KeyError(f"unknown capability reason: {reason}")
    state, retryable = REASONS[reason]
    node: dict[str, Any] = {
        "state": state,
        "reason": reason,
        "hint": hint_for(
            reason, exchange=exchange, market=market, capability=capability, code=code
        ),
        "retryable": retryable,
        "support": support,
        "evidence": evidence,
        "code": code,
        "checked_at": checked_at,
    }
    if capability in TRADE_CAPABILITIES:
        node["blocked_symbols"] = list(blocked_symbols or [])
    return node


def attribution_for(reason: str, code: Any = None) -> dict[str, Any]:
    """Return the bounded attribution block; never claims a certainty it lacks."""
    def candidate(ident: str, likelihood: str = "equal") -> dict[str, str]:
        label, check = _FIXED_ATTRIBUTION[ident]
        return {"id": ident, "label": label, "check": check, "likelihood": likelihood}

    if code == -2015:
        return {
            "code": code,
            "confidence": "undetermined",
            "candidates": [
                candidate("api_key_invalid"),
                candidate("ip_not_whitelisted"),
                candidate("permission_missing"),
            ],
        }
    if reason == "TIME_SKEW":
        return {"code": code, "confidence": "determined", "candidates": [candidate("clock_skew")]}
    if reason == "PROBE_TIMEOUT":
        return {
            "code": code,
            "confidence": "undetermined",
            "candidates": [candidate("network_or_venue")],
        }
    if reason == "PERMISSION_DENIED":
        return {
            "code": code,
            "confidence": "determined",
            "candidates": [candidate("permission_missing")],
        }
    if reason in {"AUTH_REJECTED", "AUTH_INVALID_KEY", "SIGNATURE_INVALID"}:
        ident = {
            "AUTH_REJECTED": "api_key_invalid",
            "AUTH_INVALID_KEY": "api_key_invalid",
            "SIGNATURE_INVALID": "permission_missing",
        }[reason]
        return {"code": code, "confidence": "determined", "candidates": [candidate(ident)]}
    if reason in {"VENUE_UNAVAILABLE", "MARKET_DATA_UNAVAILABLE"}:
        return {
            "code": code,
            "confidence": "determined",
            "candidates": [candidate("network_or_venue")],
        }
    if code is None:
        return {"code": None, "confidence": "undetermined", "candidates": []}
    return {"code": code, "confidence": "determined", "candidates": []}


def public_json(url: str, params: dict[str, Any] | None = None, *, timeout: float = 5.0) -> Any:
    """Public market-data GET; tests replace this single seam."""
    response = requests.get(url, params=params, timeout=timeout, allow_redirects=False)
    response.raise_for_status()
    return response.json()


def _hyperliquid_public_info() -> Any:
    response = requests.post(
        "https://api.hyperliquid.xyz/info",
        json={"type": "metaAndAssetCtxs"},
        timeout=5,
        allow_redirects=False,
    )
    response.raise_for_status()
    return response.json()


def _iso_from_ms(value: Any) -> str | None:
    try:
        seconds = int(value) / 1000
    except (TypeError, ValueError):
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds))


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _quote(symbol: str) -> str:
    return symbol[-4:] if symbol.endswith("USDT") else "USDT"


def futures_market_snapshot(
    symbol: str,
    *,
    connector: Any | None = None,
    paper: bool = False,
) -> dict[str, Any]:
    """Read Binance USDⓈ-M public mark/index/funding and last price.

    A missing optional account field is a *degradation*, never an exception.
    Public data failure raises ``MarketDataUnavailable`` so the route can map it
    to HTTP 502 without swallowing programming errors.
    """
    symbol = str(symbol or "BTCUSDT").upper()
    started = time.monotonic()
    try:
        premium = public_json(
            "https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": symbol}
        )
        ticker = public_json(
            "https://fapi.binance.com/fapi/v1/ticker/price", {"symbol": symbol}
        )
    except Exception as exc:  # network / HTTP / JSON
        raise MarketDataUnavailable(str(exc)) from exc
    if not isinstance(premium, dict) or _float_or_none(premium.get("markPrice")) is None:
        raise MarketDataUnavailable("mark price missing")
    mark_price = _float_or_none(premium.get("markPrice"))
    if not mark_price or mark_price <= 0:
        raise MarketDataUnavailable("mark price invalid")

    degradations: list[str] = []
    margin_mode: str | None = None
    position_mode: str | None = None

    if paper:
        degradations.append("paper_mode")
    else:
        configured = False
        if connector is not None:
            try:
                keys = connector.load()
                configured = bool(keys.get("api_key") and keys.get("api_secret"))
            except Exception:
                configured = False
        if configured:
            try:
                positions = connector.signed("GET", "/fapi/v1/positionRisk")
                if isinstance(positions, list) and positions:
                    margin_mode = str(positions[0].get("marginType") or "").upper() or None
            except Exception:
                margin_mode = None
            try:
                dual = connector.signed("GET", "/fapi/v1/positionSide/dual")
                if isinstance(dual, dict) and "dualSidePosition" in dual:
                    position_mode = "HEDGE" if dual.get("dualSidePosition") else "ONE_WAY"
            except Exception:
                position_mode = None
        if margin_mode is None:
            degradations.append("margin_mode_unavailable")
        if position_mode is None:
            degradations.append("position_mode_unavailable")

    if margin_mode is None and "margin_mode_unavailable" not in degradations:
        degradations.append("margin_mode_unavailable")
    if position_mode is None and "position_mode_unavailable" not in degradations:
        degradations.append("position_mode_unavailable")

    funding_rate = _float_or_none(premium.get("lastFundingRate"))
    index_price = _float_or_none(premium.get("indexPrice"))
    last_price = _float_or_none(ticker.get("price")) if isinstance(ticker, dict) else None

    return {
        "schema_version": SCHEMA_VERSION,
        "exchange": "binance",
        "market": "futures",
        "symbol": symbol,
        "quote": _quote(symbol),
        "source": "binance_futures_public",
        "source_label": "Binance Futures",
        "native": True,
        "fetched_at": _now_iso(),
        "latency_ms": max(0, int((time.monotonic() - started) * 1000)),
        "mark_price": mark_price,
        "index_price": index_price,
        "last_price": last_price,
        "funding_rate": funding_rate,
        "next_funding_at": _iso_from_ms(premium.get("nextFundingTime")),
        "margin_mode": margin_mode,
        "position_mode": position_mode,
        "account_scoped": False,
        "degradations": degradations,
    }


class CapabilityRegistry:
    """Probe/cache the capability matrix for one console process."""

    TTL_SECONDS = 60.0

    def __init__(
        self,
        cfg: Any,
        connections: dict[tuple[str, str], Any],
        *,
        disable_live: bool = False,
        preflight: Any | None = None,
        ledger: Any | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.cfg = cfg
        self.connections = connections
        self.disable_live = bool(disable_live)
        self.preflight = preflight
        self.ledger = ledger
        self._clock = clock or _now_iso
        self._cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._lock = threading.RLock()
        self.probe_counts: dict[tuple[str, str, str], int] = {
            (exchange, market, capability): 0
            for exchange, market in SCOPES
            for capability in CAPABILITY_IDS
        }

    # -- public API -----------------------------------------------------
    @property
    def env(self) -> str:
        return "paper" if self.disable_live else "live"

    def matrix(
        self,
        exchange: str | None = None,
        market: str | None = None,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        selected = self._select_scopes(exchange, market)
        payload: dict[str, Any] = {}
        for scope in selected:
            if refresh and not self.disable_live:
                result = self._probe_scope(scope)
            else:
                with self._lock:
                    cached = self._cache.get(scope)
                    fresh = cached is not None and time.monotonic() - cached[0] < self.TTL_SECONDS
                result = cached[1] if fresh else self._config_scope(scope)
            ex, mk = scope
            payload.setdefault(ex, {})[mk] = self._scope_payload(result)
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self._clock(),
            "env": self.env,
            "refresh": bool(refresh),
            "capabilities": payload,
        }

    def cached_matrix(self) -> dict[str, Any]:
        """Matrix for ``/api/live/features``: cached data only, zero probes."""
        return self.matrix(refresh=False)["capabilities"]

    def features(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "env": self.env,
            "generated_at": self._clock(),
            "manual": not self.disable_live,
            "grid": not self.disable_live,
            "auto": [] if self.disable_live else ["binance"],
            "capabilities": self.cached_matrix(),
        }

    def health(self, exchange: str, market: str) -> dict[str, Any]:
        scope = (exchange, market)
        if scope not in SCOPES:
            raise ValueError("unsupported exchange/market")
        result = self._config_scope(scope) if self.disable_live else self._probe_scope(scope)
        meta = result["account"]
        checked_at = meta.get("checked_at") or self._clock()
        error = meta.get("error")
        return {
            "schema_version": SCHEMA_VERSION,
            "exchange": exchange,
            "market": market,
            "env": self.env,
            "configured": bool(meta.get("configured")),
            "credential_masked": meta.get("credential_masked", ""),
            "checked_at": checked_at,
            "duration_ms": int(meta.get("duration_ms") or 0),
            "read_ok": bool(meta.get("read_ok")),
            "trade_permission": meta.get("trade_permission", "unavailable"),
            "error": error,
            "attribution": meta.get("attribution")
            or attribution_for(meta.get("reason", "UNVERIFIED"), meta.get("code")),
            "venue_calls": list(meta.get("venue_calls") or []),
            "capabilities": {
                cap: result["account_nodes"][cap] if cap != "market_data" else result["market_node"]
                for cap in CAPABILITY_IDS
            },
        }

    # -- scope selection / cache ---------------------------------------
    @staticmethod
    def _select_scopes(exchange: str | None, market: str | None) -> list[tuple[str, str]]:
        ex = str(exchange).strip().lower() if exchange else None
        mk = str(market).strip().lower() if market else None
        if ex is not None and ex not in EXCHANGES:
            raise ValueError("exchange 必须是 binance/coinw/hyperliquid")
        if mk is not None and mk not in MARKETS:
            raise ValueError("market 必须是 spot/futures")
        if ex and mk:
            if (ex, mk) not in SCOPES:
                raise ValueError("该 exchange/market 组合不在覆盖范围")
            return [(ex, mk)]
        return [
            scope for scope in SCOPES
            if (ex is None or scope[0] == ex) and (mk is None or scope[1] == mk)
        ]

    def _cache_scope(self, scope: tuple[str, str], result: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._cache[scope] = (time.monotonic(), result)
        return result

    # -- config only (no probe) ----------------------------------------
    def _config_scope(self, scope: tuple[str, str]) -> dict[str, Any]:
        exchange, market = scope
        configured = self._configured(scope)
        blocked = _sorted_symbols(self._blocked_symbols(scope))
        nodes: dict[str, Any] = {}
        for cap in ACCOUNT_CAPABILITIES:
            support = SUPPORT[scope][cap]
            if support == "unsupported":
                node = make_node(
                    "NOT_IMPLEMENTED", support=support, evidence="config",
                    exchange=exchange, market=market, capability=cap,
                )
            elif self.disable_live:
                node = make_node(
                    "PAPER_MODE", support=support, evidence="config",
                    exchange=exchange, market=market, capability=cap,
                )
            elif exchange == "hyperliquid":
                reason = "UNVERIFIED" if cap == "account_read" else "NOT_IMPLEMENTED"
                node = make_node(
                    reason, support=support, evidence="config",
                    exchange=exchange, market=market, capability=cap,
                )
            elif not configured:
                node = make_node(
                    "NOT_CONFIGURED", support=support, evidence="config",
                    exchange=exchange, market=market, capability=cap,
                )
            else:
                node = make_node(
                    "UNVERIFIED", support=support, evidence="config",
                    exchange=exchange, market=market, capability=cap,
                )
            if (
                blocked
                and cap in TRADE_CAPABILITIES
                and support != "unsupported"
                and not self.disable_live
                and exchange != "hyperliquid"
                and node["reason"] in {"OK", "UNVERIFIED"}
            ):
                # The pending flag is derived from the local ledger, not from a
                # venue probe, so it must never claim ``evidence="probe"``.
                node = make_node(
                    "PENDING_RECONCILE", support=support, evidence="config",
                    checked_at=None, exchange=exchange, market=market,
                    capability=cap, blocked_symbols=blocked,
                )
            nodes[cap] = node
        market_node = make_node(
            "UNVERIFIED", support=SUPPORT[scope]["market_data"], evidence="config",
            exchange=exchange, market=market, capability="market_data",
        )
        return {
            "scope": scope,
            "market_node": market_node,
            "account_nodes": nodes,
            "account": self._empty_account_meta(scope, configured),
        }

    def _empty_account_meta(self, scope: tuple[str, str], configured: bool) -> dict[str, Any]:
        exchange, market = scope
        connector = self.connections.get(scope)
        masked = ""
        if connector is not None:
            try:
                keys = connector.load()
                masked = _mask(keys.get("api_key", ""))
            except Exception:
                masked = ""
        if self.disable_live:
            reason = "PAPER_MODE"
            message = "当前为 PAPER 模式"
        elif not configured:
            reason = "NOT_CONFIGURED"
            message = "尚未配置该市场的 API 连接"
        else:
            reason = "UNVERIFIED"
            message = None
        error = None
        if reason != "OK":
            error = {
                "code": None,
                "reason": reason,
                "message": message,
                "hint": hint_for(reason, exchange=exchange, market=market),
                "retryable": REASONS[reason][1],
            }
        return {
            "configured": configured,
            "credential_masked": masked,
            "read_ok": False,
            "trade_permission": "unavailable",
            "reason": reason,
            "code": None,
            "message": message,
            "hint": hint_for(reason, exchange=exchange, market=market),
            "error": error,
            "attribution": attribution_for(reason),
            "venue_calls": [],
            "checked_at": self._clock(),
            "duration_ms": 0,
        }

    # -- probing --------------------------------------------------------
    def _probe_scope(self, scope: tuple[str, str]) -> dict[str, Any]:
        exchange, market = scope
        started = time.monotonic()
        for cap in ACCOUNT_CAPABILITIES:
            if SUPPORT[scope][cap] != "unsupported":
                self.probe_counts[(exchange, market, cap)] += 1
        self.probe_counts[(exchange, market, "market_data")] += 1

        market_reason, market_code = self._probe_market_data(scope)
        market_node = make_node(
            market_reason,
            support=SUPPORT[scope]["market_data"],
            evidence="probe",
            code=market_code,
            checked_at=self._clock(),
            exchange=exchange,
            market=market,
            capability="market_data",
        )
        if exchange == "hyperliquid":
            result = self._config_scope(scope)
            result["market_node"] = market_node
            result["account"] = self._empty_account_meta(scope, self._configured(scope))
            return self._cache_scope(scope, result)

        account = self._probe_account(scope, started)
        blocked = _sorted_symbols(self._blocked_symbols(scope))
        nodes: dict[str, Any] = {}
        for cap in ACCOUNT_CAPABILITIES:
            support = SUPPORT[scope][cap]
            if support == "unsupported":
                node = make_node(
                    "NOT_IMPLEMENTED", support=support, evidence="config",
                    exchange=exchange, market=market, capability=cap,
                )
            elif (
                blocked
                and cap in TRADE_CAPABILITIES
                and account["reason"] in {"OK", "UNVERIFIED"}
            ):
                # Same rule as config scope: the pending flag comes from the
                # local ledger, so it is local evidence even after a probe.
                node = make_node(
                    "PENDING_RECONCILE", support=support, evidence="config",
                    checked_at=None, exchange=exchange, market=market,
                    capability=cap, blocked_symbols=blocked,
                )
            else:
                node = make_node(
                    account["reason"], support=support, evidence="probe",
                    code=account["code"], checked_at=account["checked_at"],
                    exchange=exchange, market=market, capability=cap,
                    blocked_symbols=blocked,
                )
            nodes[cap] = node
        return self._cache_scope(scope, {
            "scope": scope,
            "market_node": market_node,
            "account_nodes": nodes,
            "account": account,
        })

    def _probe_market_data(self, scope: tuple[str, str]) -> tuple[str, Any]:
        exchange, market = scope
        symbol = "BTCUSDT"
        try:
            if exchange == "binance" and market == "spot":
                data = public_json(
                    "https://api.binance.com/api/v3/ticker/price", {"symbol": symbol}
                )
                if not _float_or_none(data.get("price")):
                    raise ValueError("price missing")
            elif exchange == "binance":
                data = public_json(
                    "https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": symbol}
                )
                if not _float_or_none(data.get("markPrice")):
                    raise ValueError("markPrice missing")
            elif exchange == "coinw" and market == "spot":
                data = public_json(
                    "https://api.coinw.com/api/v1/public", {"command": "returnTicker"}
                )
                pair = (data.get("data") or {}).get("BTC_USDT") if isinstance(data, dict) else None
                if not pair or _float_or_none(pair.get("last")) is None:
                    raise ValueError("ticker missing")
            elif exchange == "coinw":
                data = public_json(
                    "https://api.coinw.com/v1/perpum/instruments", {"name": "BTC"}
                )
                rows = data.get("data") if isinstance(data, dict) else data
                if not rows:
                    raise ValueError("instrument missing")
            else:
                _hyperliquid_public_info()
        except Exception:
            return "MARKET_DATA_UNAVAILABLE", None
        return "OK", None

    def _probe_account(self, scope: tuple[str, str], started: float) -> dict[str, Any]:
        exchange, market = scope
        connector = self.connections[scope]
        try:
            keys = connector.load()
        except Exception:
            keys = {}
        configured = bool(keys.get("api_key") and keys.get("api_secret"))
        checked_at = self._clock()
        if not configured:
            meta = self._empty_account_meta(scope, False)
            meta["checked_at"] = checked_at
            meta["duration_ms"] = int((time.monotonic() - started) * 1000)
            return meta

        snapshot: dict[str, Any]
        trade_permission = "unavailable"
        try:
            if self.preflight is not None:
                snapshot = self.preflight.account(connector, exchange, market)
                trade_permission = snapshot.get("trade_permission", "unavailable")
            else:
                snapshot = connector.snapshot()
                trade_permission = "unknown" if not snapshot.get("error") else "unavailable"
        except Exception as exc:
            snapshot = {
                "configured": True,
                "error": str(exc),
                "error_code": getattr(exc, "code", None),
            }
            trade_permission = "unavailable"

        reason = reason_from_snapshot(
            configured=bool(snapshot.get("configured", True)),
            snapshot=snapshot,
            trade_permission=trade_permission,
        )
        # CoinW snapshots historically lose the numeric code; do not guess a
        # timeout when the venue only returned a message.
        if exchange == "coinw" and reason == "PROBE_TIMEOUT":
            reason = "VENUE_UNAVAILABLE"
        code = snapshot.get("error_code")
        if reason == "PERMISSION_DENIED":
            code = code if code is not None else None
        message = snapshot.get("error") or snapshot.get("permission_error")
        read_ok = bool(snapshot.get("configured", True)) and not snapshot.get("error")
        venue_calls = self._venue_calls(scope, snapshot, trade_permission, read_ok)
        error = None
        if reason != "OK":
            error = {
                "code": code,
                "reason": reason,
                "message": message or hint_for(reason, exchange=exchange, market=market, code=code),
                "hint": hint_for(reason, exchange=exchange, market=market, code=code),
                "retryable": REASONS[reason][1],
            }
        return {
            "configured": True,
            "credential_masked": _mask(keys.get("api_key", "")),
            "read_ok": read_ok,
            "trade_permission": trade_permission,
            "reason": reason,
            "code": code,
            "message": message,
            "hint": hint_for(reason, exchange=exchange, market=market, code=code),
            "error": error,
            "attribution": attribution_for(reason, code),
            "venue_calls": venue_calls,
            "checked_at": checked_at,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    @staticmethod
    def _venue_calls(
        scope: tuple[str, str],
        snapshot: dict[str, Any],
        trade_permission: str,
        read_ok: bool,
    ) -> list[dict[str, str]]:
        exchange, market = scope
        if exchange == "binance":
            account_paths = ["/api/v3/account"] if market == "spot" else [
                "/fapi/v2/account", "/fapi/v2/positionRisk"
            ]
            calls = [{"method": "GET", "path": account_paths[0]}]
            if read_ok:
                calls = [{"method": "GET", "path": path} for path in account_paths]
                if trade_permission in {"confirmed", "unknown"}:
                    calls.append({"method": "GET", "path": "/sapi/v1/account/apiRestrictions"})
            return calls
        if market == "spot":
            return [{"method": "POST", "path": "/api/v1/private"}]
        return [{"method": "GET", "path": "/v1/perpum/account/getUserAssets"}]

    # -- local helpers --------------------------------------------------
    def _configured(self, scope: tuple[str, str]) -> bool:
        connector = self.connections.get(scope)
        if connector is None:
            return False
        try:
            keys = connector.load()
        except Exception:
            return False
        return bool(keys.get("api_key") and keys.get("api_secret"))

    def _blocked_symbols(self, scope: tuple[str, str]) -> list[str]:
        if self.disable_live or self.ledger is None or scope[0] == "hyperliquid":
            return []
        connector = self.connections.get(scope)
        if connector is None:
            return []
        try:
            keys = connector.load()
        except Exception:
            return []
        if not keys.get("api_key") or not keys.get("api_secret"):
            return []
        account = hashlib.sha256(keys["api_key"].encode()).hexdigest()
        try:
            return self.ledger.read_only_uncertain_symbols(scope[0], scope[1], account)
        except Exception:
            return []

    @staticmethod
    def _scope_payload(result: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"quote": "USDT"}
        payload["market_data"] = result["market_node"]
        for cap in ACCOUNT_CAPABILITIES:
            payload[cap] = result["account_nodes"][cap]
        return payload
