"""
api/routers/watchlist.py
------------------------
Watchlist management and pipeline-trigger endpoints.

Route ordering note
-------------------
FastAPI matches routes in declaration order, so static paths (/bulk, /upload,
/run) MUST be declared BEFORE the dynamic /{symbol} path to prevent
those literal strings from being captured as symbol names.

Routes:
  GET    /api/v1/watchlist          — list all watchlist symbols with SEPA scores
  POST   /api/v1/watchlist/bulk     — bulk-add symbols
  POST   /api/v1/watchlist/upload   — upload .csv/.json/.xlsx/.txt file
  POST   /api/v1/watchlist/run      — trigger pipeline run in background
  DELETE /api/v1/watchlist          — clear entire watchlist
  POST   /api/v1/watchlist/{symbol} — add single symbol
  DELETE /api/v1/watchlist/{symbol} — remove single symbol
"""

from __future__ import annotations

import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, status

from api.auth import require_admin_key, require_read_key
from api.deps import get_config, get_db
from api.schemas.common import APIResponse
from ingestion.universe_loader import load_watchlist_file, validate_symbol
from storage.sqlite_store import SQLiteStore
from utils.exceptions import WatchlistParseError

router = APIRouter(prefix="/api/v1/watchlist")

_MAX_UPLOAD_BYTES = 1 * 1024 * 1024  # 1 MB
_SUPPORTED_UPLOAD_SUFFIXES = {".csv", ".json", ".xlsx", ".xls", ".txt"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_enriched_watchlist(
    db: SQLiteStore, sort: str = "score", limit: int = 100
) -> list[dict[str, Any]]:
    """Return watchlist rows sorted by last_score or symbol name."""
    rows = db.get_watchlist()
    if sort == "score":
        rows.sort(key=lambda r: (r.get("last_score") or 0), reverse=True)
    elif sort == "symbol":
        rows.sort(key=lambda r: r.get("symbol", ""))
    # else: keep DB order (added_at DESC)
    return rows[:limit]


# ---------------------------------------------------------------------------
# GET /api/v1/watchlist
# ---------------------------------------------------------------------------

@router.get("", dependencies=[Depends(require_read_key)])
async def get_watchlist(
    sort: str = "score",
    limit: int = 100,
    db: SQLiteStore = Depends(get_db),
) -> APIResponse[list[dict]]:
    """Returns all watchlist symbols with latest SEPA scores (last_score)."""
    rows = _get_enriched_watchlist(db, sort=sort, limit=limit)
    return APIResponse(success=True, data=rows, meta={"count": len(rows)})


# ---------------------------------------------------------------------------
# POST /api/v1/watchlist/bulk  (static path — must precede /{symbol})
# ---------------------------------------------------------------------------

@router.post("/bulk", dependencies=[Depends(require_admin_key)])
async def add_bulk(
    body: dict = Body(...),
    db: SQLiteStore = Depends(get_db),
) -> APIResponse[dict]:
    """Bulk-add symbols.  Body: {"symbols": ["RELIANCE", "TCS"]}"""
    symbols: list = body.get("symbols", [])
    if not isinstance(symbols, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'symbols' must be a list of strings.",
        )

    existing = {r["symbol"] for r in db.get_watchlist()}
    added = 0
    already_exists = 0
    invalid: list[str] = []
    valid_new: list[str] = []

    for raw in symbols:
        candidate = str(raw).strip().upper()
        if not validate_symbol(candidate):
            invalid.append(str(raw))
        elif candidate in existing:
            already_exists += 1
        else:
            valid_new.append(candidate)
            added += 1

    if valid_new:
        db.bulk_add(valid_new, added_via="api")

    watchlist = _get_enriched_watchlist(db)
    return APIResponse(
        success=True,
        data={
            "added": added,
            "already_exists": already_exists,
            "invalid": invalid,
            "watchlist": watchlist,
        },
    )


# ---------------------------------------------------------------------------
# POST /api/v1/watchlist/upload  (static path — must precede /{symbol})
# ---------------------------------------------------------------------------

@router.post("/upload", dependencies=[Depends(require_admin_key)])
async def upload_watchlist(
    file: UploadFile,
    db: SQLiteStore = Depends(get_db),
) -> APIResponse[dict]:
    """Upload a watchlist file (.csv / .json / .xlsx / .txt).

    Max size: 1 MB.  Returns 400 on oversized or unparseable files.
    """
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File too large. Maximum allowed size is 1 MB.",
        )

    suffix = Path(file.filename or "upload.csv").suffix.lower()
    if suffix not in _SUPPORTED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file format '{suffix}'. Use .csv, .json, .xlsx, or .txt.",
        )

    # Persist to a temp file so load_watchlist_file() can seek/read normally
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        parsed_symbols = load_watchlist_file(tmp_path)
    except WatchlistParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    existing = {r["symbol"] for r in db.get_watchlist()}
    added = 0
    skipped = 0
    invalid: list[str] = []
    valid_new: list[str] = []

    for sym in parsed_symbols:
        candidate = sym.strip().upper()
        if not validate_symbol(candidate):
            invalid.append(sym)
        elif candidate in existing:
            skipped += 1
        else:
            valid_new.append(candidate)
            added += 1

    if valid_new:
        db.bulk_add(valid_new, added_via="upload")

    watchlist = _get_enriched_watchlist(db)
    return APIResponse(
        success=True,
        data={
            "added": added,
            "skipped": skipped,
            "invalid": invalid,
            "watchlist": watchlist,
        },
    )


