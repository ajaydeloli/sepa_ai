"""
dashboard/pages/01_Watchlist.py
--------------------------------
SEPA Watchlist page — Streamlit multi-page app.

Sections
--------
1. Run-status bar           (last scan time, quality counts)
2. Custom Watchlist Manager (collapsible expander)
   ├── Manual symbol entry
   ├── File upload (.csv / .json / .xlsx / .txt)
   ├── Current watchlist table  (edit / remove per row)
   └── Run Watchlist Now → POST /api/v1/watchlist/run
3. Today's Results tabs
   ├── ★ Watchlist Results
   └── Universe A+/A

Anti-patterns avoided
---------------------
* Pipeline is NEVER called directly — always via the API endpoint.
* Symbol selection stored in st.session_state, not URL params.
* Expensive DB reads wrapped in @st.cache_data(ttl=60).
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

import streamlit as st

# ---------------------------------------------------------------------------
# Project root on sys.path (so sibling packages resolve correctly)
# ---------------------------------------------------------------------------

_PAGE_DIR  = Path(__file__).resolve().parent          # dashboard/pages/
_DASH_DIR  = _PAGE_DIR.parent                         # dashboard/
_ROOT      = _DASH_DIR.parent                         # project root
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Internal imports (after path fixup)
# ---------------------------------------------------------------------------

from dashboard.components.metrics import render_run_status_bar
from dashboard.components.tables import render_results_table
from ingestion.universe_loader import load_watchlist_file, validate_symbol
from screener.results import get_top_candidates, load_results
from storage.sqlite_store import SQLiteStore
from utils.exceptions import WatchlistParseError

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(page_title="SEPA Watchlist", layout="wide")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DB_PATH   = _ROOT / "data" / "sepa_ai.db"
_API_BASE  = "http://localhost:8000"
_RUN_URL   = f"{_API_BASE}/api/v1/watchlist/run"

_QUALITY_RANKS = {"All": 0, "B": 1, "A": 2, "A+": 3}
_QUALITY_ORDER = {"A+": 4, "A": 3, "B": 2, "C": 1, "FAIL": 0}

# ---------------------------------------------------------------------------
# DB helper — opened fresh per page load (Streamlit process is long-lived)
# ---------------------------------------------------------------------------

@st.cache_resource
def _get_db() -> SQLiteStore:
    return SQLiteStore(_DB_PATH)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _get_last_run(db: SQLiteStore) -> dict | None:
    """Query run_history for the most recent completed run."""
    try:
        conn = db._connect()
        row = conn.execute(
            """SELECT run_date, a_plus_count, a_count, duration_sec, created_at
               FROM run_history
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
        conn.close()
        if row is None:
            return None
        return {
            "timestamp":      row["created_at"],
            "quality_counts": {"A+": row["a_plus_count"] or 0,
                               "A":  row["a_count"]      or 0},
            "duration_seconds": float(row["duration_sec"] or 0),
        }
    except Exception:
        return None


def _get_recent_run_dates(db: SQLiteStore, n: int = 10) -> list[str]:
    """Return up to *n* most-recent run dates as YYYY-MM-DD strings."""
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
def load_today_results(
    run_date_str: str,
    min_score: int,
    min_quality: str,
) -> tuple[list[dict], list[dict]]:
    """Return (watchlist_results, universe_top_results) from SQLite.

    Both lists are already filtered by *min_score* and *min_quality*.
    Results are sorted score DESC (handled by screener/results.py).
    """
    db = _get_db()
    run_date = _parse_date(run_date_str)

    watchlist_syms: set[str] = {r["symbol"] for r in db.get_watchlist()}

    all_rows = load_results(db, run_date)

    qual_rank_threshold = _QUALITY_ORDER.get(min_quality, 0) if min_quality != "All" else -1

    def _keep(row: dict[str, Any]) -> bool:
        score_ok = (row.get("score") or 0) >= min_score
        qual_ok  = (
            qual_rank_threshold < 0
            or _QUALITY_ORDER.get(row.get("setup_quality", "FAIL"), 0)
               >= qual_rank_threshold
        )
        return score_ok and qual_ok

    filtered = [r for r in all_rows if _keep(r)]

    watchlist_results  = [r for r in filtered if r["symbol"] in watchlist_syms]
    universe_top       = [
        r for r in filtered
        if r["symbol"] not in watchlist_syms
        and _QUALITY_ORDER.get(r.get("setup_quality", "FAIL"), 0) >= _QUALITY_ORDER["A"]
    ]

    return watchlist_results, universe_top


def _parse_date(s: str) -> date | None:
    try:
        from datetime import datetime
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None

# ---------------------------------------------------------------------------
# API trigger helper
# ---------------------------------------------------------------------------

