"""
dashboard/pages/02_Screener.py
--------------------------------
SEPA Full Universe Screener page — Streamlit multi-page app.

Sections
--------
1. Filter row  (Quality, Stage, Min RS, Sector, Min Price, Date)
2. Summary metrics  (Total screened, Stage 2, Passed TT, A+/A setups)
3. Results table  (render_results_table → click navigates to Stock page)
4. Export row  (Download CSV)
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

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

from dashboard.components.tables import render_results_table
from screener.results import load_results
from storage.sqlite_store import SQLiteStore

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="SEPA Screener", layout="wide")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DB_PATH        = _ROOT / "data" / "sepa_ai.db"
_SYMBOL_INFO    = _ROOT / "data" / "metadata" / "symbol_info.csv"
_QUALITY_ORDER  = {"A+": 4, "A": 3, "B": 2, "C": 1, "FAIL": 0}

# ---------------------------------------------------------------------------
# DB / data helpers
# ---------------------------------------------------------------------------

@st.cache_resource
def _get_db() -> SQLiteStore:
    return SQLiteStore(_DB_PATH)


@st.cache_data(ttl=60)
def _get_recent_run_dates(n: int = 10) -> list[str]:
    db = _get_db()
    try:
        conn = db._connect()
        rows = conn.execute(
            "SELECT DISTINCT run_date FROM screen_results "
            "ORDER BY run_date DESC LIMIT ?", (n,)
        ).fetchall()
        conn.close()
        return [r["run_date"] for r in rows]
    except Exception:
        return [str(date.today())]


@st.cache_data(ttl=60)
def _get_sectors() -> list[str]:
    try:
        df = pd.read_csv(_SYMBOL_INFO)
        sectors = sorted(df["sector"].dropna().unique().tolist())
        return ["All"] + sectors
    except Exception:
        return ["All"]


@st.cache_data(ttl=300)
def load_screener_results(run_date_str: str) -> list[dict[str, Any]]:
    """Load all screening results for *run_date_str* from SQLite."""
    db = _get_db()
    try:
        from datetime import datetime
        rd = datetime.strptime(run_date_str, "%Y-%m-%d").date()
        return load_results(db, rd)
    except Exception:
        return []


def _load_symbol_info() -> dict[str, str]:
    """Return {symbol: sector} mapping."""
    try:
        df = pd.read_csv(_SYMBOL_INFO)
        return dict(zip(df["symbol"].str.upper(), df["sector"].fillna("Unknown")))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------

def _apply_filters(
    results: list[dict],
    quality_filter: list[str],
    stage_filter: str,
    min_rs: int,
    sector_filter: str,
    min_price: float,
    sym_sector: dict[str, str],
) -> list[dict]:
    out = []
    for r in results:
        if quality_filter and r.get("setup_quality") not in quality_filter:
            continue
        if stage_filter != "All" and str(r.get("stage", "")) != stage_filter:
            continue
        if (r.get("rs_rating") or 0) < min_rs:
            continue
        if sector_filter != "All":
            sym = r.get("symbol", "").upper()
            if sym_sector.get(sym, "Unknown") != sector_filter:
                continue
        if min_price > 0 and (r.get("entry_price") or 0) < min_price:
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Session-state init
# ---------------------------------------------------------------------------

def _init_state() -> None:
    for key, val in {"selected_symbol": None}.items():
        if key not in st.session_state:
            st.session_state[key] = val

_init_state()

# ---------------------------------------------------------------------------
# ── PAGE ─────────────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

st.title("📊 Full Universe Screener")

db           = _get_db()
run_dates    = _get_recent_run_dates() or [str(date.today())]
sectors      = _get_sectors()
sym_sector   = _load_symbol_info()


# ── Filter row ────────────────────────────────────────────────────────────────

fc1, fc2, fc3, fc4, fc5, fc6 = st.columns([2, 1, 1, 2, 1, 2])

with fc1:
    quality_filter: list[str] = st.multiselect(
        "Quality", ["A+", "A", "B", "C", "FAIL"],
        default=[], key="scr_quality"
    )
with fc2:
    stage_filter: str = st.selectbox(
        "Stage", ["All", "1", "2", "3", "4"], key="scr_stage"
    )
with fc3:
    min_rs: int = st.slider(
        "Min RS", min_value=0, max_value=99, value=0, key="scr_rs"
    )
with fc4:
    sector_filter: str = st.selectbox(
        "Sector", sectors, index=0, key="scr_sector"
    )
with fc5:
    min_price: float = st.number_input(
        "Min Price (₹)", min_value=0.0, value=0.0, step=10.0, key="scr_price"
    )
with fc6:
    selected_date: str = st.selectbox(
        "Run Date", run_dates, index=0, key="scr_date"
    )

if st.button("🔄 Refresh data", key="scr_refresh"):
    st.cache_data.clear()
    st.rerun()

# ── Load & filter ─────────────────────────────────────────────────────────────

all_results = load_screener_results(selected_date)
filtered    = _apply_filters(
    all_results, quality_filter, stage_filter,
    min_rs, sector_filter, min_price, sym_sector,
)


# ── Summary metrics ───────────────────────────────────────────────────────────

mc1, mc2, mc3, mc4 = st.columns(4)

total_screened = len(all_results)
stage2_count   = sum(1 for r in filtered if str(r.get("stage", "")) == "2")
passed_tt      = sum(1 for r in filtered if r.get("trend_template_pass"))
aplus_a_count  = sum(
    1 for r in filtered
    if r.get("setup_quality") in ("A+", "A")
)

with mc1:
    st.metric("Total Screened", total_screened)
with mc2:
    st.metric("Stage 2", stage2_count)
with mc3:
    st.metric("Passed TT", passed_tt)
with mc4:
    st.metric("A+/A Setups", aplus_a_count)

st.caption(f"Showing **{len(filtered)}** of {total_screened} results")
st.divider()

# ── Results table ─────────────────────────────────────────────────────────────

watchlist_syms = [r["symbol"] for r in db.get_watchlist()]

chosen = render_results_table(filtered, watchlist_symbols=watchlist_syms)
if chosen:
    st.session_state["selected_symbol"] = chosen.lstrip("★ ").strip()
    st.switch_page("pages/03_Stock.py")

st.divider()

# ── Export row ────────────────────────────────────────────────────────────────

if filtered:
    export_df = pd.DataFrame(filtered)
    csv_bytes  = export_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="📥 Download CSV",
        data=csv_bytes,
        file_name=f"sepa_screener_{selected_date}.csv",
        mime="text/csv",
        key="scr_download",
    )
else:
    st.info("Apply filters and wait for results before exporting.")
