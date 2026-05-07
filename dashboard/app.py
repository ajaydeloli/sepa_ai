"""
dashboard/app.py
----------------
Streamlit entry point for the SEPA AI — Minervini Screener dashboard.

Multi-page navigation is handled by Streamlit's native page discovery
(files in dashboard/pages/ are auto-detected).  This file provides:

  * Global st.set_page_config()
  * Sidebar navigation header + API health indicator
  * Landing page with a quick-stats panel pulled from run_history

Conventions
-----------
* Pipeline is NEVER called directly here — all mutations go via the API.
* DB access is read-only on this landing page.
* No st.set_page_config() calls should appear in pages/ — each page
  sets its OWN page_title but the layout/icon is inherited from here.
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Project-root on sys.path (mirrors pattern used in every page)
# ---------------------------------------------------------------------------

_DASH_DIR = Path(__file__).resolve().parent   # dashboard/
_ROOT     = _DASH_DIR.parent                  # project root
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Third-party imports (after path fixup so project packages resolve)
# ---------------------------------------------------------------------------

import streamlit as st

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

from storage.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DB_PATH  = _ROOT / "data" / "sepa_ai.db"
_API_BASE = "http://localhost:8000"

# ---------------------------------------------------------------------------
# Streamlit page config — MUST be the first st.* call in the entry point
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="SEPA AI — Minervini Screener",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# DB helper
# ---------------------------------------------------------------------------

@st.cache_resource
def _get_db() -> SQLiteStore:
    """Return a shared (cached) SQLiteStore for the lifetime of the process."""
    return SQLiteStore(_DB_PATH)


# ---------------------------------------------------------------------------
# Meta helper — aggregates quick-stats from run_history
# ---------------------------------------------------------------------------

def _get_meta(db: SQLiteStore) -> dict[str, Any]:
    """Return a dict with keys used by the landing-page metrics panel.

    Falls back to safe defaults if the DB is empty or the table
    doesn't exist yet.
    """
    defaults: dict[str, Any] = {
        "last_screen_date": "Never",
        "a_plus_count":     0,
        "a_count":          0,
        "universe_size":    0,
    }
    try:
        conn = db._connect()
        row = conn.execute(
            """
            SELECT run_date, a_plus_count, a_count, universe_size
            FROM   run_history
            ORDER  BY created_at DESC
            LIMIT  1
            """
        ).fetchone()
        conn.close()
        if row is None:
            return defaults
        return {
            "last_screen_date": row["run_date"]     or "Never",
            "a_plus_count":     row["a_plus_count"] or 0,
            "a_count":          row["a_count"]      or 0,
            "universe_size":    row["universe_size"] or 0,
        }
    except Exception:
        return defaults


# ---------------------------------------------------------------------------
# ── SIDEBAR ──────────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

st.sidebar.title("📈 SEPA AI")
st.sidebar.caption("Minervini SEPA Screener v1.0")
st.sidebar.divider()

# -- API health indicator ----------------------------------------------------

def _check_api_health() -> str:
    """Return 'ok', 'error', or 'offline'."""
    url = f"{_API_BASE}/api/v1/health"
    try:
        if _HAS_REQUESTS:
            resp = _requests.get(url, timeout=2)
            return "ok" if resp.ok else "error"
        else:
            with urllib.request.urlopen(url, timeout=2) as r:
                return "ok" if r.status == 200 else "error"
    except Exception:
        return "offline"


with st.sidebar:
    status = _check_api_health()
    if status == "ok":
        st.success("✅ API connected")
    elif status == "error":
        st.warning("⚠️ API error")
    else:
        st.error("❌ API offline")

st.sidebar.divider()
st.sidebar.caption("Navigate using the pages above ↑")


# ---------------------------------------------------------------------------
# ── LANDING PAGE ─────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

st.title("📈 SEPA AI — Minervini Stock Screener")
st.markdown(
    """
    Welcome to the SEPA AI dashboard. Navigate using the sidebar:

    | Page | Purpose |
    |---|---|
    | **Watchlist** | Daily A+/A candidates + custom watchlist manager |
    | **Screener** | Full universe results with filters |
    | **Stock** | Single-stock deep-dive with chart + AI analysis |
    | **Portfolio** | Paper-trading portfolio tracker |
    | **Backtest** | Historical strategy performance |
    """
)

st.divider()

# ---------------------------------------------------------------------------
# Quick-stats panel
# ---------------------------------------------------------------------------

st.subheader("📊 Latest Screen Summary")

db   = _get_db()
meta = _get_meta(db)

col1, col2, col3, col4 = st.columns(4)

with col1:
    st.metric(
        label="Last Run",
        value=str(meta["last_screen_date"]),
        help="Date of the most recent completed daily screen",
    )
with col2:
    st.metric(
        label="A+ Setups",
        value=int(meta["a_plus_count"]),
        help="High-conviction breakout candidates from the last run",
    )
with col3:
    st.metric(
        label="A Setups",
        value=int(meta["a_count"]),
        help="Good-quality setups from the last run",
    )
with col4:
    st.metric(
        label="Universe",
        value=int(meta["universe_size"]),
        help="Total symbols screened in the last run",
    )

st.divider()

# ---------------------------------------------------------------------------
# Quick-navigation buttons (supplement the sidebar links)
# ---------------------------------------------------------------------------

st.subheader("🧭 Quick Navigation")

btn_col1, btn_col2, btn_col3, btn_col4, btn_col5 = st.columns(5)

with btn_col1:
    if st.button("📋 Watchlist", use_container_width=True):
        st.switch_page("pages/01_Watchlist.py")
with btn_col2:
    if st.button("🔍 Screener", use_container_width=True):
        st.switch_page("pages/02_Screener.py")
with btn_col3:
    if st.button("📈 Stock", use_container_width=True):
        st.switch_page("pages/03_Stock.py")
with btn_col4:
    if st.button("💼 Portfolio", use_container_width=True):
        st.switch_page("pages/04_Portfolio.py")
with btn_col5:
    if st.button("🔁 Backtest", use_container_width=True):
        st.switch_page("pages/05_Backtest.py")
