"""
paper_trading/report.py
-----------------------
Performance report generator for the SEPA AI paper trading engine.

Produces a self-contained HTML file with:
  1. Summary cards  (total return, realised P&L, win rate, avg R, profit factor,
                     total trades, open positions)
  2. Equity curve   (matplotlib → base64 PNG → embedded <img>)
  3. Open positions table
  4. Closed trades table
  5. Quality breakdown  (win rate by setup_quality)
  6. Hold-time histogram (matplotlib → base64 PNG → embedded <img>)

No external CSS or JS dependencies — everything is inline.
"""

from __future__ import annotations

import base64
import io
import os
from collections import defaultdict
from datetime import date
from typing import TYPE_CHECKING

import matplotlib
matplotlib.use("Agg")           # non-interactive backend — safe in all envs
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from paper_trading.portfolio import ClosedTrade, Portfolio
from utils.logger import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_quality_breakdown(trades: list[ClosedTrade]) -> dict:
    """Return win-rate and avg R-multiple grouped by setup_quality.

    Example return value::

        {
            "A+": {"trades": 5, "wins": 4, "win_rate": 0.80, "avg_r": 2.1},
            "A":  {"trades": 3, "wins": 2, "win_rate": 0.667, "avg_r": 1.2},
        }

    ``setup_quality`` is read via ``getattr`` with fallback ``"Unknown"``
    so the function is safe even when older ClosedTrade objects lack the field.
    """
    buckets: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "r_sum": 0.0})

    for t in trades:
        quality = getattr(t, "setup_quality", "Unknown")
        buckets[quality]["trades"] += 1
        if t.pnl > 0:
            buckets[quality]["wins"] += 1
        buckets[quality]["r_sum"] += t.r_multiple

    result: dict = {}
    for quality, data in buckets.items():
        n = data["trades"]
        result[quality] = {
            "trades":   n,
            "wins":     data["wins"],
            "win_rate": round(data["wins"] / n, 4) if n else 0.0,
            "avg_r":    round(data["r_sum"]  / n, 4) if n else 0.0,
        }
    return result


def get_monthly_pnl(trades: list[ClosedTrade]) -> dict[str, float]:
    """Return cumulative P&L grouped by calendar month.

    Groups by ``exit_date`` and returns a ``dict`` keyed by ``"YYYY-MM"``::

        {"2024-01": 12500.0, "2024-02": -3200.0}

    Months with no closed trades are omitted.
    """
    monthly: dict[str, float] = defaultdict(float)
    for t in trades:
        key = t.exit_date.strftime("%Y-%m")
        monthly[key] += t.pnl
    return dict(sorted(monthly.items()))


# ---------------------------------------------------------------------------
# Private chart helpers
# ---------------------------------------------------------------------------

_CHART_STYLE = {
    "figure.facecolor":  "#1a1a2e",
    "axes.facecolor":    "#16213e",
    "axes.edgecolor":    "#0f3460",
    "axes.labelcolor":   "#e0e0e0",
    "xtick.color":       "#a0a0a0",
    "ytick.color":       "#a0a0a0",
    "text.color":        "#e0e0e0",
    "grid.color":        "#0f3460",
    "grid.linestyle":    "--",
    "grid.alpha":        0.6,
}