# ---------------------------------------------------------------------------
# POST /api/v1/watchlist/run  (static path — must precede /{symbol})
# ---------------------------------------------------------------------------

@router.post("/run", dependencies=[Depends(require_admin_key)])
async def trigger_run(
    body: dict | None = Body(default=None),
    config: dict = Depends(get_config),
) -> APIResponse[dict]:
    """Trigger pipeline/runner.run_daily() in a background thread.

    Body (all optional):
        {"scope": "all" | "watchlist" | "universe"}
        {"symbols": ["RELIANCE", "TCS"]}

    Returns immediately with {"status": "started", "run_id": <uuid>}.
    """
    from datetime import date as _date

    payload = body or {}
    scope: str = payload.get("scope", "all")
    symbols_override: list[str] | None = payload.get("symbols")
    run_id = str(uuid.uuid4())

    def _run_pipeline() -> None:
        try:
            from pipeline import runner
            from pipeline.context import RunContext

            ctx = RunContext(
                run_date=_date.today(),
                mode="daily",
                config=config,
                scope=scope,
                symbols_override=symbols_override,
            )
            runner.run_daily(ctx)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger("api").error(
                "trigger_run %s failed: %s", run_id, exc, exc_info=True
            )

    thread = threading.Thread(
        target=_run_pipeline,
        daemon=True,
        name=f"pipeline-run-{run_id}",
    )
    thread.start()

    return APIResponse(
        success=True,
        data={"status": "started", "run_id": run_id},
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/watchlist  (clear all — static path before /{symbol})
# ---------------------------------------------------------------------------

@router.delete("", dependencies=[Depends(require_admin_key)])
async def clear_watchlist(db: SQLiteStore = Depends(get_db)) -> APIResponse[dict]:
    """Remove every symbol from the watchlist. Returns the count removed."""
    rows = db.get_watchlist()
    count = len(rows)
    db.clear_watchlist()
    return APIResponse(success=True, data={"removed": count})


# ---------------------------------------------------------------------------
# POST /api/v1/watchlist/{symbol}  (dynamic path — must come LAST among POSTs)
# ---------------------------------------------------------------------------

@router.post("/{symbol}", dependencies=[Depends(require_admin_key)])
async def add_to_watchlist(
    symbol: str,
    note: str | None = None,
    db: SQLiteStore = Depends(get_db),
) -> APIResponse[dict]:
    """Add a single symbol to the watchlist.

    Returns HTTP 400 if the symbol is not a valid NSE ticker.
    Returns HTTP 200 (with already_exists=True) if already present.
    """
    candidate = symbol.strip().upper()
    if not validate_symbol(candidate):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid symbol '{symbol}'. Expected 1-20 uppercase alphanumeric characters.",
        )

    existing = {r["symbol"] for r in db.get_watchlist()}
    already_exists = candidate in existing

    db.add_symbol(candidate, note=note, added_via="api")

    return APIResponse(
        success=True,
        data={"symbol": candidate, "already_exists": already_exists},
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/watchlist/{symbol}  (dynamic path — last DELETE route)
# ---------------------------------------------------------------------------

@router.delete("/{symbol}", dependencies=[Depends(require_admin_key)])
async def remove_from_watchlist(
    symbol: str,
    db: SQLiteStore = Depends(get_db),
) -> APIResponse[dict]:
    """Remove a symbol from the watchlist.  Returns 404 if not present."""
    candidate = symbol.strip().upper()
    existing = {r["symbol"] for r in db.get_watchlist()}
    if candidate not in existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Symbol '{candidate}' is not in the watchlist.",
        )

    db.remove_symbol(candidate)
    return APIResponse(success=True, data={"symbol": candidate, "removed": True})
