"""
dashboard/pages/04_Portfolio.py
--------------------------------
Paper Trading Portfolio page — SEPA Streamlit dashboard.

Sections
--------
1. Summary KPI cards     (Total Return, Realised P&L, Win Rate, Open Positions)
2. Equity curve chart
3. Tabs
   ├── 📂 Open Positions   – live table with per-row close button
   ├── 📜 Closed Trades    – all closed trades, sorted by exit_date DESC
   └── 📊 Statistics       – quality breakdown, monthly P&L bar, hold histogram
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st
import yaml

# ── project root on sys.path ──────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dashboard.components.charts import render_equity_curve
from dashboard.components.metrics import render_portfolio_summary_cards
from paper_trading.portfolio import Portfolio
from paper_trading.report import get_monthly_pnl, get_quality_breakdown
from paper_trading.simulator import load_state, save_state

# ── page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Paper Trading Portfolio", layout="wide")

# ── constants ─────────────────────────────────────────────────────────────────
_CONFIG_PATH = _ROOT / "config" / "settings.yaml"
_PT_DIR      = _ROOT / "data" / "paper_trading"


# ── helpers ───────────────────────────────────────────────────────────────────

@st.cache_resource
def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        with _CONFIG_PATH.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def _get_current_prices(portfolio: Portfolio) -> dict[str, float]:
    """Try to read latest close from parquet; fall back to entry_price."""
    prices: dict[str, float] = {}
    features_dir = _ROOT / "data" / "features"
    for symbol, pos in portfolio.positions.items():
        parquet = features_dir / f"{symbol}.parquet"
        if parquet.exists():
            try:
                from storage.parquet_store import read_last_n_rows
                df = read_last_n_rows(parquet, 1)
                if not df.empty and "close" in df.columns:
                    prices[symbol] = float(df["close"].iloc[-1])
                    continue
            except Exception:
                pass
        prices[symbol] = pos.entry_price
    return prices


def _close_position(portfolio: Portfolio, symbol: str, price: float) -> None:
    """Manually close *symbol* at *price*, then persist state."""
    portfolio.close_position(symbol, price, "manual", date.today())
    save_state(portfolio)
    st.session_state["_portfolio_dirty"] = True


def _colour_row(val: Any, positive_green: bool = True) -> str:
    """Return a CSS background colour string for a numeric cell."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v > 0:
        return "background-color: #16a34a22" if positive_green else "background-color: #dc262622"
    if v < 0:
        return "background-color: #dc262622" if positive_green else "background-color: #16a34a22"
    return ""


# ── session state ─────────────────────────────────────────────────────────────

if "_portfolio_dirty" not in st.session_state:
    st.session_state["_portfolio_dirty"] = False

# ── load state ────────────────────────────────────────────────────────────────

config    = _load_config()
portfolio = load_state(config)

if not _PT_DIR.exists() or not (_PT_DIR / "portfolio.json").exists():
    st.title("💼 Paper Trading Portfolio")
    st.warning(
        "No paper trades yet. Run the daily screener to generate signals."
    )
    st.stop()

current_prices = _get_current_prices(portfolio)
summary        = portfolio.get_summary(current_prices)

# Normalise keys for render_portfolio_summary_cards
summary_cards: dict[str, Any] = {
    "total_return_pct": summary["total_return_pct"],
    "realised_pnl":     summary["realised_pnl"],
    "win_rate_pct":     summary["win_rate"] * 100.0,
    "open_positions":   summary["open_count"],
}

# ── page title & KPI row ──────────────────────────────────────────────────────

st.title("💼 Paper Trading Portfolio")

render_portfolio_summary_cards(summary_cards)

# ── equity curve ──────────────────────────────────────────────────────────────

render_equity_curve(portfolio.equity_curve)

# ── tabs ──────────────────────────────────────────────────────────────────────

tab_open, tab_closed, tab_stats = st.tabs([
    "📂 Open Positions",
    "📜 Closed Trades",
    "📊 Statistics",
])


# ── Tab 1: Open Positions ─────────────────────────────────────────────────────

with tab_open:
    open_positions = summary.get("positions", [])

    if not open_positions:
        st.info("No open positions at the moment.")
    else:
        st.caption(
            f"Portfolio cash: ₹{summary['cash']:,.0f}  |  "
            f"Open value: ₹{summary['open_value']:,.0f}  |  "
            f"Total: ₹{summary['total_value']:,.0f}"
        )

        for pos in open_positions:
            pnl_pct = pos["unrealised_pnl_pct"]
            row_colour = "#16a34a18" if pnl_pct >= 0 else "#dc262618"
            with st.container():
                cols = st.columns([2, 1.5, 1.5, 1.5, 1, 1.5, 1.5, 1, 1.2])
                cols[0].markdown(f"**{pos['symbol']}**")
                cols[1].metric("Entry ₹",   f"₹{pos['entry_price']:,.2f}")
                cols[2].metric("Current ₹", f"₹{pos['current_price']:,.2f}")

                delta_str = f"{pnl_pct:+.2f}%"
                delta_col = "normal" if pnl_pct >= 0 else "inverse"
                cols[3].metric("Unreal P&L%", delta_str, delta_str, delta_color=delta_col)

                cols[4].metric("Days", pos["days_held"])
                cols[5].metric("Stop Loss",     f"₹{pos['stop_loss']:,.2f}")
                cols[6].metric("Trail Stop",    f"₹{pos['trailing_stop']:,.2f}")
                cols[7].metric("Quality",       pos.get("quality", "—"))

                close_key = f"close_{pos['symbol']}"
                if cols[8].button("🚪 Close", key=close_key, type="secondary"):
                    _close_position(portfolio, pos["symbol"], pos["current_price"])
                    st.success(f"Closed {pos['symbol']} @ ₹{pos['current_price']:,.2f}")
                    st.rerun()

                st.markdown(
                    f'<hr style="margin:4px 0; border-color:{row_colour}; border-width:3px">',
                    unsafe_allow_html=True,
                )