def _fig_to_b64(fig: plt.Figure) -> str:
    """Render *fig* to a base64-encoded PNG string (no newlines)."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def _build_equity_chart(equity_curve: list[dict]) -> str:
    """Return base64 PNG of the equity curve, or empty string if no data."""
    if not equity_curve:
        return ""

    dates  = [e["date"]        for e in equity_curve]
    values = [e["total_value"] for e in equity_curve]

    with plt.rc_context(_CHART_STYLE):
        fig, ax = plt.subplots(figsize=(10, 3.5))
        ax.plot(dates, values, color="#00d4ff", linewidth=1.8, label="Portfolio Value")
        ax.fill_between(dates, values, alpha=0.15, color="#00d4ff")
        ax.set_title("Equity Curve", fontsize=13, pad=10)
        ax.set_xlabel("Date")
        ax.set_ylabel("Portfolio Value (₹)")
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(
            lambda x, _: f"₹{x:,.0f}"
        ))
        # Show at most 10 x-tick labels to avoid crowding
        step = max(1, len(dates) // 10)
        ax.set_xticks(dates[::step])
        ax.set_xticklabels(dates[::step], rotation=30, ha="right", fontsize=7)
        ax.grid(True)
        ax.legend(fontsize=9)
        fig.tight_layout()
    return _fig_to_b64(fig)


def _build_hold_histogram(trades: list[ClosedTrade]) -> str:
    """Return base64 PNG of a hold-time histogram, or empty string if no trades."""
    if not trades:
        return ""

    days_held = [(t.exit_date - t.entry_date).days for t in trades]

    with plt.rc_context(_CHART_STYLE):
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.hist(days_held, bins=min(20, len(days_held)), color="#f5a623",
                edgecolor="#1a1a2e", alpha=0.85)
        ax.set_title("Hold-Time Distribution", fontsize=13, pad=10)
        ax.set_xlabel("Days Held")
        ax.set_ylabel("# Trades")
        ax.grid(True, axis="y")
        fig.tight_layout()
    return _fig_to_b64(fig)


# ---------------------------------------------------------------------------
# HTML template helpers
# ---------------------------------------------------------------------------

_CSS = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #0d1117;
         color: #c9d1d9; padding: 24px; }
  h1   { color: #58a6ff; margin-bottom: 20px; font-size: 1.6rem; }
  h2   { color: #79c0ff; margin: 28px 0 12px; font-size: 1.15rem;
         border-bottom: 1px solid #21262d; padding-bottom: 6px; }
  .cards { display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 28px; }
  .card  { background: #161b22; border: 1px solid #21262d; border-radius: 8px;
           padding: 16px 22px; min-width: 160px; flex: 1 1 160px; }
  .card-label { font-size: 0.75rem; color: #8b949e; text-transform: uppercase;
                letter-spacing: .05em; margin-bottom: 6px; }
  .card-value { font-size: 1.5rem; font-weight: 700; }
  .pos  { color: #3fb950; }
  .neg  { color: #f85149; }
  .neu  { color: #e3b341; }
  table { width: 100%; border-collapse: collapse; font-size: 0.88rem;
          margin-bottom: 10px; }
  th   { background: #161b22; color: #8b949e; text-align: left;
         padding: 8px 10px; font-weight: 600; border-bottom: 2px solid #21262d; }
  td   { padding: 7px 10px; border-bottom: 1px solid #21262d; }
  tr:hover td { background: #1c2128; }
  .chart-wrap { margin: 10px 0 24px; }
  .chart-wrap img { border-radius: 8px; max-width: 100%; }
  .empty { color: #8b949e; font-style: italic; padding: 14px 0; }
  .footer { margin-top: 36px; font-size: 0.76rem; color: #484f58;
            border-top: 1px solid #21262d; padding-top: 12px; }
</style>
"""


def _color_class(value: float) -> str:
    if value > 0:
        return "pos"
    if value < 0:
        return "neg"
    return "neu"


def _fmt_pct(v: float) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.2f} %"


def _fmt_inr(v: float) -> str:
    sign = "+" if v > 0 else ""
    return f"{sign}₹{v:,.0f}"


def _render_summary_cards(summary: dict) -> str:
    cards = [
        ("Total Return",    _fmt_pct(summary["total_return_pct"]),
         _color_class(summary["total_return_pct"])),
        ("Realised P&L",    _fmt_inr(summary["realised_pnl"]),
         _color_class(summary["realised_pnl"])),
        ("Win Rate",        f"{summary['win_rate'] * 100:.1f} %",       "neu"),
        ("Avg R-Multiple",  f"{summary['avg_r_multiple']:.2f} R",
         _color_class(summary["avg_r_multiple"])),
        ("Profit Factor",   f"{summary['profit_factor']:.2f}",          "neu"),
        ("Total Trades",    str(summary["total_trades"]),                "neu"),
        ("Open Positions",  str(summary["open_count"]),                  "neu"),
    ]
    parts = []
    for label, value, cls in cards:
        parts.append(
            f'<div class="card">'
            f'<div class="card-label">{label}</div>'
            f'<div class="card-value {cls}">{value}</div>'
            f'</div>'
        )
    return '<div class="cards">' + "\n".join(parts) + "</div>"


def _render_open_positions(positions_list: list[dict]) -> str:
    if not positions_list:
        return '<p class="empty">No open positions.</p>'
    rows = []
    for p in positions_list:
        cls = _color_class(p["unrealised_pnl_pct"])
        rows.append(
            f"<tr>"
            f"<td><strong>{p['symbol']}</strong></td>"
            f"<td>₹{p['entry_price']:,.2f}</td>"
            f"<td>₹{p['current_price']:,.2f}</td>"
            f"<td class='{cls}'>{_fmt_pct(p['unrealised_pnl_pct'])}</td>"
            f"<td>{p['days_held']}</td>"
            f"<td>₹{p['stop_loss']:,.2f}</td>"
            f"<td>{p.get('quality', '—')}</td>"
            f"</tr>"
        )
    header = (
        "<tr><th>Symbol</th><th>Entry</th><th>Current</th>"
        "<th>Unreal P&L%</th><th>Days</th><th>Stop</th><th>Quality</th></tr>"
    )
    return f"<table>{header}{''.join(rows)}</table>"


