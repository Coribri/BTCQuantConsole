"""Configuration loading.

The project uses TOML (stdlib ``tomllib``) so there is no YAML dependency.
All relative paths are resolved against the directory that contains the
config file, not against the current shell directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys
import tomllib
from typing import Any


DEFAULT_PARAMS: dict[str, dict[str, Any]] = {
    "trend_v2": {
        "fast": 20, "slow": 60, "regime": 200, "adx_min": 22.0,
        "atr_stop": 2.5, "trail_atr": 2.0, "max_hold": 0,
        "max_extension_atr": 2.0, "min_volume_ratio": 0.65,
        "max_atr_pct": 0.06, "roundtrip_cost": 0.0024,
    },
    "trend": {
        "fast": 20,
        "slow": 100,
        "regime": 200,
        "adx_min": 20.0,
        "atr_stop": 3.0,
        "trail_atr": 0.0,
        "max_hold": 0,
    },
    "donchian": {
        "entry": 55,
        "exit": 20,
        "regime": 200,
        "atr_stop": 3.0,
        "trail_atr": 4.0,
        "max_hold": 0,
    },
    "meanrev": {
        "bb": 20,
        "bb_std": 2.0,
        "rsi_n": 2,
        "rsi_entry": 10.0,
        "rsi_exit": 60.0,
        "regime": 200,
        "atr_stop": 2.5,
        "trail_atr": 0.0,
        "max_hold": 20,
    },
}


@dataclass(frozen=True)
class MarketConfig:
    symbol: str = "BTCUSDT"
    interval: str = "4h"
    start: str = "2017-08-17"
    quote: str = "USDT"


@dataclass(frozen=True)
class DataConfig:
    dir: Path = Path("data")
    base_urls: tuple[str, ...] = (
        "https://data-api.binance.vision",
        "https://api.binance.com",
        "https://api1.binance.com",
        "https://api2.binance.com",
    )
    timeout_seconds: float = 20.0


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 10_000.0
    fee_rate: float = 0.001
    slippage_bps: float = 2.0
    max_fraction: float = 0.95
    sizing: str = "risk"  # fixed_fraction | risk
    risk_per_trade: float = 0.02
    bars_per_year: int = 2190
    train_end: str = "2022-12-31"
    test_start: str = "2023-01-01"


@dataclass(frozen=True)
class PaperConfig:
    initial_cash: float = 10_000.0
    fee_rate: float = 0.001
    slippage_bps: float = 2.0
    max_fraction: float = 0.95
    sizing: str = "risk"
    risk_per_trade: float = 0.02
    poll_seconds: int = 60
    state_file: Path = Path("runtime/paper_state.json")
    trades_file: Path = Path("runtime/paper_trades.csv")
    equity_file: Path = Path("runtime/paper_equity.csv")
    log_file: Path = Path("runtime/paper_events.jsonl")


@dataclass(frozen=True)
class TestnetConfig:
    base_url: str = "https://testnet.binance.vision"
    api_key_env: str = "BINANCE_TESTNET_API_KEY"
    api_secret_env: str = "BINANCE_TESTNET_API_SECRET"
    recv_window: int = 5000
    poll_seconds: int = 5
    auto_start: bool = False
    quote_amount: float = 100.0
    state_file: Path = Path("runtime/testnet_state.json")
    paper_state_file: Path = Path("runtime/dashboard_paper_state.json")


@dataclass(frozen=True)
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    open_browser: bool = True


@dataclass(frozen=True)
class AppConfig:
    root: Path
    market: MarketConfig
    data: DataConfig
    backtest: BacktestConfig
    paper: PaperConfig
    strategy_name: str
    strategy_params: dict[str, Any] = field(default_factory=dict)
    report_dir: Path = Path("reports")
    testnet: TestnetConfig = TestnetConfig()
    web: WebConfig = WebConfig()

    def strategy_params_for(self, name: str | None = None) -> dict[str, Any]:
        name = name or self.strategy_name
        params = dict(DEFAULT_PARAMS.get(name, {}))
        if name == self.strategy_name:
            params.update(self.strategy_params)
        return params


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"config section [{key}] must be a table")
    return value


def _path(root: Path, value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (root / p)


def _resolve_config_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if str(path) == "config.toml" and getattr(sys, "frozen", False):
        # In a packaged build, always prefer the config next to the exe.
        # Otherwise launching from another folder could silently use a
        # different config or a developer .env.
        exe_config = Path(sys.executable).resolve().parent / "config.toml"
        if exe_config.exists():
            return exe_config
    if candidate.exists():
        return candidate.resolve()
    if str(path) == "config.toml":
        candidates = [Path(__file__).resolve().parent.parent / "config.toml"]
        for item in candidates:
            if item.exists():
                return item.resolve()
    raise FileNotFoundError(f"config not found: {candidate.resolve()}")


def load_config(path: str | Path = "config.toml") -> AppConfig:
    cfg_path = _resolve_config_path(path)
    root = cfg_path.parent
    with cfg_path.open("rb") as fh:
        raw = tomllib.load(fh)

    market_raw = _section(raw, "market")
    data_raw = _section(raw, "data")
    bt_raw = _section(raw, "backtest")
    paper_raw = _section(raw, "paper")
    testnet_raw = _section(raw, "testnet")
    web_raw = _section(raw, "web")
    strategy_raw = _section(raw, "strategy")
    strategy_params = strategy_raw.get("params", {})
    if not isinstance(strategy_params, dict):
        raise ValueError("[strategy.params] must be a table")

    market = MarketConfig(
        symbol=str(market_raw.get("symbol", "BTCUSDT")),
        interval=str(market_raw.get("interval", "4h")),
        start=str(market_raw.get("start", "2017-08-17")),
        quote=str(market_raw.get("quote", "USDT")),
    )
    data = DataConfig(
        dir=_path(root, data_raw.get("dir", "data")),
        base_urls=tuple(data_raw.get("base_urls", DataConfig.base_urls)),
        timeout_seconds=float(data_raw.get("timeout_seconds", 20.0)),
    )
    backtest = BacktestConfig(
        initial_cash=float(bt_raw.get("initial_cash", 10_000.0)),
        fee_rate=float(bt_raw.get("fee_rate", 0.001)),
        slippage_bps=float(bt_raw.get("slippage_bps", 2.0)),
        max_fraction=float(bt_raw.get("max_fraction", 0.95)),
        sizing=str(bt_raw.get("sizing", "fixed_fraction")),
        risk_per_trade=float(bt_raw.get("risk_per_trade", 0.02)),
        bars_per_year=int(bt_raw.get("bars_per_year", 2190)),
        train_end=str(bt_raw.get("train_end", "2022-12-31")),
        test_start=str(bt_raw.get("test_start", "2023-01-01")),
    )
    paper = PaperConfig(
        initial_cash=float(paper_raw.get("initial_cash", 10_000.0)),
        fee_rate=float(paper_raw.get("fee_rate", 0.001)),
        slippage_bps=float(paper_raw.get("slippage_bps", 2.0)),
        max_fraction=float(paper_raw.get("max_fraction", 0.95)),
        sizing=str(paper_raw.get("sizing", "fixed_fraction")),
        risk_per_trade=float(paper_raw.get("risk_per_trade", 0.02)),
        poll_seconds=int(paper_raw.get("poll_seconds", 60)),
        state_file=_path(root, paper_raw.get("state_file", "runtime/paper_state.json")),
        trades_file=_path(root, paper_raw.get("trades_file", "runtime/paper_trades.csv")),
        equity_file=_path(root, paper_raw.get("equity_file", "runtime/paper_equity.csv")),
        log_file=_path(root, paper_raw.get("log_file", "runtime/paper_events.jsonl")),
    )
    report_dir = _path(root, raw.get("report_dir", "reports"))
    testnet = TestnetConfig(
        base_url=str(testnet_raw.get("base_url", "https://testnet.binance.vision")),
        api_key_env=str(testnet_raw.get("api_key_env", "BINANCE_TESTNET_API_KEY")),
        api_secret_env=str(testnet_raw.get("api_secret_env", "BINANCE_TESTNET_API_SECRET")),
        recv_window=int(testnet_raw.get("recv_window", 5000)),
        poll_seconds=int(testnet_raw.get("poll_seconds", 5)),
        auto_start=bool(testnet_raw.get("auto_start", False)),
        quote_amount=float(testnet_raw.get("quote_amount", 100.0)),
        state_file=_path(root, testnet_raw.get("state_file", "runtime/testnet_state.json")),
        paper_state_file=_path(
            root, testnet_raw.get("paper_state_file", "runtime/dashboard_paper_state.json")
        ),
    )
    web = WebConfig(
        host=str(web_raw.get("host", "127.0.0.1")),
        port=int(web_raw.get("port", 8787)),
        open_browser=bool(web_raw.get("open_browser", True)),
    )

    return AppConfig(
        root=root,
        market=market,
        data=data,
        backtest=backtest,
        paper=paper,
        strategy_name=str(strategy_raw.get("name", "trend")),
        strategy_params=strategy_params,
        report_dir=report_dir,
        testnet=testnet,
        web=web,
    )
