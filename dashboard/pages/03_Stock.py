"""
dashboard/pages/03_Stock.py
----------------------------
SEPA Stock deep-dive page — Streamlit multi-page app.

Sections
--------
1. Symbol & date selection
2. Score card  (render_score_card)
3. Chart tab   (render_ohlcv_chart + n_days slider + MA toggles)
4. Analysis tabs:
     📋 Trend Template | 🌀 VCP | 📈 Fundamentals | 💬 LLM Brief | 📅 History
5. Sidebar watchlist shortcuts
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------

_PAGE_DIR = Path(__file__).resolve().parent
_DASH_DIR = _PAGE_DIR.parent
_ROOT     = _DASH_DIR.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Internal imports
# ---------------------------------------------------------------------------

from dashboard.components.charts import render_ohlcv_chart
from dashboard.components.metrics import render_score_card
from dashboard.components.tables import (
    render_fundamental_scorecard,
    render_trend_template_checklist,
)
from storage.parquet_store import read_parquet
from storage.sqlite_store import SQLiteStore

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="SEPA Stock Deep-Dive", layout="wide")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DB_PATH = _ROOT / "data" / "sepa_ai.db"


# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

@st.cache_resource
def _get_db() -> SQLiteStore:
    return SQLiteStore(_DB_PATH)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_stock_data(symbol: str, run_date: str) -> tuple[dict, pd.DataFrame, dict]:
    """Return (sepa_result, ohlcv_df, vcp_metrics).

    sepa_result: full dict from result_json column in screen_results.
    ohlcv_df: OHLCV + MA columns from data/processed/{symbol}.parquet.
    vcp_metrics: nested dict from sepa_result, or {} if absent.
    """
    db = _get_db()

    # -- SEPA result ----------------------------------------------------------
    row = db.get_result(symbol.upper(), run_date)
    sepa_result: dict[str, Any] = {}
    if row:
        try:
            sepa_result = json.loads(row.get("result_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            sepa_result = dict(row)

    # -- OHLCV ----------------------------------------------------------------
    processed_path = _ROOT / "data" / "processed" / f"{symbol.upper()}.parquet"
    ohlcv_df = read_parquet(processed_path)

    # Try features parquet as fallback (has MA columns)
    if ohlcv_df.empty:
        features_path = _ROOT / "data" / "features" / f"{symbol.upper()}.parquet"
        ohlcv_df = read_parquet(features_path)

    # -- VCP metrics ----------------------------------------------------------
    vcp_metrics: dict[str, Any] = sepa_result.get("vcp_metrics") or {}

    return sepa_result, ohlcv_df, vcp_metrics


@st.cache_data(ttl=300)
def load_stock_history(symbol: str, days: int = 30) -> list[dict]:
    """Return list of {run_date, score, setup_quality} for *symbol* over last *days*."""
    db = _get_db()
    try:
        cutoff = (datetime.today() - timedelta(days=days)).strftime("%Y-%m-%d")
        conn = db._connect()
        rows = conn.execute(
            """SELECT run_date, score, setup_quality
               FROM screen_results
               WHERE symbol = ? AND run_date >= ?
               ORDER BY run_date ASC""",
            (symbol.upper(), cutoff),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_run_dates_for_symbol(symbol: str) -> list[str]:
    """Return run dates (DESC) that have a result for *symbol*."""
    db = _get_db()
    try:
        conn = db._connect()
        rows = conn.execute(
            "SELECT DISTINCT run_date FROM screen_results "
            "WHERE symbol = ? ORDER BY run_date DESC LIMIT 30",
            (symbol.upper(),),
        ).fetchall()
        conn.close()
        return [r["run_date"] for r in rows]
    except Exception:
        return [str(date.today())]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_all_symbols() -> list[str]:
    """Return all symbols that appear in screen_results."""
    db = _get_db()
    try:
        conn = db._connect()
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM screen_results ORDER BY symbol"
        ).fetchall()
        conn.close()
        return [r["symbol"] for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Session-state init
# ---------------------------------------------------------------------------

def _init_state() -> None:
    for key, val in {"selected_symbol": None}.items():
        if key not in st.session_state:
            st.session_state[key] = val

_init_state()

# ===========================================================================
# ── PAGE ─────────────────────────────────────────────────────────────────────
# ===========================================================================

st.title("🔍 Stock Deep-Dive")

# ── Symbol selection ──────────────────────────────────────────────────────────

all_symbols = _get_all_symbols()

preselected = st.session_state.get("selected_symbol")
default_idx = 0
if preselected and preselected in all_symbols:
    default_idx = all_symbols.index(preselected)

sel_col, date_col = st.columns([2, 2])

with sel_col:
    symbol: str = st.selectbox(
        "Symbol", all_symbols or ["—"],
        index=default_idx,
        key="stock_symbol",
    )

# ── Date selection ────────────────────────────────────────────────────────────

run_dates = _get_run_dates_for_symbol(symbol) if symbol != "—" else [str(date.today())]

with date_col:
    run_date: str = st.selectbox(
        "Run Date", run_dates, index=0, key="stock_date"
    )

if symbol == "—" or not run_dates:
    st.info("No screening data found. Run the screener first.")
    st.stop()


# ── Load data ─────────────────────────────────────────────────────────────────

with st.spinner(f"Loading data for {symbol}…"):
    sepa_result, ohlcv_df, vcp_metrics = load_stock_data(symbol, run_date)

# ── Row 1: Score card ─────────────────────────────────────────────────────────

score:       int = int(sepa_result.get("score") or 0)
quality:     str = str(sepa_result.get("setup_quality") or "—")
stage_label: str = str(sepa_result.get("stage_label") or f"Stage {sepa_result.get('stage', '?')}")

render_score_card(score, quality, stage_label)

st.divider()

# ── Row 2: Chart ──────────────────────────────────────────────────────────────

st.subheader("📈 Price Chart")

chart_c1, chart_c2, chart_c3 = st.columns([2, 1, 1])
with chart_c1:
    n_days: int = st.select_slider(
        "Lookback (days)", options=[30, 60, 90, 180], value=90, key="chart_ndays"
    )
with chart_c2:
    show_ma: bool = st.checkbox("Show MA Ribbons", value=True, key="chart_ma")
with chart_c3:
    show_vcp: bool = st.checkbox("Show VCP Zones", value=True, key="chart_vcp")

# Build a trimmed result dict that respects MA toggle
chart_result = dict(sepa_result)
if not show_ma:
    # Remove MA columns from ohlcv_df for this render call
    ohlcv_no_ma = ohlcv_df.drop(
        columns=[c for c in ohlcv_df.columns if c.startswith("sma_")],
        errors="ignore",
    )
    render_ohlcv_chart(
        symbol, ohlcv_no_ma, chart_result,
        vcp_metrics=vcp_metrics if show_vcp else None,
        n_days=n_days,
    )
else:
    render_ohlcv_chart(
        symbol, ohlcv_df, chart_result,
        vcp_metrics=vcp_metrics if show_vcp else None,
        n_days=n_days,
    )

st.divider()


# ── Row 3: Analysis tabs ──────────────────────────────────────────────────────

tab_tt, tab_vcp, tab_fund, tab_llm, tab_hist = st.tabs([
    "📋 Trend Template",
    "🌀 VCP",
    "📈 Fundamentals",
    "💬 LLM Brief",
    "📅 History",
])

# ── Tab 1: Trend Template ─────────────────────────────────────────────────────
with tab_tt:
    tt_details = sepa_result.get("trend_template_details") or {}
    if tt_details:
        render_trend_template_checklist(tt_details)
    else:
        st.info("Trend template details not available for this result.")

# ── Tab 2: VCP ────────────────────────────────────────────────────────────────
with tab_vcp:
    st.subheader("VCP Metrics")
    if vcp_metrics:
        vcp_c1, vcp_c2 = st.columns(2)
        with vcp_c1:
            st.metric("Contraction Count",  vcp_metrics.get("contraction_count", "—"))
            st.metric("Base Weeks",          vcp_metrics.get("base_weeks", "—"))
            st.metric("Vol Dry-up Ratio",    f"{vcp_metrics.get('vol_ratio', 0):.2f}"
                      if vcp_metrics.get("vol_ratio") is not None else "—")
        with vcp_c2:
            st.metric("Tightness",
                f"{vcp_metrics.get('tightness', 0):.2f}%"
                if vcp_metrics.get("tightness") is not None else "—"
            )
            depths = vcp_metrics.get("depths") or []
            if depths:
                st.metric("Pivot Depths", "  ›  ".join(f"{d:.1f}%" for d in depths))
        st.divider()
        st.markdown("""
