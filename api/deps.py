"""
api/deps.py
-----------
FastAPI dependency-injection helpers for the SEPA AI API.

Provides:
  - ``get_db``       — singleton :class:`~storage.sqlite_store.SQLiteStore`
  - ``get_config``   — cached app-config dict read from ``config/settings.yaml``
  - ``get_run_date`` — query-parameter helper that coerces a date string (or
                       ``None``) into a :class:`datetime.date` object
"""

from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import yaml
from fastapi import HTTPException, Query, status

from storage.sqlite_store import SQLiteStore

# ---------------------------------------------------------------------------
# Paths resolved relative to the project root (two levels above this file)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SETTINGS_PATH = _PROJECT_ROOT / "config" / "settings.yaml"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_db() -> SQLiteStore:
    """Return a singleton :class:`~storage.sqlite_store.SQLiteStore`.

    The database path is read from ``watchlist.persist_path`` in
    ``config/settings.yaml``.  The instance is constructed once and then
    returned from the LRU cache on every subsequent call, making it
    effectively thread-safe for read operations (SQLite WAL mode is enabled
    by the store itself).
    """
    cfg = get_config()
    db_path_str: str = cfg.get("watchlist", {}).get("persist_path", "data/sepa_ai.db")
    db_path = _PROJECT_ROOT / db_path_str
    return SQLiteStore(db_path)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_config() -> dict:
    """Return the parsed ``config/settings.yaml`` as a plain dict.

    The result is cached via :func:`functools.lru_cache` so the file is
    read only once per process lifetime.  Call ``get_config.cache_clear()``
    in tests to force a re-read.
    """
    with open(_SETTINGS_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# Date helper
# ---------------------------------------------------------------------------


def get_run_date(
    date_str: str | None = Query(
        default=None,
        alias="date",
        description="Run date in YYYY-MM-DD format. Defaults to today.",
        examples=["2024-01-15"],
    ),
) -> date:
    """FastAPI dependency: resolve an optional ``?date=YYYY-MM-DD`` query param.

    * Returns :func:`datetime.date.today` when *date_str* is ``None`` or
      an empty string.
    * Raises ``HTTP 422 Unprocessable Entity`` when the string is present
      but cannot be parsed as ``YYYY-MM-DD``.
    """
    if not date_str:
        return date.today()

    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid date format: '{date_str}'. "
                "Expected YYYY-MM-DD (e.g. 2024-01-15)."
            ),
        )
