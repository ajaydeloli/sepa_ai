"""
backtest/report.py
------------------
Report generator for SEPA AI backtest results.

Produces a self-contained HTML report and a flat CSV of all trades.

Public API
----------
generate_report(result, metrics, output_dir) -> (html_path, csv_path)
plot_equity_curve(equity_curve)              -> base64 PNG string
"""

from __future__ import annotations

import base64
import csv
import dataclasses
import io
import os
from datetime import date, datetime
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from backtest.engine import BacktestResult, BacktestTrade
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Chart style (matches paper_trading/report.py palette)
# ---------------------------------------------------------------------------

_CHART_STYLE: dict[str, Any] = {
    "figure.facecolor": "#1a1a2e",
    "axes.facecolor":   "#16213e",
    "axes.edgecolor":   "#0f3460",
    "axes.labelcolor":  "#e0e0e0",
    "xtick.color":      "#a0a0a0",
    "ytick.color":      "#a0a0a0",
    "text.color":       "#e0e0e0",
    "grid.color":       "#0f3460",
    "grid.linestyle":   "--",
    "grid.alpha":       0.6,
}

# ---------------------------------------------------------------------------
# CSS — dark theme, self-contained
# ---------------------------------------------------------------------------

_CSS = """
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', Arial, sans-serif; background: #0d1117;
       color: #c9d1d9; padding: 24px; }
h1  { color: #58a6ff; margin-bottom: 20px; font-size: 1.6rem; }
h2  { color: #79c0ff; margin: 28px 0 12px; font-size: 1.15rem;
      border-bottom: 1px solid #21262d; padding-bottom: 6px; }
.cards { display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 28px; }
.card  { background: #161b22; border: 1px solid #21262d; border-radius: 8px;
         padding: 16px 22px; min-width: 150px; flex: 1 1 150px; }
.card-label { font-size: 0.72rem; color: #8b949e; text-transform: uppercase;
              letter-spacing: .05em; margin-bottom: 6px; }
.card-value { font-size: 1.4rem; font-weight: 700; }
.pos { color: #3fb950; } .neg { color: #f85149; } .neu { color: #e3b341; }
table { width: 100%; border-collapse: collapse; font-size: 0.87rem;
        margin-bottom: 10px; }
th { background: #161b22; color: #8b949e; text-align: left;
     padding: 8px 10px; font-weight: 600; border-bottom: 2px solid #21262d; }
td { padding: 7px 10px; border-bottom: 1px solid #21262d; }
tr:hover td { background: #1c2128; }
.chart-wrap { margin: 10px 0 24px; }
.chart-wrap img { border-radius: 8px; max-width: 100%; }
.empty { color: #8b949e; font-style: italic; padding: 14px 0; }
.meta  { font-size: 0.82rem; color: #8b949e; margin-bottom: 6px; }
.footer { margin-top: 36px; font-size: 0.76rem; color: #484f58;
          border-top: 1px solid #21262d; padding-top: 12px; }
pre { background:#161b22; border:1px solid #21262d; border-radius:6px;
      padding:12px; font-size:0.78rem; overflow-x:auto; color:#adbac7; }
</style>
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cc(v: float) -> str:
    return "pos" if v > 0 else ("neg" if v < 0 else "neu")


def _pct(v: float) -> str:
    return f"{'+'if v>0 else ''}{v:.2f}%"


def _fig_to_b64(fig: plt.Figure) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


# ---------------------------------------------------------------------------
# plot_equity_curve
# ---------------------------------------------------------------------------


def plot_equity_curve(equity_curve: list[dict]) -> str:
    """Plot equity curve with drawdown shading; return base64-encoded PNG.

    Parameters
    ----------
    equity_curve:
        List of dicts each containing ``"portfolio_value"`` and optionally
        ``"date"`` keys — same structure produced by BacktestPortfolio.

    Returns
    -------
    str
        Base64-encoded PNG (no newlines).  Empty string when *equity_curve*
        is empty.
    """
    if not equity_curve:
        return ""

    values: list[float] = [float(s["portfolio_value"]) for s in equity_curve]
    labels: list[str] = [
        str(s["date"]) if "date" in s else str(i)
        for i, s in enumerate(equity_curve)
    ]

    # Compute running peak for drawdown shading
    peaks: list[float] = []
    running_peak = values[0]
    for v in values:
        if v > running_peak:
            running_peak = v
        peaks.append(running_peak)

    with plt.rc_context(_CHART_STYLE):
        fig, ax = plt.subplots(figsize=(11, 4))

        # Drawdown shading — red fill between curve and previous peak
        ax.fill_between(
            range(len(values)),
            values,
            peaks,
            where=[v < p for v, p in zip(values, peaks)],
            alpha=0.30,
            color="#f85149",
            label="Drawdown",
            interpolate=True,
        )

        ax.plot(range(len(values)), values,
                color="#00d4ff", linewidth=1.8, label="Portfolio Value")
        ax.fill_between(range(len(values)), values,
                        min(values), alpha=0.08, color="#00d4ff")

        ax.set_title("Equity Curve", fontsize=13, pad=10)
        ax.set_ylabel("Portfolio Value (₹)")
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: f"₹{x:,.0f}")
        )

        # x-axis: show at most 12 readable labels
        step = max(1, len(labels) // 12)
        ticks = list(range(0, len(labels), step))
        ax.set_xticks(ticks)
        ax.set_xticklabels([labels[i] for i in ticks],
                           rotation=30, ha="right", fontsize=7)
        ax.grid(True)
        ax.legend(fontsize=9)
        fig.tight_layout()

    return _fig_to_b64(fig)


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_cards(metrics: dict) -> str:
    inf_val = metrics.get("profit_factor", 0)
    pf_str = "∞" if inf_val == float("inf") else f"{inf_val:.2f}"

    cards = [
        ("CAGR",         f"{metrics.get('cagr', 0)*100:.2f}%",
         _cc(metrics.get("cagr", 0))),
        ("Total Return", _pct(metrics.get("total_return_pct", 0)),
         _cc(metrics.get("total_return_pct", 0))),
        ("Sharpe",       f"{metrics.get('sharpe_ratio', 0):.2f}",
         _cc(metrics.get("sharpe_ratio", 0))),
        ("Max DD",       f"{metrics.get('max_drawdown_pct', 0):.2f}%", "neg"),
        ("Win Rate",     f"{metrics.get('win_rate', 0)*100:.1f}%",    "neu"),
        ("Profit Factor", pf_str,                                      "neu"),
        ("Total Trades", str(metrics.get("total_trades", 0)),          "neu"),
        ("Avg R",        f"{metrics.get('avg_r_multiple', 0):.2f} R",
         _cc(metrics.get("avg_r_multiple", 0))),
    ]
    parts = [
        f'<div class="card">'
        f'<div class="card-label">{lbl}</div>'
        f'<div class="card-value {cls}">{val}</div>'
        f'</div>'
        for lbl, val, cls in cards
    ]
    return '<div class="cards">' + "\n".join(parts) + "</div>"


def _render_regime_table(trades: list[BacktestTrade]) -> str:
    """Regime breakdown: Regime | Trades | Win Rate | Avg P&L% | Avg R-Multiple."""
    if not trades:
        return '<p class="empty">No trades to show regime breakdown.</p>'

    buckets: dict[str, list[BacktestTrade]] = {}
    for t in trades:
        buckets.setdefault(t.regime, []).append(t)

    rows = []
    for regime in sorted(buckets):
        ts = buckets[regime]
        wins = sum(1 for t in ts if t.pnl > 0)
        wr = wins / len(ts)
        avg_pnl = sum(t.pnl_pct for t in ts) / len(ts)
        avg_r = sum(t.r_multiple for t in ts) / len(ts)
        rows.append(
            f"<tr><td><strong>{regime}</strong></td>"
            f"<td>{len(ts)}</td>"
            f"<td class='{_cc(wr - 0.5)}'>{wr*100:.1f}%</td>"
            f"<td class='{_cc(avg_pnl)}'>{_pct(avg_pnl)}</td>"
            f"<td class='{_cc(avg_r)}'>{avg_r:.2f} R</td></tr>"
        )
    hdr = ("<tr><th>Regime</th><th>Trades</th><th>Win Rate</th>"
           "<th>Avg P&amp;L%</th><th>Avg R-Multiple</th></tr>")
    return f"<table>{hdr}{''.join(rows)}</table>"


def _render_vcp_quality_table(trades: list[BacktestTrade]) -> str:
    """VCP quality breakdown: Quality | Trades | Win Rate | Avg R-Multiple."""
    if not trades:
        return '<p class="empty">No trades to show VCP quality breakdown.</p>'

    buckets: dict[str, list[BacktestTrade]] = {}
    for t in trades:
        buckets.setdefault(t.setup_quality or "Unknown", []).append(t)

    order = ["A+", "A", "B", "C", "Unknown"]
    rows = []
    for q in order + [k for k in buckets if k not in order]:
        if q not in buckets:
            continue
        ts = buckets[q]
        wins = sum(1 for t in ts if t.pnl > 0)
        wr = wins / len(ts)
        avg_r = sum(t.r_multiple for t in ts) / len(ts)
        rows.append(
            f"<tr><td><strong>{q}</strong></td>"
            f"<td>{len(ts)}</td>"
            f"<td class='{_cc(wr - 0.5)}'>{wr*100:.1f}%</td>"
            f"<td class='{_cc(avg_r)}'>{avg_r:.2f} R</td></tr>"
        )
    hdr = ("<tr><th>Quality</th><th>Trades</th>"
           "<th>Win Rate</th><th>Avg R-Multiple</th></tr>")
    return f"<table>{hdr}{''.join(rows)}</table>"


def _render_stop_comparison(
    trailing_metrics: dict | None,
    fixed_metrics: dict | None,
) -> str:
    """Stop-type comparison table (only when both were tested)."""
    if not trailing_metrics or not fixed_metrics:
        return ""

    def _row(label: str, m: dict) -> str:
        inf_val = m.get("profit_factor", 0)
        pf = "∞" if inf_val == float("inf") else f"{inf_val:.2f}"
        return (
            f"<tr><td><strong>{label}</strong></td>"
            f"<td class='{_cc(m.get('cagr',0))}'>{m.get('cagr',0)*100:.2f}%</td>"
            f"<td class='{_cc(m.get('sharpe_ratio',0))}'>{m.get('sharpe_ratio',0):.2f}</td>"
            f"<td class='neg'>{m.get('max_drawdown_pct',0):.2f}%</td>"
            f"<td>{m.get('win_rate',0)*100:.1f}%</td></tr>"
        )

    hdr = ("<tr><th>Stop Type</th><th>CAGR</th><th>Sharpe</th>"
           "<th>Max DD</th><th>Win Rate</th></tr>")
    return (
        "<h2>Trailing vs Fixed Stop Comparison</h2>"
        f"<table>{hdr}"
        f"{_row('Trailing Stop', trailing_metrics)}"
        f"{_row('Fixed Stop', fixed_metrics)}"
        "</table>"
    )


def _render_trades_table(
    trades: list[BacktestTrade],
    title: str,
    limit: int | None = None,
) -> str:
    if not trades:
        return f'<p class="empty">No trades to display.</p>'

    rows_data = trades[:limit] if limit else trades
    rows = []
    for t in rows_data:
        cls = _cc(t.pnl)
        rows.append(
            f"<tr>"
            f"<td><strong>{t.symbol}</strong></td>"
            f"<td>{t.entry_date}</td>"
            f"<td>{t.exit_date}</td>"
            f"<td>₹{t.entry_price:,.2f}</td>"
            f"<td>₹{t.exit_price:,.2f}</td>"
            f"<td class='{cls}'>{_pct(t.pnl_pct)}</td>"
            f"<td class='{cls}'>{t.r_multiple:.2f} R</td>"
            f"<td>{t.exit_reason}</td>"
            f"<td>{t.regime}</td>"
            f"<td>{t.setup_quality}</td>"
            f"<td>{t.stop_type}</td>"
            f"</tr>"
        )
    hdr = (
        "<tr><th>Symbol</th><th>Entry</th><th>Exit</th>"
        "<th>Entry ₹</th><th>Exit ₹</th><th>P&amp;L%</th><th>R</th>"
        "<th>Reason</th><th>Regime</th><th>Quality</th><th>Stop</th></tr>"
    )
    return f"<table>{hdr}{''.join(rows)}</table>"


# ---------------------------------------------------------------------------
# generate_report — main public function
# ---------------------------------------------------------------------------


def generate_report(
    result: BacktestResult,
    metrics: dict,
    output_dir: str,
    equity_curve: list[dict] | None = None,
    trailing_metrics: dict | None = None,
    fixed_metrics: dict | None = None,
) -> tuple[str, str]:
    """Generate HTML + CSV backtest report.

    Parameters
    ----------
    result:
        Completed ``BacktestResult`` from ``run_backtest()``.
    metrics:
        Performance metrics dict from ``compute_metrics()``.
    output_dir:
        Directory where files are written.  Created if absent.
    equity_curve:
        Optional list of equity snapshots for the chart.
    trailing_metrics:
        Metrics dict for the trailing-stop run (compare mode).
    fixed_metrics:
        Metrics dict for the fixed-stop run (compare mode).

    Returns
    -------
    tuple[str, str]
        ``(html_path, csv_path)``
    """
    os.makedirs(output_dir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"backtest_{result.start_date}_{result.end_date}_{ts}"
    html_path = os.path.join(output_dir, f"{base}.html")
    csv_path  = os.path.join(output_dir, f"{base}.csv")

    log.info("Generating backtest report → %s", html_path)

    trades  = result.trades
    winners = sorted([t for t in trades if t.pnl > 0], key=lambda t: t.pnl_pct, reverse=True)
    losers  = sorted([t for t in trades if t.pnl <= 0], key=lambda t: t.pnl_pct)
    all_sorted = sorted(trades, key=lambda t: t.entry_date)

    # --- Equity curve chart ---
    ec = equity_curve or []
    equity_b64 = plot_equity_curve(ec)
    equity_section = (
        f'<div class="chart-wrap">'
        f'<img src="data:image/png;base64,{equity_b64}" alt="Equity Curve">'
        f'</div>'
        if equity_b64
        else '<p class="empty">No equity data supplied for this run.</p>'
    )

    # --- Config snapshot ---
    import json as _json
    cfg_text = _json.dumps(result.config_snapshot, indent=2, default=str)

    # --- Stop comparison (optional) ---
    stop_comparison_html = _render_stop_comparison(trailing_metrics, fixed_metrics)

    # --- Main HTML assembly ---
    no_trades_note = (
        '<p class="empty">⚠ No trades were generated for this backtest run.</p>'
        if not trades else ""
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backtest Report — {result.start_date} to {result.end_date}</title>
{_CSS}
</head>
<body>
<h1>📊 Backtest Report &mdash; {result.start_date} to {result.end_date}</h1>

<p class="meta">
  Universe size: <strong>{result.universe_size}</strong> symbols &bull;
  Total trades: <strong>{len(trades)}</strong> &bull;
  Generated: <strong>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</strong>
</p>

{no_trades_note}

<h2>Key Metrics</h2>
{_render_cards(metrics)}

<h2>Equity Curve</h2>
{equity_section}

<h2>Regime Breakdown</h2>
{_render_regime_table(trades)}

<h2>VCP Quality Breakdown</h2>
{_render_vcp_quality_table(trades)}

{stop_comparison_html}

<h2>Top 10 Winning Trades</h2>
{_render_trades_table(winners, "Top 10 Winners", limit=10)}

<h2>Bottom 10 Losing Trades</h2>
{_render_trades_table(losers, "Bottom 10 Losers", limit=10)}

<h2>All Trades (sorted by Entry Date)</h2>
{_render_trades_table(all_sorted, "All Trades")}

<h2>Config Snapshot</h2>
<pre>{cfg_text}</pre>

<div class="footer">
  Generated by SEPA AI Backtesting Engine &bull; {result.start_date} – {result.end_date}
</div>
</body>
</html>"""

    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    log.info("HTML report written: %s (%d bytes)", html_path, len(html))

    # --- CSV ---
    _write_csv(csv_path, trades)
    log.info("CSV written: %s (%d rows)", csv_path, len(trades))

    return html_path, csv_path


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------


def _write_csv(path: str, trades: list[BacktestTrade]) -> None:
    """Write one CSV row per trade with all BacktestTrade fields."""
    fields = [f.name for f in dataclasses.fields(BacktestTrade)]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for t in trades:
            writer.writerow(dataclasses.asdict(t))