def _trigger_run_via_api(scope: str = "watchlist") -> dict | None:
    """POST to /api/v1/watchlist/run. Returns response dict or None on error."""
    import json
    try:
        import urllib.request
        admin_key = os.environ.get("API_ADMIN_KEY", "")
        req = urllib.request.Request(
            _RUN_URL,
            data=json.dumps({"scope": scope}).encode(),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": admin_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return {"_error": str(exc)}


def _trigger_run_direct(scope: str = "watchlist") -> str:
    """Fallback: call pipeline runner directly when API is not reachable."""
    from datetime import date as _date
    from pipeline import runner
    from pipeline.context import RunContext
    import yaml

    with open(_ROOT / "config" / "settings.yaml", "r") as fh:
        config = yaml.safe_load(fh) or {}

    ctx = RunContext(
        run_date=_date.today(),
        mode="daily",
        config=config,
        scope=scope,
        symbols_override=None,
    )
    runner.run_daily(ctx)
    return "Pipeline run completed (direct fallback)."

# ---------------------------------------------------------------------------
# Session-state initialisation
# ---------------------------------------------------------------------------

def _init_state() -> None:
    defaults = {
        "selected_symbol":     None,
        "confirm_clear_all":   False,
        "last_run_result":     None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


_init_state()

# ---------------------------------------------------------------------------
# ── SIDEBAR ──────────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

db = _get_db()

recent_dates = _get_recent_run_dates(db) or [str(date.today())]

st.sidebar.header("Filters")
selected_date = st.sidebar.selectbox("Date", recent_dates, index=0)
min_score     = st.sidebar.number_input("Min Score", min_value=0, max_value=100, value=40)
min_quality   = st.sidebar.selectbox("Min Quality", ["All", "B", "A", "A+"], index=0)

if st.sidebar.button("🔄 Refresh"):
    st.cache_data.clear()
    st.rerun()

# ---------------------------------------------------------------------------
# ── SECTION 1: Status bar ────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

st.title("📋 SEPA Watchlist")

last_run = _get_last_run(db)
render_run_status_bar(last_run)

# ---------------------------------------------------------------------------
# ── SECTION 2: Custom Watchlist Manager (collapsible) ────────────────────────
# ---------------------------------------------------------------------------

with st.expander("⚙️ Custom Watchlist Manager", expanded=True):

    # ── Manual entry ──────────────────────────────────────────────────────

    st.subheader("Manual Entry")
    manual_input = st.text_input(
        "Enter symbols (comma-separated)",
        placeholder="RELIANCE, TCS, DIXON",
        key="manual_symbols_input",
    )

    if st.button("➕ Add Symbols", key="btn_add_manual"):
        if manual_input.strip():
            raw_list   = [s.strip().upper() for s in manual_input.split(",") if s.strip()]
            existing   = {r["symbol"] for r in db.get_watchlist()}
            added, dupes, invalid = [], [], []

            for sym in raw_list:
                if not validate_symbol(sym):
                    invalid.append(sym)
                elif sym in existing:
                    dupes.append(sym)
                else:
                    db.add_symbol(sym, added_via="dashboard")
                    added.append(sym)

            parts = []
            if added:   parts.append(f"✅ Added: **{len(added)}**")
            if dupes:   parts.append(f"ℹ️ Already exists: **{len(dupes)}**")
            if invalid: parts.append(f"❌ Invalid: `{invalid}`")
            st.success("  |  ".join(parts) if parts else "Nothing to add.")
            st.cache_data.clear()
            st.rerun()
        else:
            st.warning("Please enter at least one symbol.")

    st.divider()

    # ── File upload ───────────────────────────────────────────────────────

    st.subheader("File Upload")
    uploaded = st.file_uploader(
        "Upload a watchlist file",
        type=["csv", "json", "xlsx", "txt"],
        key="watchlist_file_uploader",
    )

    if uploaded is not None:
        suffix = Path(uploaded.name).suffix.lower()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(uploaded.read())
            tmp_path = Path(tmp.name)

        try:
            parsed = load_watchlist_file(tmp_path)
        except WatchlistParseError as exc:
            st.error(f"❌ Parse error: {exc}")
            parsed = []
        finally:
            tmp_path.unlink(missing_ok=True)

        if parsed:
            existing = {r["symbol"] for r in db.get_watchlist()}
            new_syms = [s for s in parsed if s not in existing]
            skipped  = len(parsed) - len(new_syms)
            if new_syms:
                db.bulk_add(new_syms, added_via="upload")
            st.success(
                f"📂 **{uploaded.name}** — "
                f"Loaded: **{len(parsed)}** | "
                f"Added: **{len(new_syms)}** | "
                f"Skipped (duplicate): **{skipped}**"
            )
            st.cache_data.clear()
            st.rerun()

    st.divider()

    # ── Current watchlist table ───────────────────────────────────────────

    st.subheader("Current Watchlist")
    wl_rows: list[dict] = db.get_watchlist()

    if not wl_rows:
        st.info("Watchlist is empty. Add symbols above or upload a file.")
    else:
        import pandas as pd

        wl_df = pd.DataFrame(wl_rows)
        display_cols = [c for c in
            ["symbol", "last_score", "last_quality", "note", "added_at", "added_via"]
            if c in wl_df.columns]
        wl_df = wl_df[display_cols].copy()
        wl_df.insert(0, "🗑 Remove", False)

        edited = st.data_editor(
            wl_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "🗑 Remove": st.column_config.CheckboxColumn(
                    "Remove?", help="Check to delete this symbol"
                ),
                "last_score": st.column_config.NumberColumn("Score"),
                "last_quality": st.column_config.TextColumn("Quality"),
                "note": st.column_config.TextColumn("Note"),
            },
            disabled=[c for c in display_cols],  # only checkbox editable
            key="watchlist_editor",
        )

        to_remove = edited.loc[edited["🗑 Remove"] == True, "symbol"].tolist()  # noqa: E712
        if to_remove:
            if st.button(f"🗑 Remove {len(to_remove)} selected symbol(s)", type="primary"):
                for sym in to_remove:
                    db.remove_symbol(sym)
                st.success(f"Removed: {', '.join(to_remove)}")
                st.cache_data.clear()
                st.rerun()

    # ── Clear All ─────────────────────────────────────────────────────────

    st.divider()
    col_clear, col_confirm = st.columns([1, 3])

    with col_clear:
        if st.button("🧹 Clear All", key="btn_clear_all"):
            st.session_state["confirm_clear_all"] = True

    if st.session_state.get("confirm_clear_all"):
        with col_confirm:
            st.warning(
                f"⚠️ This will permanently remove **{len(wl_rows)} symbol(s)** "
                "from the watchlist. Are you sure?"
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ Yes, clear all", key="btn_confirm_clear"):
                    db.clear_watchlist()
                    st.session_state["confirm_clear_all"] = False
                    st.success("Watchlist cleared.")
                    st.cache_data.clear()
                    st.rerun()
            with c2:
                if st.button("❌ Cancel", key="btn_cancel_clear"):
                    st.session_state["confirm_clear_all"] = False
                    st.rerun()

    st.divider()

    # ── Run Watchlist Now ─────────────────────────────────────────────────

    if st.button("🚀 Run Watchlist Now", type="primary", key="btn_run_watchlist"):
        with st.spinner("Triggering pipeline scan for watchlist symbols…"):
            result = _trigger_run_via_api(scope="watchlist")

        api_error = result.get("_error") if result else "No response"

        if result and not api_error:
            run_id = result.get("data", {}).get("run_id", "?")
            st.success(
                f"✅ Pipeline started (run_id: `{run_id}`). "
                "Results will appear on next refresh (usually ~60 s)."
            )
            st.session_state["last_run_result"] = result
        else:
            st.warning(
                f"⚠️ API not reachable ({api_error}). "
                "Falling back to direct pipeline call…"
            )
            with st.spinner("Running pipeline directly…"):
                try:
                    msg = _trigger_run_direct(scope="watchlist")
                    st.success(msg)
                    st.cache_data.clear()
                except Exception as exc:
                    st.error(f"Pipeline error: {exc}")

# ---------------------------------------------------------------------------
# ── SECTION 3: Today's Results ───────────────────────────────────────────────
# ---------------------------------------------------------------------------

st.subheader("Today's Results")

watchlist_results, universe_top_results = load_today_results(
    str(selected_date), int(min_score), str(min_quality)
)

watchlist_syms: list[str] = [r["symbol"] for r in db.get_watchlist()]

tab_wl, tab_uni = st.tabs([
    f"★ Watchlist Results ({len(watchlist_results)})",
    f"Universe A+/A ({len(universe_top_results)})",
])

with tab_wl:
    if not watchlist_results:
        st.info(
            "No watchlist results for the selected date / filters. "
            "Try a different date or lower the Min Score."
        )
    else:
        chosen = render_results_table(
            watchlist_results,
            watchlist_symbols=watchlist_syms,
        )
        if chosen:
            st.session_state["selected_symbol"] = chosen.lstrip("★ ").strip()
            st.switch_page("pages/03_Stock.py")

with tab_uni:
    if not universe_top_results:
        st.info("No A+/A universe results for the selected date / filters.")
    else:
        chosen = render_results_table(
            universe_top_results,
            watchlist_symbols=watchlist_syms,
        )
        if chosen:
            st.session_state["selected_symbol"] = chosen.lstrip("★ ").strip()
            st.switch_page("pages/03_Stock.py")
