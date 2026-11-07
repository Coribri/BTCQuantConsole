"""Read-only local-vs-venue order audit (Binance spot/futures).

The audit never writes: the local ledger is opened with SQLite ``mode=ro`` and
the exchange side only uses GET ``allOrders``/``openOrders``/account reads.
Balances and positions are shown as ``venue_only`` facts and never enter the
diff list (decision D5 in requirements.md).
"""
from __future__ import annotations

from contextlib import closing
from decimal import Decimal, InvalidOperation
import hashlib
import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from .broker import LIVE_UNCERTAIN
from .capabilities import reason_from_code


_SCHEMA_VERSION = 1
_KIND_ORDER = {"pending": 0, "local_only": 1, "exchange_only": 2, "match": 3}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _iso_from_ms(value: Any) -> str | None:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(value) / 1000))
    except (TypeError, ValueError):
        return None


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _normal_status(value: Any) -> str:
    status = str(value or "")
    return {
        "EXPIRED_IN_MATCH": "EXPIRED",
        "PENDING_CANCEL": "CANCEL_PENDING",
        "PENDING_NEW": "ACCEPTED",
    }.get(status, status)


def _local_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "status": row.get("status"),
        "order_id": row.get("order_id"),
        "created_at": row.get("created_at"),
        "filled": row.get("filled"),
        "quote_filled": row.get("quote_filled"),
    }


def _exchange_view(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": str(order.get("orderId")) if order.get("orderId") is not None else None,
        "client_order_id": order.get("clientOrderId") or order.get("origClientOrderId"),
        "status": _normal_status(order.get("status")),
        "executed_qty": order.get("executedQty"),
        "quote_filled": order.get(
            "cummulativeQuoteQty", order.get("cumulativeQuoteQty", order.get("cumQuote"))
        ),
    }