**VCP Anatomy (schematic)**
```
Price │     ╱‾‾╲           ╱‾‾‾‾
      │    ╱    ╲         ╱
      │   ╱      ╲      ╱   ← Breakout
      │  ╱        ╲  ╱
      │ ╱       C1  C2  C3 → tighter contractions
      └──────────────────────── Time
```
Depth shrinks with each contraction (C1 > C2 > C3).  
Volume dries up into the base, then surges on breakout.
        """)
    else:
        st.info("VCP metrics not available — symbol may not have a qualified VCP pattern.")


# ── Tab 3: Fundamentals ───────────────────────────────────────────────────────
with tab_fund:
    fund_details = sepa_result.get("fundamental_details") or None
    render_fundamental_scorecard(fund_details)

    # EPS acceleration bar chart (last 4 quarters)
    eps_quarters: list[dict] = sepa_result.get("eps_quarters") or []
    if eps_quarters:
        st.subheader("EPS Acceleration (last 4 quarters)")
        eps_df = pd.DataFrame(eps_quarters)
        if "quarter" in eps_df.columns and "eps_growth_pct" in eps_df.columns:
            eps_df = eps_df.tail(4)
            fig, ax = plt.subplots(figsize=(7, 3))
            colours = ["#22c55e" if v >= 0 else "#ef4444"
                       for v in eps_df["eps_growth_pct"]]
            ax.bar(eps_df["quarter"].astype(str), eps_df["eps_growth_pct"],
                   color=colours)
            ax.axhline(0, color="#9ca3af", linewidth=0.8, linestyle="--")
            ax.set_ylabel("EPS Growth (%)")
            ax.set_title("Quarterly EPS Growth %")
            ax.grid(True, axis="y", linestyle=":", alpha=0.5)
            fig.tight_layout()
            st.pyplot(fig)
            plt.close(fig)

# ── Tab 4: LLM Brief ──────────────────────────────────────────────────────────
with tab_llm:
    llm_brief: str | None = sepa_result.get("llm_brief")
    if llm_brief:
        st.info(llm_brief)
    else:
        st.info(
            "LLM brief not generated "
            "(disabled or quality below threshold)."
        )


# ── Tab 5: History ────────────────────────────────────────────────────────────
with tab_hist:
    hist_days: int = st.slider(
        "History window (days)", min_value=7, max_value=90, value=30, key="hist_days"
    )
    history = load_stock_history(symbol, days=hist_days)

    if history:
        hist_df = pd.DataFrame(history)
        hist_df["run_date"] = pd.to_datetime(hist_df["run_date"])
        hist_df = hist_df.sort_values("run_date")

        # Score line chart
        fig, ax = plt.subplots(figsize=(10, 3))
        ax.plot(hist_df["run_date"], hist_df["score"],
                color="#3b82f6", linewidth=2, marker="o", markersize=4)
        ax.axhline(70, color="#22c55e", linestyle="--", linewidth=0.8, label="A threshold (70)")
        ax.axhline(40, color="#f59e0b", linestyle="--", linewidth=0.8, label="B threshold (40)")
        ax.set_ylabel("SEPA Score")
        ax.set_title(f"{symbol} — Score History ({hist_days}d)")
        ax.legend(fontsize=8)
        ax.grid(True, linestyle=":", alpha=0.5)
        fig.tight_layout()
        st.pyplot(fig)
        plt.close(fig)

        # Daily quality table
        display_hist = hist_df[["run_date", "score", "setup_quality"]].copy()
        display_hist["run_date"] = display_hist["run_date"].dt.strftime("%Y-%m-%d")
        display_hist.columns = ["Date", "Score", "Quality"]
        st.dataframe(display_hist, use_container_width=True, hide_index=True)
    else:
        st.info(f"No historical data found for {symbol} in the last {hist_days} days.")

st.divider()

# ── Row 4: Sidebar watchlist controls ─────────────────────────────────────────

db = _get_db()
watchlist_syms = {r["symbol"] for r in db.get_watchlist()}
in_watchlist   = symbol.upper() in watchlist_syms

with st.sidebar:
    st.header(f"⭐ {symbol}")
    st.caption(f"Quality: **{quality}**  |  Stage: **{stage_label}**  |  Score: **{score}**")
    st.divider()

    if not in_watchlist:
        if st.button("⭐ Add to Watchlist", type="primary", key="btn_wl_add"):
            db.add_symbol(symbol.upper(), added_via="dashboard")
            st.success(f"{symbol} added to watchlist.")
            st.rerun()
    else:
        st.success(f"✅ {symbol} is in your watchlist.")
        if st.button("🔴 Remove from Watchlist", key="btn_wl_remove"):
            db.remove_symbol(symbol.upper())
            st.warning(f"{symbol} removed from watchlist.")
            st.rerun()

    st.divider()
    if st.button("← Back to Screener", key="btn_back"):
        st.switch_page("pages/02_Screener.py")