# ── Tab 2: Closed Trades ──────────────────────────────────────────────────────

with tab_closed:
    closed = sorted(portfolio.closed_trades, key=lambda t: t.exit_date, reverse=True)

    if not closed:
        st.info("No closed trades yet.")
    else:
        rows = []
        for t in closed:
            rows.append({
                "Symbol":     t.symbol,
                "Entry Date": str(t.entry_date),
                "Exit Date":  str(t.exit_date),
                "Entry ₹":    round(t.entry_price, 2),
                "Exit ₹":     round(t.exit_price, 2),
                "P&L%":       round(t.pnl_pct, 2),
                "P&L ₹":      round(t.pnl, 2),
                "R-Multiple": round(t.r_multiple, 3),
                "Reason":     t.exit_reason,
                "Quality":    getattr(t, "setup_quality", "—"),
            })

        df_closed = pd.DataFrame(rows)

        def _style_closed(row: pd.Series) -> list[str]:
            colour = "#16a34a18" if row["P&L%"] > 0 else "#dc262618"
            base = [f"background-color: {colour}"] * len(row)
            # Bold R-Multiple if > 2.0
            r_idx = list(row.index).index("R-Multiple")
            if row["R-Multiple"] > 2.0:
                base[r_idx] = base[r_idx] + "; font-weight: bold"
            return base

        st.dataframe(
            df_closed.style.apply(_style_closed, axis=1),
            use_container_width=True,
            hide_index=True,
        )


# ── Tab 3: Statistics ─────────────────────────────────────────────────────────

with tab_stats:
    closed_all = portfolio.closed_trades

    if not closed_all:
        st.info("No closed trades to compute statistics from.")
    else:
        # --- Quality breakdown table ---
        st.subheader("Quality Breakdown")
        breakdown = get_quality_breakdown(closed_all)
        order = ["A+", "A", "B", "C", "Unknown"]
        qual_rows = []
        for q in order + [k for k in breakdown if k not in order]:
            if q not in breakdown:
                continue
            d = breakdown[q]
            qual_rows.append({
                "Quality":  q,
                "Trades":   d["trades"],
                "Wins":     d["wins"],
                "Win Rate": f"{d['win_rate'] * 100:.1f}%",
                "Avg R":    round(d["avg_r"], 3),
            })
        st.dataframe(pd.DataFrame(qual_rows), use_container_width=True, hide_index=True)

        st.divider()

        # --- Monthly P&L bar chart ---
        st.subheader("Monthly P&L")
        monthly = get_monthly_pnl(closed_all)
        if monthly:
            months  = list(monthly.keys())
            pnl_vals = list(monthly.values())
            colours = ["#22c55e" if v >= 0 else "#ef4444" for v in pnl_vals]

            fig_m, ax_m = plt.subplots(figsize=(max(6, len(months) * 0.7), 4))
            ax_m.bar(months, pnl_vals, color=colours, edgecolor="#374151", linewidth=0.5)
            ax_m.axhline(0, color="#9ca3af", linewidth=0.8)
            ax_m.set_title("Monthly Realised P&L (₹)", fontsize=12)
            ax_m.set_ylabel("P&L (₹)")
            plt.xticks(rotation=45, ha="right", fontsize=8)
            ax_m.grid(True, axis="y", linestyle=":", alpha=0.5)
            fig_m.tight_layout()
            st.pyplot(fig_m)
            plt.close(fig_m)

        st.divider()

        # --- Hold-time histogram ---
        st.subheader("Hold Time Distribution")
        days_held = [(t.exit_date - t.entry_date).days for t in closed_all]
        if days_held:
            fig_h, ax_h = plt.subplots(figsize=(8, 3))
            ax_h.hist(days_held, bins=min(20, len(days_held)),
                      color="#3b82f6", edgecolor="#1e3a5f", alpha=0.85)
            ax_h.set_xlabel("Days Held")
            ax_h.set_ylabel("# Trades")
            ax_h.set_title("Hold-Time Distribution")
            ax_h.grid(True, axis="y", linestyle=":", alpha=0.5)
            fig_h.tight_layout()
            st.pyplot(fig_h)
            plt.close(fig_h)