def _read_local(
    root: Any,
    account: str | None,
    exchange: str,
    market: str,
    symbol: str,
    ledger: Any | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    facts = {
        "readable": False,
        "source": "runtime/live_orders.sqlite3",
        "orders": 0,
        "filled": 0,
        "uncertain": 0,
        "fetched_at": _now_iso(),
    }
    if not account:
        return facts, []
    if ledger is not None:
        result = ledger.read_only_orders(exchange, market, symbol, account)
        records = result.get("rows", [])
        facts["readable"] = bool(result.get("readable"))
    else:
        path = root / "runtime" / "live_orders.sqlite3"
        if not path.exists():
            return facts, []
        try:
            uri = f"file:{path.as_posix()}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=4)) as db:
                rows = db.execute(
                    "SELECT record FROM orders WHERE account=? AND exchange=? AND market=? AND symbol=?",
                    (account, exchange, market, symbol),
                ).fetchall()
            records = [json.loads(record) for record, in rows]
        except (sqlite3.Error, ValueError):
            return facts, []
        facts["readable"] = True
    facts["orders"] = len(records)
    facts["filled"] = sum(1 for row in records if row.get("status") == "FILLED")
    facts["uncertain"] = sum(1 for row in records if row.get("status") in LIVE_UNCERTAIN)
    return facts, records


def _pending(local: dict[str, Any], reason: str, note: str, exchange: Any = None) -> dict[str, Any]:
    return {
        "kind": "pending",
        "entity": "order",
        "ref": str(local.get("id") or ""),
        "reason": reason,
        "local": _local_view(local),
        "exchange": exchange,
        "note": note,
    }


def _empty_summary() -> dict[str, int]:
    return {"total": 0, "match": 0, "local_only": 0, "exchange_only": 0, "pending": 0}


def _summarise(diffs: list[dict[str, Any]]) -> dict[str, int]:
    summary = _empty_summary()
    for diff in diffs:
        summary[diff["kind"]] += 1
    summary["total"] = len(diffs)
    return summary


def _sort_key(diff: dict[str, Any]) -> tuple[int, float, str]:
    created = diff.get("local", {}).get("created_at") if diff.get("local") else None
    if diff.get("kind") == "exchange_only":
        created = diff.get("exchange", {}).get("created_at")
    epoch = 0.0
    if isinstance(created, str) and created:
        try:
            epoch = time.mktime(time.strptime(created[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            epoch = 0.0
    return (_KIND_ORDER[diff["kind"]], -epoch, str(diff.get("ref") or ""))


def _build_diffs(
    local_rows: list[dict[str, Any]], exchange_orders: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_order: dict[str, dict[str, Any]] = {}
    by_client: dict[str, dict[str, Any]] = {}
    for order in exchange_orders:
        order_id = order.get("orderId")
        if order_id is not None:
            by_order.setdefault(str(order_id), order)
        for field in ("clientOrderId", "origClientOrderId"):
            if order.get(field):
                by_client.setdefault(str(order[field]), order)

    diffs: list[dict[str, Any]] = []
    used: set[int] = set()

    for row in local_rows:
        match = None
        for key in (row.get("order_id"), row.get("client_order_id"), row.get("id")):
            if not key:
                continue
            match = by_order.get(str(key)) or by_client.get(str(key))
            if match is not None and id(match) not in used:
                break
            match = None
        if match is not None:
            used.add(id(match))

        status = str(row.get("status") or "")
        exchange_view = _exchange_view(match) if match is not None else None
        ref = str(row.get("id") or "")
        created = row.get("created_at")

        if status in LIVE_UNCERTAIN:
            diffs.append(_pending(
                row, "PENDING_RECONCILE",
                "本地状态待核对；按原确认号查询，不重复提交", exchange_view,
            ))
            continue

        if status in {"FILLED", "CANCELED"}:
            if match is None:
                diffs.append({
                    "kind": "local_only", "entity": "order", "ref": ref,
                    "reason": "LOCAL_ORDER_NOT_ON_VENUE",
                    "local": _local_view(row), "exchange": None,
                    "note": "本地记为终态，交易所全量订单中未找到该编号；需人工确认是否历史归档或账户不一致",
                })
                continue
            same_status = _normal_status(match.get("status")) == status
            same_qty = _decimal(row.get("filled")) == _decimal(match.get("executedQty"))
            if same_status and same_qty:
                diffs.append({
                    "kind": "match", "entity": "order", "ref": ref, "reason": "OK",
                    "local": _local_view(row), "exchange": exchange_view,
                    "note": "本地状态与交易所全量订单一致",
                })
            else:
                diffs.append(_pending(
                    row, "STATUS_MISMATCH",
                    "本地与交易所的状态或成交量不一致，需人工核对", exchange_view,
                ))
            continue

        if status == "ACKNOWLEDGED":
            if match is not None:
                diffs.append({
                    "kind": "match", "entity": "order", "ref": ref, "reason": "OK",
                    "local": _local_view(row), "exchange": exchange_view,
                    "note": f"本地已核对，交易所有同号记录（交易所状态 {exchange_view['status']}）",
                })
            else:
                diffs.append({
                    "kind": "local_only", "entity": "order", "ref": ref,
                    "reason": "LOCAL_ORDER_NOT_ON_VENUE",
                    "local": _local_view(row), "exchange": None,
                    "note": "本地已核对但交易所全量订单中无该编号；核对历史归档或账户一致性",
                })
            continue

        if status == "REJECTED":
            diffs.append({
                "kind": "match", "entity": "order", "ref": ref, "reason": "OK",
                "local": _local_view(row), "exchange": exchange_view,
                "note": (
                    "本地已拒绝，交易所无此单，一致" if match is None
                    else "本地已拒绝，交易所有同号记录，人工确认状态一致"
                ),
            })
            continue

        # Non-terminal local states (NEW / PARTIALLY_FILLED / ACCEPTED / ...).
        if match is None:
            diffs.append({
                "kind": "local_only", "entity": "order", "ref": ref,
                "reason": "LOCAL_ORDER_NOT_ON_VENUE",
                "local": _local_view(row), "exchange": None,
                "note": "本地存在该订单，交易所全量订单中未找到该编号",
            })
        elif _normal_status(match.get("status")) == status:
            diffs.append({
                "kind": "match", "entity": "order", "ref": ref, "reason": "OK",
                "local": _local_view(row), "exchange": exchange_view,
                "note": "本地与交易所订单状态一致",
            })
        else:
            diffs.append(_pending(
                row, "STATUS_MISMATCH",
                "本地与交易所订单状态不一致，需人工核对", exchange_view,
            ))

    for order in exchange_orders:
        if id(order) in used:
            continue
        ref = str(order.get("orderId") or order.get("clientOrderId") or order.get("origClientOrderId") or "")
        diffs.append({
            "kind": "exchange_only", "entity": "order", "ref": ref,
            "reason": "VENUE_ORDER_WITHOUT_LOCAL",
            "local": None,
            "exchange": {
                **_exchange_view(order),
                "created_at": _iso_from_ms(order.get("time") or order.get("updateTime")),
            },
            "note": "交易所存在该订单但本地账本无对应 id/client_order_id；仅展示，不据此改本地账本",
        })

    diffs.sort(key=_sort_key)
    return diffs


def _balances_from_account(exchange: str, account: dict[str, Any]) -> list[dict[str, Any]]:
    if exchange == "futures":
        assets = account.get("assets")
        if isinstance(assets, list):
            rows = []
            for item in assets:
                asset = str(item.get("asset", ""))
                wallet = float(item.get("walletBalance", 0) or 0)
                available = float(item.get("availableBalance", 0) or 0)
                if asset and (wallet or available):
                    rows.append({
                        "asset": asset, "free": available,
                        "locked": max(0.0, wallet - available), "total": wallet,
                    })
            return rows
        return []
    rows = []
    for item in account.get("balances", []) or []:
        asset = str(item.get("asset", ""))
        free = float(item.get("free", 0) or 0)
        locked = float(item.get("locked", 0) or 0)
        total = free + locked
        if asset and (total > 0 or asset == "USDT"):
            rows.append({"asset": asset, "free": free, "locked": locked, "total": total})
    return rows


def _positions_from_account(account: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in account.get("positions", []) or []:
        amount = _decimal(item.get("positionAmt"))
        if amount == 0:
            continue
        rows.append({
            "symbol": item.get("symbol"),
            "side": "LONG" if amount > 0 else "SHORT",
            "qty": str(abs(amount)),
            "entry": item.get("entryPrice"),
            "mark": item.get("markPrice"),
            "unrealized": item.get("unRealizedProfit"),
            "leverage": item.get("leverage"),
        })
    return rows


def audit_account(
    root: Any,
    exchange: str,
    market: str,
    symbol: str,
    *,
    connector: Any,
    env: str = "live",
    ledger: Any | None = None,
) -> dict[str, Any]:
    """Build the read-only account audit payload for one exchange/market/symbol."""
    started = time.monotonic()
    checked_at = _now_iso()

    account: str | None = None
    try:
        keys = connector.load()
        if keys.get("api_key") and keys.get("api_secret"):
            account = hashlib.sha256(keys["api_key"].encode()).hexdigest()
    except Exception:
        account = None

    local_facts, local_rows = _read_local(
        root, account, exchange, market, symbol, ledger=ledger
    )
    venue_calls: list[dict[str, str]] = []
    read_ok = False
    partial = False
    read_error: dict[str, Any] | None = None
    exchange_facts: dict[str, Any] = {
        "readable": False,
        "basis": "venue_only",
        "balances": [],
        "positions": [],
        "open_orders": [],
        "fetched_at": checked_at,
    }
    diffs: list[dict[str, Any]] = []

    if exchange != "binance":
        read_error = {
            "code": "NOT_IMPLEMENTED",
            "reason": "NOT_IMPLEMENTED",
            "message": "该交易所暂不支持订单核对（未接入全量订单查询）",
        }
        diffs = [
            _pending(row, "EXCHANGE_UNREADABLE", "该交易所不支持订单核对，无法与交易所比对")
            for row in local_rows
        ]
    elif account is None:
        read_error = {
            "code": None,
            "reason": "NOT_CONFIGURED",
            "message": "尚未配置该市场的 API 连接",
        }
        diffs = [
            _pending(row, "EXCHANGE_UNREADABLE", "尚未配置 API 连接，无法与交易所核对")
            for row in local_rows
        ]
    else:
        def signed_get(path: str, params: dict[str, Any] | None = None) -> Any:
            venue_calls.append({"method": "GET", "path": path})
            return connector.signed("GET", path, params)

        account_path = "/api/v3/account" if market == "spot" else "/fapi/v2/account"
        open_path = "/api/v3/openOrders" if market == "spot" else "/fapi/v1/openOrders"
        all_path = "/api/v3/allOrders" if market == "spot" else "/fapi/v1/allOrders"
        account_data: dict[str, Any] = {}
        try:
            account_data = signed_get(account_path)
            if not isinstance(account_data, dict):
                raise ValueError("交易所账户响应格式异常")
            read_ok = True
        except Exception as exc:
            code = getattr(exc, "code", None)
            read_error = {
                "code": code,
                "reason": reason_from_code(code) or "PROBE_TIMEOUT",
                "message": str(exc),
            }
            diffs = [
                _pending(row, "EXCHANGE_UNREADABLE", "交易所账户读取失败，无法核对；不假装一致")
                for row in local_rows
            ]

        if read_ok:
            try:
                open_orders = signed_get(open_path, {"symbol": symbol})
                if not isinstance(open_orders, list):
                    open_orders = []
            except Exception:
                open_orders = []
            try:
                all_orders = signed_get(all_path, {"symbol": symbol, "limit": 1000})
                if not isinstance(all_orders, list):
                    all_orders = []
            except Exception as exc:
                code = getattr(exc, "code", None)
                partial = True
                read_error = {
                    "code": code,
                    "reason": reason_from_code(code) or "PROBE_TIMEOUT",
                    "message": str(exc),
                }
                diffs = [
                    _pending(
                        row, "EXCHANGE_QUERY_FAILED",
                        "账户可读但全量订单枚举失败；按待核对处理，不据此清账",
                    )
                    for row in local_rows
                ]
            else:
                diffs = _build_diffs(local_rows, all_orders)

            exchange_facts = {
                "readable": True,
                "basis": "venue_only",
                "balances": _balances_from_account(market, account_data),
                "positions": _positions_from_account(account_data),
                "open_orders": [
                    {
                        "order_id": str(order.get("orderId")) if order.get("orderId") is not None else None,
                        "client_order_id": order.get("clientOrderId"),
                        "side": order.get("side"),
                        "type": order.get("type"),
                        "status": _normal_status(order.get("status")),
                        "price": order.get("price"),
                        "orig_qty": order.get("origQty"),
                        "executed_qty": order.get("executedQty"),
                    }
                    for order in open_orders if isinstance(order, dict)
                ],
                "fetched_at": _now_iso(),
            }

    summary = _summarise(diffs)
    return {
        "schema_version": _SCHEMA_VERSION,
        "audit_id": uuid4().hex[:12],
        "exchange": exchange,
        "market": market,
        "symbol": symbol,
        "quote": symbol[-4:] if symbol.endswith("USDT") else "USDT",
        "env": env,
        "account_ref": account[:8] if account else None,
        "checked_at": checked_at,
        "fetched_at": checked_at,
        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
        "read_ok": read_ok,
        "partial": partial,
        "read_error": read_error,
        "venue_calls": venue_calls,
        "local_facts": local_facts,
        "exchange_facts": exchange_facts,
        "strategies": [],
        "summary": summary,
        "diffs": diffs,
    }
