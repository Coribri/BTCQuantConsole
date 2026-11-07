"""Entry point for the packaged Windows console.

This intentionally imports only the web/console stack (not matplotlib or the
backtest report modules), keeping the distributable smaller and simpler.
"""

from __future__ import annotations

import argparse
import multiprocessing
import sys
import threading
import webbrowser

import uvicorn

from btcquant.config import load_config
from btcquant.web import create_app, existing_console_url, load_env_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="BTCQuantConsole",
        description="BTC spot Testnet visual trading console",
    )
    parser.add_argument("command", nargs="?", default="web", help="only 'web' is supported")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--paper", action="store_true", help="force local paper mode")
    parser.add_argument("--no-open", action="store_true", help="do not open the browser")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "web":
        print("Only the 'web' command is included in this build.")
        return 2

    try:
        cfg = load_config(args.config)
    except FileNotFoundError:
        print("找不到 config.toml。请把 exe、config.toml 和 _internal 放在同一个文件夹。")
        return 1
    loaded = load_env_file(cfg.root / ".env")
    if loaded:
        print(f"loaded {loaded} variable(s) from .env")

    host = args.host or cfg.web.host
    port = args.port or cfg.web.port
    url = f"http://{'127.0.0.1' if host in {'0.0.0.0', '::'} else host}:{port}"
    existing = existing_console_url(host, port)
    if existing:
        print(f"已有控制台在运行：{existing}")
        print("没有重复启动。要重启，请先运行 停止.bat。")
        if not args.no_open:
            webbrowser.open(existing)
        return 0
    print("=" * 66)
    print("BTC QUANT — VISUAL TESTNET CONSOLE")
    print(f"URL       : {url}")
    print("MODE      : Testnet if .env keys exist, otherwise local PAPER")
    print("WORKSPACE : Simulation is separate from the live exchange workspace.")
    print("=" * 66)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print("WARNING: this console has no login. Do not expose it to the internet.")
    if not args.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    app = create_app(cfg, force_paper=args.paper, disable_live=args.paper)
    try:
        uvicorn.run(app, host=host, port=port, log_level="info")
    except OSError as exc:
        print(f"\nFailed to open {host}:{port}: {exc}")
        print("Close the other program using this port, or edit config.toml [web] port.")
        return 1
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
