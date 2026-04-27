"""
screener/results.py
-------------------
Persistence helpers for SEPAResult objects.

Public API
----------
persist_results(results, db, run_date)  -- upsert list of SEPAResult to SQLite
load_results(db, run_date)              -- load one run's results, sorted by score DESC
get_top_candidates(db, run_date, ...)   -- filtered, top-N view
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date
from typing import Any

from rules.scorer import SEPAResult
from storage.sqlite_store import SQLiteStore
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Quality ordering for filtering
# ---------------------------------------------------------------------------

_QUALITY_ORDER: dict[str, int] = {
    "A+": 4,
    "A":  3,
    "B":  2,
    "C":  1,
    "FAIL": 0,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def persist_results(
    results: list[SEPAResult],
    db: SQLiteStore,
    run_date: date,
) -> None:
    """Write a list of SEPAResult objects to SQLite using upsert semantics.

    Maps each SEPAResult to the columns expected by SQLiteStore.save_result()
    and serialises the full dataclass as JSON in the ``result_json`` column.

    Parameters
    ----------
    results:
        Screening results produced by run_screen().
    db:
        Open SQLiteStore instance (schema must already exist).
    run_date:
        Date of the screening run — used as the partition key.
    """
    for result in results:
        d: dict[str, Any] = dataclasses.asdict(result)
        # Coerce date fields to str so json.dumps doesn't choke
        d["run_date"] = str(result.run_date)
        row_dict = {
            "symbol":               result.symbol,
            "stage":                result.stage,
            "score":                result.score,
            "setup_quality":        result.setup_quality,
            "trend_template_pass":  result.trend_template_pass,
            "vcp_qualified":        result.vcp_qualified,
            "breakout_triggered":   result.breakout_triggered,
            "rs_rating":            result.rs_rating,
            "entry_price":          result.entry_price,
            "stop_loss":            result.stop_loss,
            "risk_pct":             result.risk_pct,
            "result_json":          json.dumps(d, default=str),
        }
        db.save_result(run_date, row_dict)

    log.info("persist_results: wrote %d results for %s", len(results), run_date)


def load_results(
    db: SQLiteStore,
    run_date: date | None = None,
) -> list[dict[str, Any]]:
    """Load screening results from SQLite.

    If *run_date* is None, loads the most recent run by finding the latest
    run_date in the screen_results table.

    Parameters
    ----------
    db:
        Open SQLiteStore instance.
    run_date:
        Specific run date to load.  When None, the most recent run is used.

    Returns
    -------
    list[dict]
        Rows as plain dicts, sorted by score DESC.
    """
    if run_date is None:
        # Find the most recent run_date available in screen_results
        conn = db._connect()
        row = conn.execute(
            "SELECT run_date FROM screen_results ORDER BY run_date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row is None:
            log.debug("load_results: no results in database")
            return []
        run_date = row["run_date"]

    rows = db.get_results(run_date)
    log.debug("load_results: loaded %d rows for %s", len(rows), run_date)
    return rows


def get_top_candidates(
    db: SQLiteStore,
    run_date: date | None = None,
    min_quality: str = "A",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return top-N candidates filtered by setup quality.

    Quality hierarchy: A+ > A > B > C > FAIL.

    Parameters
    ----------
    db:
        Open SQLiteStore instance.
    run_date:
        Run date to query (None → most recent).
    min_quality:
        Minimum quality grade (inclusive).  E.g. ``"A"`` returns A and A+.
    limit:
        Maximum number of results to return.

    Returns
    -------
    list[dict]
        Top candidates sorted by score DESC, filtered by quality gate.
    """
    all_rows = load_results(db, run_date)
    min_rank = _QUALITY_ORDER.get(min_quality, 0)

    filtered = [
        r for r in all_rows
        if _QUALITY_ORDER.get(r.get("setup_quality", "FAIL"), 0) >= min_rank
    ]

    log.debug(
        "get_top_candidates: %d/%d rows pass min_quality=%s",
        len(filtered), len(all_rows), min_quality,
    )
    return filtered[:limit]
