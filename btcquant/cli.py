"""Command-line interface.

Examples
--------
python -m btcquant download
python -m btcquant backtest --strategy trend
python -m btcquant compare
python -m btcquant paper --once
python -m btcquant paper --loop --interval 60
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import logging
from pathlib import Path
import sys
import threading
from typing import Any

import pandas as pd

from .config import AppConfig, load_config
from .data import describe_data, load_klines
from .engine import BacktestResult, run_backtest
from .paper import PaperTrader, format_status
from .report import write_compare_report, write_report
from .strategies import generate_signals


def _config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="config.toml", help="path to config.toml")


def _utc_timestamp(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _run_one(
    cfg: AppConfig,
    *,
    strategy: str,
    start: str | None,
    end: str | None,
    force_download: bool,
    sizing: str | None,
    risk: float | None,
) -> tuple[BacktestResult, dict[str, Any], Any]:
    # Always load the full history so indicators have warm-up.  The requested
    # start/end only restrict the *trading* window, not the feature history.
    full = load_klines(cfg.data, cfg.market, cfg.market.start, None, force=force_download)
    params = cfg.strategy_params_for(strategy)
    signals_all = generate_signals(full, strategy, params)

    mask = pd.Series(True, index=full.index)
    if start:
        mask &= full["open_time"] >= _utc_timestamp(start)
    if end:
        mask &= full["open_time"] < _utc_timestamp(end)
    view = full[mask].reset_index(drop=True)
    signals = signals_all[mask].reset_index(drop=True)
    if view.empty or signals.empty:
        raise SystemExit("no bars in the requested start/end window")

    bt = cfg.backtest
    if sizing:
        if sizing not in ("fixed_fraction", "risk"):
            raise SystemExit("--sizing must be fixed_fraction or risk")
        bt = replace(bt, sizing=sizing)
    if risk is not None:
        bt = replace(bt, risk_per_trade=risk)
    result = run_backtest(view, signals, bt, strategy=strategy, params=params)
    return result, describe_data(view), view


def _print_result(result: BacktestResult) -> None:
    m, bm = result.metrics, result.benchmark_metrics
    print("")
    print(f"=== {result.strategy.upper()} | BTC spot long-only | {m.get('start', '')[:10]} -> {m.get('end', '')[:10]} ===")
    print(f"{'metric':<16}{'strategy':>14}{'buy & hold':>14}")
    print("-" * 44)
    rows = [
        ("total return", f"{m.get('total_return', 0):.1%}", f"{bm.get('total_return', 0):.1%}"),
        ("CAGR", f"{m.get('cagr', 0):.1%}", f"{bm.get('cagr', 0):.1%}"),
        ("volatility", f"{m.get('volatility', 0):.1%}", f"{bm.get('volatility', 0):.1%}"),
        ("Sharpe", f"{m.get('sharpe', 0):.2f}", f"{bm.get('sharpe', 0):.2f}"),
        ("Sortino", f"{m.get('sortino', 0):.2f}", f"{bm.get('sortino', 0):.2f}"),
        ("max drawdown", f"{m.get('max_drawdown', 0):.1%}", f"{bm.get('max_drawdown', 0):.1%}"),
        ("Calmar", f"{m.get('calmar', 0):.2f}", f"{bm.get('calmar', 0):.2f}"),
        ("exposure", f"{m.get('exposure', 0):.1%}", f"{bm.get('exposure', 0):.1%}"),
        ("trades", f"{int(m.get('trades', 0))}", "-"),
        ("win rate", f"{m.get('win_rate', 0):.1%}", "-"),
        ("profit factor", f"{m.get('profit_factor', 0):.2f}", "-"),
        ("final equity", f"{m.get('final_equity', 0):,.0f}", f"{bm.get('final_equity', 0):,.0f}"),
    ]
    for label, a, b in rows:
        print(f"{label:<16}{a:>14}{b:>14}")
    print("")
    print(f"trades CSV: {result.trades.shape[0]} rows in memory")


def _write_outputs(result: BacktestResult, cfg: AppConfig, desc: dict[str, Any]) -> dict[str, Path]:
    return write_report(result, cfg.report_dir, data_desc=desc)


def cmd_download(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    df = load_klines(cfg.data, cfg.market, args.start or cfg.market.start, args.end, force=args.force)
    print(json.dumps(describe_data(df), indent=2, ensure_ascii=False))
    print(f"cache: {cfg.data.dir / (cfg.market.symbol + '_' + cfg.market.interval + '.csv')}")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    result, desc, _ = _run_one(
        cfg,
        strategy=args.strategy or cfg.strategy_name,
        start=args.start,
        end=args.end,
        force_download=args.force_download,
        sizing=args.sizing,
        risk=args.risk,
    )
    _print_result(result)
    if args.json:
        print(json.dumps({"metrics": result.metrics, "benchmark": result.benchmark_metrics}, indent=2, default=str))
    if not args.no_report:
        paths = _write_outputs(result, cfg, desc)
        print(f"report: {paths['html']}")
        print(f"chart : {paths['png']}")
        print(f"trades: {paths['trades']}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    results: list[BacktestResult] = []
    desc: dict[str, Any] = {}
    for strategy in ("trend", "donchian", "meanrev"):
        result, desc, _ = _run_one(
            cfg,
            strategy=strategy,
            start=args.start,
            end=args.end,
            force_download=args.force_download,
            sizing=args.sizing,
            risk=args.risk,
        )
        results.append(result)
        m = result.metrics
        print(
            f"{strategy:<9} ret {m.get('total_return', 0):>8.1%}  CAGR {m.get('cagr', 0):>7.1%}  "
            f"DD {m.get('max_drawdown', 0):>7.1%}  Sharpe {m.get('sharpe', 0):>5.2f}  "
            f"trades {int(m.get('trades', 0)):>3d}  win {m.get('win_rate', 0):>5.1%}"
        )
    path = write_compare_report(results, cfg.report_dir, data_desc=desc)
    print(f"comparison report: {path}")
    return 0


def cmd_paper(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    trader = PaperTrader(cfg, strategy=args.strategy or cfg.strategy_name)

    if args.reset:
        if not args.yes:
            answer = input("Reset the paper account? Old files will be archived. [y/N] ").strip().lower()
            if answer != "y":
                print("cancelled")
                return 1
        archive = trader.reset(archive=True)
        print(f"paper account reset; archive: {archive}")

    if args.status:
        print(format_status(trader.summary()))
        return 0

    if args.once:
        summary = trader.step()
        print(format_status(summary))
        return 0

    if args.loop:
        print("PAPER ONLY — no API key is used and no real order can be sent.")
        print(f"polling every {args.interval or cfg.paper.poll_seconds}s; Ctrl+C to stop")
        trader.run_forever(interval_seconds=args.interval, max_steps=args.max_steps)
        return 0

    print(format_status(trader.summary()))
    print("\nUse --once for one decision, or --loop for continuous paper trading.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    trader = PaperTrader(cfg, strategy=args.strategy or cfg.strategy_name)
    print(format_status(trader.summary()))
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    import uvicorn
    import webbrowser

    from .web import create_app, existing_console_url, load_env_file

    cfg = load_config(args.config)
    loaded = load_env_file(cfg.root / ".env")
    if loaded:
        print(f"loaded {loaded} variable(s) from .env")
    host = args.host or cfg.web.host
    port = args.port or cfg.web.port
    url = f"http://{'127.0.0.1' if host in {'0.0.0.0', '::'} else host}:{port}"
    existing = existing_console_url(host, port)
    if existing:
        print(f"已有控制台在运行：{existing}")
        print("没有重复启动。要重启，请先运行 停止.bat 或关闭原进程。")
        if args.open:
            webbrowser.open(existing)
        return 0
    mode = "PAPER fallback" if args.paper else "auto (Testnet if keys are configured)"
    print("=" * 64)
    print("BTC QUANT — VISUAL TESTNET CONSOLE")
    print(f"mode      : {mode}")
    print(f"bind      : http://{host}:{port}")
    print("safety    : Spot Testnet only; no mainnet order endpoint is used.")
    print("=" * 64)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print("WARNING: binding beyond localhost exposes an unauthenticated order UI.")
    app = create_app(cfg, force_paper=args.paper, disable_live=args.paper)
    if args.open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="btcquant",
        description="BTC spot long-only backtest and local paper-trading lab",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("download", help="download or refresh Binance klines")
    _config_arg(p)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("backtest", help="run one strategy backtest and write an HTML report")
    _config_arg(p)
    p.add_argument("--strategy", choices=["trend", "donchian", "meanrev"], default=None)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--force-download", action="store_true")
    p.add_argument("--sizing", choices=["fixed_fraction", "risk"], default=None)
    p.add_argument("--risk", type=float, default=None, help="risk per trade, e.g. 0.02")
    p.add_argument("--no-report", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("compare", help="compare trend / donchian / meanrev")
    _config_arg(p)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument("--force-download", action="store_true")
    p.add_argument("--sizing", choices=["fixed_fraction", "risk"], default="risk")
    p.add_argument("--risk", type=float, default=0.02)
    p.set_defaults(func=cmd_compare)

    for name, func, help_text in [
        ("paper", cmd_paper, "run the local paper-trading loop"),
        ("status", cmd_status, "show paper-trading state"),
    ]:
        p = sub.add_parser(name, help=help_text)
        _config_arg(p)
        p.add_argument("--strategy", choices=["trend", "donchian", "meanrev"], default=None)
        p.set_defaults(func=func)
        if name == "paper":
            p.add_argument("--once", action="store_true", help="one decision, then exit")
            p.add_argument("--loop", action="store_true", help="poll continuously")
            p.add_argument("--status", action="store_true", help="print state only")
            p.add_argument("--interval", type=int, default=None, help="poll interval seconds")
            p.add_argument("--max-steps", type=int, default=None, help="stop after N steps (debug)")
            p.add_argument("--reset", action="store_true", help="archive and reset the paper account")
            p.add_argument("--yes", action="store_true", help="skip reset confirmation")

    p = sub.add_parser("web", help="open the visual Testnet trading console")
    _config_arg(p)
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--paper", action="store_true", help="force local paper mode instead of Testnet")
    p.add_argument("--open", action=argparse.BooleanOptionalAction, default=True, help="open browser")
    p.set_defaults(func=cmd_web)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows consoles often default to GBK; the reports and CLI contain
    # Chinese text and Unicode punctuation, so request UTF-8 explicitly.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