def _render_closed_trades(trades: list[ClosedTrade]) -> str:
    if not trades:
        return '<p class="empty">No closed trades yet.</p>'
    rows = []
    for t in trades:
        cls = _color_class(t.pnl)
        rows.append(
            f"<tr>"
            f"<td><strong>{t.symbol}</strong></td>"
            f"<td>{t.entry_date}</td>"
            f"<td>{t.exit_date}</td>"
            f"<td>₹{t.entry_price:,.2f}</td>"
            f"<td>₹{t.exit_price:,.2f}</td>"
            f"<td class='{cls}'>{_fmt_pct(t.pnl_pct)}</td>"
            f"<td class='{cls}'>{t.r_multiple:.2f} R</td>"
            f"<td>{t.exit_reason}</td>"
            f"</tr>"
        )
    header = (
        "<tr><th>Symbol</th><th>Entry Date</th><th>Exit Date</th>"
        "<th>Entry ₹</th><th>Exit ₹</th><th>P&L%</th><th>R</th><th>Reason</th></tr>"
    )
    return f"<table>{header}{''.join(rows)}</table>"


def _render_quality_breakdown(breakdown: dict) -> str:
    if not breakdown:
        return '<p class="empty">No quality data available.</p>'
    order = ["A+", "A", "B", "C", "Unknown"]
    rows = []
    for q in order + [k for k in breakdown if k not in order]:
        if q not in breakdown:
            continue
        d = breakdown[q]
        r_cls = _color_class(d["avg_r"])
        rows.append(
            f"<tr>"
            f"<td><strong>{q}</strong></td>"
            f"<td>{d['trades']}</td>"
            f"<td>{d['wins']}</td>"
            f"<td>{d['win_rate'] * 100:.1f} %</td>"
            f"<td class='{r_cls}'>{d['avg_r']:.2f} R</td>"
            f"</tr>"
        )
    header = (
        "<tr><th>Quality</th><th>Trades</th><th>Wins</th>"
        "<th>Win Rate</th><th>Avg R</th></tr>"
    )
    return f"<table>{header}{''.join(rows)}</table>"


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------


def generate_performance_report(
    portfolio: Portfolio,
    current_prices: dict[str, float],
    output_dir: str,
    run_date: date,
) -> str:
    """Generate a self-contained HTML performance report.

    Parameters
    ----------
    portfolio:
        Live in-memory portfolio with open positions and closed-trade history.
    current_prices:
        Latest mark-to-market prices for all held symbols.
    output_dir:
        Directory where the report is written.  Created if absent.
    run_date:
        Date label used in the filename and report heading.

    Returns
    -------
    str
        Absolute path to the generated HTML file.
    """
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"paper_trading_{run_date}.html")

    log.info("Generating performance report → %s", out_path)

    summary   = portfolio.get_summary(current_prices)
    trades    = portfolio.closed_trades
    breakdown = get_quality_breakdown(trades)
    monthly   = get_monthly_pnl(trades)

    # --- charts -------------------------------------------------------
    equity_b64   = _build_equity_chart(portfolio.equity_curve)
    holdtime_b64 = _build_hold_histogram(trades)

    equity_section = (
        f'<div class="chart-wrap">'
        f'<img src="data:image/png;base64,{equity_b64}" alt="Equity Curve">'
        f'</div>'
        if equity_b64
        else '<p class="empty">No equity data recorded yet.</p>'
    )

    holdtime_section = (
        f'<div class="chart-wrap">'
        f'<img src="data:image/png;base64,{holdtime_b64}" alt="Hold-Time Distribution">'
        f'</div>'
        if holdtime_b64
        else '<p class="empty">No closed trades for histogram.</p>'
    )

    # --- monthly P&L mini-table ---------------------------------------
    if monthly:
        mrows = "".join(
            f"<tr><td>{m}</td><td class='{_color_class(v)}'>{_fmt_inr(v)}</td></tr>"
            for m, v in monthly.items()
        )
        monthly_section = (
            '<table><tr><th>Month</th><th>P&amp;L</th></tr>'
            + mrows + '</table>'
        )
    else:
        monthly_section = '<p class="empty">No monthly data.</p>'

    # --- stitch together ----------------------------------------------
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Paper Trading Report — {run_date}</title>
{_CSS}
</head>
<body>
<h1>📈 Paper Trading Report &mdash; {run_date}</h1>

<h2>Performance Summary</h2>
{_render_summary_cards(summary)}

<h2>Equity Curve</h2>
{equity_section}

<h2>Open Positions</h2>
{_render_open_positions(summary["positions"])}

<h2>Closed Trades</h2>
{_render_closed_trades(trades)}

<h2>Quality Breakdown</h2>
{_render_quality_breakdown(breakdown)}

<h2>Monthly P&amp;L</h2>
{monthly_section}

<h2>Hold-Time Distribution</h2>
{holdtime_section}

<div class="footer">
  Generated by SEPA AI paper trading engine &bull; {run_date}
</div>
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)

    log.info("Report written: %s  (%d bytes)", out_path, len(html))
    return out_path
