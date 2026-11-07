"""Backtest charts and a self-contained HTML report."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import html
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .engine import BacktestResult, run_backtest  # noqa: E402


def _setup_style() -> None:
    try:
        plt.style.use("seaborn-v0_8-whitegrid")
    except Exception:
        pass
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#d0d5dd",
            "axes.labelcolor": "#344054",
            "xtick.color": "#475467",
            "ytick.color": "#475467",
            "font.size": 10,
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "legend.frameon": False,
        }
    )


def yearly_returns(equity: pd.DataFrame, initial_cash: float) -> pd.Series:
    if equity.empty:
        return pd.Series(dtype=float)
    s = equity.set_index("open_time")["equity"].astype(float)
    ends = s.resample("YE").last()
    prev = ends.shift(1)
    if len(prev):
        prev.iloc[0] = initial_cash
    return ends / prev - 1.0


def plot_dashboard(result: BacktestResult, path: Path) -> None:
    _setup_style()
    eq = result.equity.set_index("open_time")["equity"].astype(float)
    bench = result.benchmark_equity.set_index("open_time")["equity"].astype(float)
    dd = result.equity.set_index("open_time")["drawdown"].astype(float) * 100.0
    bdd = result.benchmark_equity.set_index("open_time")["drawdown"].astype(float) * 100.0
    yr = yearly_returns(result.equity, result.config.initial_cash)
    byr = yearly_returns(result.benchmark_equity.rename(columns={"equity": "equity"}), result.config.initial_cash)

    fig = plt.figure(figsize=(13, 10.5))
    gs = fig.add_gridspec(3, 1, height_ratios=[3.0, 1.15, 1.35], hspace=0.32)
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax3 = fig.add_subplot(gs[2])

    ax1.plot(eq.index, eq.values, color="#0b6e4f", linewidth=1.7, label="Strategy")
    ax1.plot(bench.index, bench.values, color="#98a2b3", linewidth=1.25, linestyle="--", label="Buy & hold")
    ax1.set_yscale("log")
    ax1.set_ylabel("Equity (USDT, log)")
    ax1.set_title(f"{result.strategy.upper()} — BTC spot long-only paper research")
    ax1.legend(loc="upper left")
    test_start = pd.Timestamp(result.config.test_start)
    if test_start.tzinfo is None:
        test_start = test_start.tz_localize("UTC")
    if eq.index.min() < test_start < eq.index.max():
        ax1.axvline(test_start, color="#b45309", linewidth=1.0, linestyle=":")
        ax1.text(
            test_start,
            ax1.get_ylim()[1],
            " out-of-sample",
            color="#b45309",
            fontsize=8,
            va="top",
            ha="left",
        )
    m = result.metrics
    bm = result.benchmark_metrics
    text = (
        f"Strategy: CAGR {m.get('cagr', 0):.1%} | Sharpe {m.get('sharpe', 0):.2f} | MaxDD {m.get('max_drawdown', 0):.1%}\n"
        f"Buy & hold: CAGR {bm.get('cagr', 0):.1%} | Sharpe {bm.get('sharpe', 0):.2f} | MaxDD {bm.get('max_drawdown', 0):.1%}"
    )
    ax1.text(
        0.99,
        0.03,
        text,
        transform=ax1.transAxes,
        ha="right",
        va="bottom",
        fontsize=9,
        color="#344054",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="white", edgecolor="#e4e7ec", alpha=0.9),
    )

    ax2.fill_between(dd.index, dd.values, 0, color="#c0392b", alpha=0.22, label="Strategy drawdown")
    ax2.plot(bdd.index, bdd.values, color="#98a2b3", linewidth=0.9, linestyle="--", label="Buy & hold drawdown")
    ax2.set_ylabel("Drawdown (%)")
    ax2.legend(loc="lower left", ncol=2)
    ax2.set_ylim(min(float(dd.min()), float(bdd.min())) * 1.05, 2.0)

    years = [str(x.year) for x in yr.index]
    x = range(len(years))
    ax3.bar([i - 0.19 for i in x], yr.values * 100, width=0.38, color="#0b6e4f", label="Strategy")
    ax3.bar([i + 0.19 for i in x], byr.reindex(yr.index).values * 100, width=0.38, color="#c7cdd6", label="Buy & hold")
    ax3.axhline(0, color="#475467", linewidth=0.8)
    ax3.set_xticks(list(x))
    ax3.set_xticklabels(years, rotation=0)
    ax3.set_ylabel("Calendar-year return (%)")
    ax3.set_title("Calendar-year returns (partial first/last year included)")
    ax3.legend(loc="upper left", ncol=2)
    for i, (a, b) in enumerate(zip(yr.values, byr.reindex(yr.index).values)):
        ax3.text(i - 0.19, a * 100, f"{a:.0%}", ha="center", va="bottom" if a >= 0 else "top", fontsize=7)
        ax3.text(i + 0.19, b * 100, f"{b:.0%}", ha="center", va="bottom" if b >= 0 else "top", fontsize=7, color="#667085")

    fig.text(0.01, 0.005, "Research only. Fees/slippage are modelled; real fills and taxes are not.", fontsize=8, color="#667085")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _pct(value: Any, digits: int = 1) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return str(value)


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _num(value: Any, digits: int = 2) -> str:
    try:
        value = float(value)
        if value == float("inf"):
            return "∞"
        return f"{value:.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _row(label: str, m: dict[str, Any], bm: dict[str, Any], kind: str) -> str:
    if kind == "pct":
        a, b = _pct(m.get(label)), _pct(bm.get(label))
    elif kind == "money":
        a, b = _money(m.get(label)), _money(bm.get(label))
    elif kind == "int":
        a = int(m.get(label, 0))
        b = int(bm.get(label, 0)) if bm.get(label) else "—"
    else:
        a, b = _num(m.get(label)), _num(bm.get(label))
    # The buy-and-hold benchmark has no "trades"; don't print misleading zeros.
    if label in {"win_rate", "profit_factor", "avg_bars_held"} and not bm.get("trades"):
        b = "—"
    return f"<tr><td>{html.escape(label.replace('_', ' '))}</td><td>{a}</td><td>{b}</td></tr>"


def _year_rows(result: BacktestResult) -> str:
    yr = yearly_returns(result.equity, result.config.initial_cash)
    byr = yearly_returns(result.benchmark_equity, result.config.initial_cash).reindex(yr.index)
    rows = []
    for ts, value in yr.items():
        b = byr.loc[ts] if ts in byr.index else float("nan")
        diff = value - b
        cls = "pos" if value >= 0 else "neg"
        dcls = "pos" if diff >= 0 else "neg"
        rows.append(
            f"<tr><td>{ts.year}</td><td class='{cls}'>{_pct(value)}</td>"
            f"<td>{_pct(b)}</td><td class='{dcls}'>{_pct(diff)}</td></tr>"
        )
    return "".join(rows)


def period_result(result: BacktestResult, start: str, end: str | None = None) -> BacktestResult | None:
    """Re-run the same parameters on a sub-period, keeping indicator warm-up."""
    s = result.signals
    mask = s["open_time"] >= pd.Timestamp(start, tz="UTC")
    if end:
        mask &= s["open_time"] < pd.Timestamp(end, tz="UTC")
    subset = s[mask].reset_index(drop=True)
    if len(subset) < 30:
        return None
    return run_backtest(subset, subset, result.config, strategy=result.strategy, params=result.params)


def _period_rows(rows: list[tuple[str, BacktestResult | None]]) -> str:
    out = []
    for label, r in rows:
        if r is None:
            out.append(f"<tr><td>{html.escape(label)}</td><td colspan='6'>样本不足</td></tr>")
            continue
        m = r.metrics
        out.append(
            f"<tr><td>{html.escape(label)}</td><td>{_pct(m.get('total_return'))}</td>"
            f"<td>{_pct(m.get('cagr'))}</td><td>{_pct(m.get('max_drawdown'))}</td>"
            f"<td>{_num(m.get('sharpe'))}</td><td>{int(m.get('trades', 0))}</td>"
            f"<td>{_pct(m.get('win_rate'))}</td></tr>"
        )
    return "".join(out)


def _trade_rows(result: BacktestResult, limit: int = 12) -> str:
    if result.trades.empty:
        return "<tr><td colspan='8'>没有交易</td></tr>"
    rows = []
    for _, t in result.trades.tail(limit).iterrows():
        rows.append(
            "<tr>"
            f"<td>{pd.Timestamp(t['entry_time']).strftime('%Y-%m-%d %H:%M')}</td>"
            f"<td>{pd.Timestamp(t['exit_time']).strftime('%Y-%m-%d %H:%M')}</td>"
            f"<td>{float(t['entry_price']):,.2f}</td>"
            f"<td>{float(t['exit_price']):,.2f}</td>"
            f"<td class='{'pos' if float(t['net_pnl']) >= 0 else 'neg'}'>{float(t['net_pnl']):,.2f}</td>"
            f"<td>{_pct(t['return_pct'], 2)}</td>"
            f"<td>{int(t['bars_held'])}</td>"
            f"<td>{html.escape(str(t['exit_reason']))}</td>"
            "</tr>"
        )
    return "".join(rows)


def write_report(
    result: BacktestResult,
    out_dir: Path,
    *,
    data_desc: dict[str, Any],
    title: str = "BTC 现货多头量化回测",
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"backtest_{result.strategy}"
    png_path = out_dir / f"{stem}.png"
    csv_path = out_dir / f"{stem}_trades.csv"
    json_path = out_dir / f"{stem}_metrics.json"
    html_path = out_dir / f"{stem}.html"

    plot_dashboard(result, png_path)
    result.trades.to_csv(csv_path, index=False, encoding="utf-8-sig")
    payload = {
        "strategy": result.strategy,
        "params": result.params,
        "config": result.config.__dict__,
        "metrics": result.metrics,
        "benchmark_metrics": result.benchmark_metrics,
        "data": data_desc,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")
    m, bm = result.metrics, result.benchmark_metrics
    cards = [
        ("策略总收益", _pct(m.get("total_return")), "含手续费与滑点"),
        ("年化收益 CAGR", _pct(m.get("cagr")), f"基准 {_pct(bm.get('cagr'))}"),
        ("最大回撤", _pct(m.get("max_drawdown")), f"基准 {_pct(bm.get('max_drawdown'))}"),
        ("Sharpe", _num(m.get("sharpe")), f"基准 {_num(bm.get('sharpe'))}"),
        ("胜率", _pct(m.get("win_rate")), f"{int(m.get('trades', 0))} 笔交易"),
        ("盈亏比", _num(m.get("profit_factor")), "毛利 / 毛损"),
        ("市场暴露", _pct(m.get("exposure")), "持仓时间占比"),
        ("最终权益", _money(m.get("final_equity")), "USDT"),
    ]
    card_html = "".join(
        f"<div class='card'><div class='k'>{html.escape(k)}</div><div class='v'>{v}</div><div class='s'>{html.escape(s)}</div></div>"
        for k, v, s in cards
    )
    metric_rows = "".join(
        [
            _row("total_return", m, bm, "pct"),
            _row("cagr", m, bm, "pct"),
            _row("volatility", m, bm, "pct"),
            _row("sharpe", m, bm, "num"),
            _row("sortino", m, bm, "num"),
            _row("max_drawdown", m, bm, "pct"),
            _row("calmar", m, bm, "num"),
            _row("exposure", m, bm, "pct"),
            _row("trades", m, bm, "int"),
            _row("win_rate", m, bm, "pct"),
            _row("profit_factor", m, bm, "num"),
            _row("avg_bars_held", m, bm, "num"),
        ]
    )
    full_start = str(result.metrics.get("start", ""))[:10] or "2017-01-01"
    train_res = period_result(result, full_start, result.config.test_start)
    oos_res = period_result(result, result.config.test_start)
    period_rows = _period_rows(
        [
            ("全样本", result),
            (f"样本内（至 {result.config.test_start}）", train_res),
            (f"样本外（{result.config.test_start} 起）", oos_res),
        ]
    )
    params_text = html.escape(json.dumps(result.params, ensure_ascii=False, sort_keys=True))
    p = result.params
    trail = float(p.get("trail_atr", 0) or 0)
    trail_text = "关闭（固定止损）" if trail == 0 else f"{trail:g} × ATR14"
    if result.strategy == "trend":
        logic = (
            f"入场：EMA{p.get('fast')} &gt; EMA{p.get('slow')}、收盘价 &gt; EMA{p.get('slow')}、"
            f"收盘价 &gt; EMA{p.get('regime')}、ADX14 ≥ {p.get('adx_min')}。<br>"
            f"出场：EMA{p.get('fast')} &lt; EMA{p.get('slow')} 或收盘价 &lt; EMA{p.get('regime')}。<br>"
            f"止损：入场价 − {p.get('atr_stop')} × ATR14；移动止损：{trail_text}。"
        )
    elif result.strategy == "donchian":
        logic = (
            f"入场：收盘价突破前 {p.get('entry')} 根最高价，且收盘价 &gt; EMA{p.get('regime')}。<br>"
            f"出场：收盘价跌破前 {p.get('exit')} 根最低价。<br>"
            f"止损：入场价 − {p.get('atr_stop')} × ATR14。"
        )
    else:
        logic = (
            f"入场：收盘价 &lt; 布林下轨({p.get('bb')}, {p.get('bb_std')})、RSI{p.get('rsi_n')} &lt; "
            f"{p.get('rsi_entry')}、收盘价 &gt; EMA{p.get('regime')}。<br>"
            f"出场：收盘价 &gt; 布林中轨 或 RSI{p.get('rsi_n')} &gt; {p.get('rsi_exit')}；"
            f"最长持有 {p.get('max_hold')} 根。<br>"
            f"止损：入场价 − {p.get('atr_stop')} × ATR14。"
        )
    data_text = html.escape(
        f"{data_desc.get('first', '?')} → {data_desc.get('last', '?')} | {data_desc.get('rows', 0):,} 根 K线"
        if data_desc.get("rows")
        else "无数据"
    )

    doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · {result.strategy}</title>
<style>
:root {{ color-scheme: light; --ink:#101828; --muted:#667085; --line:#e4e7ec; --green:#0b6e4f; --bg:#f8fafc; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; color:var(--ink); background:var(--bg); line-height:1.55; }}
.wrap {{ max-width: 1180px; margin: 0 auto; padding: 36px 22px 60px; }}
h1 {{ font-size: 30px; margin: 0 0 6px; letter-spacing:-.02em; }}
h2 {{ font-size: 20px; margin: 38px 0 12px; }}
.sub {{ color:var(--muted); margin:0 0 20px; }}
.banner {{ background:#fff7ed; border:1px solid #fed7aa; color:#9a3412; padding:12px 15px; border-radius:10px; font-size:13px; }}
.banner b {{ color:#7c2d12; }}
.cards {{ display:grid; grid-template-columns: repeat(4, 1fr); gap:12px; margin:22px 0 8px; }}
.card {{ background:white; border:1px solid var(--line); border-radius:12px; padding:14px 15px; }}
.card .k {{ color:var(--muted); font-size:12px; }}
.card .v {{ font-size:24px; font-weight:700; margin:3px 0; letter-spacing:-.02em; }}
.card .s {{ color:var(--muted); font-size:11px; }}
img {{ max-width:100%; border:1px solid var(--line); border-radius:12px; background:white; }}
table {{ width:100%; border-collapse:collapse; background:white; border:1px solid var(--line); border-radius:12px; overflow:hidden; font-size:13px; }}
th, td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); }}
th {{ background:#f2f4f7; color:#475467; font-weight:600; }}
tr:last-child td {{ border-bottom:none; }}
td:nth-child(2), td:nth-child(3), th:nth-child(2), th:nth-child(3) {{ text-align:right; font-variant-numeric: tabular-nums; }}
.pos {{ color:var(--green); font-weight:600; }}
.neg {{ color:#b42318; font-weight:600; }}
.note {{ background:white; border:1px solid var(--line); border-radius:12px; padding:16px 20px; }}
.note ul {{ margin:6px 0 0; padding-left:20px; }}
.note li {{ margin:5px 0; }}
code {{ background:#f2f4f7; padding:2px 6px; border-radius:5px; font-size:12px; }}
.footer {{ color:var(--muted); font-size:12px; margin-top:34px; }}
@media (max-width: 850px) {{ .cards {{ grid-template-columns: repeat(2, 1fr); }} }}
</style>
</head>
<body><div class="wrap">
<h1>{html.escape(title)}</h1>
<p class="sub">策略 <b>{result.strategy}</b> · 标的 BTCUSDT 现货 · 只做多 · 4h · 数据：{data_text}</p>
<div class="banner"><b>研究用途，不是投资建议。</b> 这是本地回测/纸交易结果，不包含真实成交、资金费率、税务、交易所宕机与提现风险。历史收益不代表未来。</div>
<div class="cards">{card_html}</div>

<h2>权益曲线与回撤</h2>
<img src="data:image/png;base64,{b64}" alt="equity curve">

<h2>策略 vs 买入持有</h2>
<table>
<thead><tr><th>指标</th><th>策略</th><th>买入持有</th></tr></thead>
<tbody>{metric_rows}</tbody>
</table>

<h2>样本内 / 样本外</h2>
<table>
<thead><tr><th>区间</th><th>总收益</th><th>CAGR</th><th>最大回撤</th><th>Sharpe</th><th>交易数</th><th>胜率</th></tr></thead>
<tbody>{period_rows}</tbody>
</table>
<p class="sub">样本外区间从 {result.config.test_start} 开始。指标是在同一组参数下重新起算的，不是从全样本曲线上截一段。</p>

<h2>年度收益</h2>
<table>
<thead><tr><th>年份</th><th>策略</th><th>买入持有</th><th>超额</th></tr></thead>
<tbody>{_year_rows(result)}</tbody>
</table>

<h2>最近交易</h2>
<table>
<thead><tr><th>入场</th><th>出场</th><th>入场价</th><th>出场价</th><th>净盈亏</th><th>收益</th><th>持仓K线</th><th>出场原因</th></tr></thead>
<tbody>{_trade_rows(result)}</tbody>
</table>
<p class="sub">完整交易记录：<code>{html.escape(csv_path.name)}</code></p>

<h2>策略逻辑</h2>
<div class="note"><p>{logic}</p></div>

<h2>策略参数与假设</h2>
<div class="note">
<p><b>参数：</b><code>{params_text}</code></p>
<ul>
<li>信号在 K 线收盘后计算，下一根 K 线开盘成交；止损在当根 K 线最低价触发。</li>
<li>手续费 {result.config.fee_rate:.2%}/边，滑点 {result.config.slippage_bps:.1f} bps/边；最大仓位 {result.config.max_fraction:.0%}。</li>
<li>仓位模式：<code>{html.escape(result.config.sizing)}</code>；风险预算 {result.config.risk_per_trade:.1%}/笔（仅 risk 模式）。</li>
<li>只做多现货，不加杠杆，不做空，不涉及合约资金费率。</li>
<li>数据来自 Binance 公共行情；已剔除未收盘 K 线。</li>
</ul>
</div>

<h2>怎么读结果</h2>
<div class="note">
<ul>
<li><b>先看回撤，再看收益。</b> 趋势策略通常胜率低（30% 左右），靠少数大行情覆盖多次小止损；不要因为连续亏损就改参数。</li>
<li><b>Sharpe 比总收益更可迁移。</b> 总收益受起点影响很大；Sharpe、最大回撤、暴露时间更能反映策略质量。</li>
<li><b>样本外才是考试。</b> 报告同时给出 2023 年后的样本外区间；如果样本外明显失效，不要上实盘。</li>
<li><b>不要用测试集调参。</b> 任何“再改一个参数就更好”的动作，都会让样本外结果失真。</li>
</ul>
</div>

<h2>下一步</h2>
<div class="note">
<ul>
<li>运行 <code>python -m btcquant paper --once</code> 初始化纸交易，再运行 <code>python -m btcquant paper --loop</code> 持续跟踪。</li>
<li>至少纸交易 4–8 周，记录真实滑点、信号延迟、停机时间，与回测假设对比。</li>
<li>若纸交易与回测偏差可接受，再用小额现货、独立 API Key（只开交易权限、关闭提现、绑定 IP）试运行。</li>
</ul>
</div>

<p class="footer">生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · btc-quant-lab · 数据：Binance public API</p>
</div></body></html>"""
    html_path.write_text(doc, encoding="utf-8")
    return {"html": html_path, "png": png_path, "trades": csv_path, "metrics": json_path}


