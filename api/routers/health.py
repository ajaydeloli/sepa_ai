"""
api/routers/health.py
---------------------
Health and metadata endpoints for the SEPA AI API.

Routes (no auth required — intentionally public):
  GET /api/v1/health   — liveness probe + last-run status
  GET /api/v1/meta     — universe / watchlist stats for the last run
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from api.deps import get_db
from storage.sqlite_store import SQLiteStore

router = APIRouter(prefix="/api/v1")

_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _query_last_run(db: SQLiteStore) -> dict | None:
    """Return the most recent row from run_history, or None."""
    conn = db._connect()
    try:
        row = conn.execute(
            """
            SELECT run_date, status, created_at, universe_size,
                   a_plus_count, a_count
            FROM   run_history
            ORDER  BY run_date DESC, created_at DESC
            LIMIT  1
            """
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _query_first_run_ts(db: SQLiteStore) -> str | None:
    """Return the earliest created_at from run_history, or None."""
    conn = db._connect()
    try:
        row = conn.execute(
            "SELECT created_at FROM run_history ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        return row["created_at"] if row else None
    finally:
        conn.close()


def _query_watchlist_count(db: SQLiteStore) -> int:
    conn = db._connect()
    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM watchlist").fetchone()
        return int(row["cnt"]) if row else 0
    finally:
        conn.close()


def _uptime_days(first_run_ts: str | None) -> float:
    """Return pipeline uptime in fractional days from first run, or 0.0."""
    if not first_run_ts:
        return 0.0
    try:
        first = datetime.fromisoformat(str(first_run_ts).replace("Z", "+00:00"))
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        delta = datetime.now(tz=timezone.utc) - first
        return round(delta.total_seconds() / 86400, 2)
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/health")
async def health_check(db: SQLiteStore = Depends(get_db)) -> dict:
    """Liveness probe — always returns HTTP 200 with last-run metadata."""
    run = _query_last_run(db)

    last_run: str | None = None
    last_run_status: str | None = None

    if run:
        # run_date is a DATE column; coerce to ISO string
        last_run = str(run["run_date"])
        raw_status = (run.get("status") or "").lower()
        if raw_status == "success":
            last_run_status = "success"
        elif raw_status in ("failed", "error"):
            last_run_status = "failed"
        else:
            last_run_status = raw_status or None

    return {
        "status": "ok",
        "last_run": last_run,
        "last_run_status": last_run_status,
        "version": _VERSION,
    }


@router.get("/meta")
async def get_meta(db: SQLiteStore = Depends(get_db)) -> dict:
    """Universe and watchlist statistics for the most recent run."""
    run = _query_last_run(db)
    watchlist_size = _query_watchlist_count(db)
    first_ts = _query_first_run_ts(db)

    universe_size: int = 0
    last_screen_date: str = ""
    a_plus_count: int = 0
    a_count: int = 0

    if run:
        universe_size = int(run.get("universe_size") or 0)
        last_screen_date = str(run.get("run_date") or "")
        a_plus_count = int(run.get("a_plus_count") or 0)
        a_count = int(run.get("a_count") or 0)

    return {
        "universe_size": universe_size,
        "watchlist_size": watchlist_size,
        "last_screen_date": last_screen_date,
        "a_plus_count": a_plus_count,
        "a_count": a_count,
        "pipeline_uptime_days": _uptime_days(first_ts),
    }