def write_compare_report(results: list[BacktestResult], out_dir: Path, *, data_desc: dict[str, Any]) -> Path:
    """Simple comparison page for the three research strategies."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _setup_style()
    fig, ax = plt.subplots(figsize=(13, 6.8))
    colors = ["#0b6e4f", "#1d4ed8", "#b45309", "#98a2b3"]
    for i, r in enumerate(results):
        eq = r.equity.set_index("open_time")["equity"]
        ax.plot(eq.index, eq.values, linewidth=1.6, label=f"{r.strategy} (Sharpe {r.metrics.get('sharpe', 0):.2f})", color=colors[i % len(colors)])
    if results:
        b = results[0].benchmark_equity.set_index("open_time")["equity"]
        ax.plot(b.index, b.values, linewidth=1.2, linestyle="--", color="#98a2b3", label="buy & hold")
    ax.set_yscale("log")
    ax.set_ylabel("Equity (USDT, log)")
    ax.set_title("BTC spot long-only — strategy comparison")
    ax.legend(loc="upper left")
    png = out_dir / "compare.png"
    fig.savefig(png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    rows = []
    for r in results:
        m = r.metrics
        rows.append(
            f"<tr><td>{html.escape(r.strategy)}</td><td>{_pct(m.get('total_return'))}</td>"
            f"<td>{_pct(m.get('cagr'))}</td><td>{_pct(m.get('max_drawdown'))}</td>"
            f"<td>{_num(m.get('sharpe'))}</td><td>{_pct(m.get('win_rate'))}</td>"
            f"<td>{_num(m.get('profit_factor'))}</td><td>{int(m.get('trades', 0))}</td></tr>"
        )
    b64 = base64.b64encode(png.read_bytes()).decode("ascii")
    data_text = html.escape(f"{data_desc.get('first', '?')} → {data_desc.get('last', '?')} | {data_desc.get('rows', 0):,} bars")
    doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>策略对比</title>
<style>body{{font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#f8fafc;color:#101828;margin:0}}
.wrap{{max-width:1100px;margin:0 auto;padding:34px 20px}}h1{{margin:0 0 6px}}p{{color:#667085}}
table{{width:100%;border-collapse:collapse;background:white;border:1px solid #e4e7ec;border-radius:12px;overflow:hidden;font-size:13px}}
th,td{{padding:10px 12px;border-bottom:1px solid #e4e7ec;text-align:right}}th:first-child,td:first-child{{text-align:left}}
th{{background:#f2f4f7}}img{{max-width:100%;border:1px solid #e4e7ec;border-radius:12px;background:white}}
code{{background:#f2f4f7;padding:2px 6px;border-radius:5px}}</style></head><body><div class="wrap">
<h1>三种策略对比</h1><p>BTCUSDT 现货 · 只做多 · 4h · {data_text}</p>
<img src="data:image/png;base64,{b64}" alt="comparison">
<h2>核心指标</h2><table><thead><tr><th>策略</th><th>总收益</th><th>CAGR</th><th>最大回撤</th><th>Sharpe</th><th>胜率</th><th>盈亏比</th><th>交易数</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<p>均值回归与突破的样本外表现通常更依赖市场状态；不要把单一样本期的第一名直接当成实盘策略。</p>
</div></body></html>"""
    path = out_dir / "compare.html"
    path.write_text(doc, encoding="utf-8")
    return path
