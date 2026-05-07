"""
api/routers/stocks.py
---------------------
Stock screening result endpoints for the SEPA AI API.

All routes are protected by require_read_key (auth is auto-disabled in dev
when API_READ_KEY / API_ADMIN_KEY env vars are absent).

Routes:
  GET /api/v1/stocks/top            — top-ranked SEPA candidates
  GET /api/v1/stocks/trend          — all Trend-Template passes
  GET /api/v1/stocks/vcp            — VCP-qualified stocks
  GET /api/v1/stocks/{symbol}       — single symbol result
  GET /api/v1/stocks/{symbol}/history — historical scores
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from api.auth import require_read_key
from api.deps import get_db, get_run_date
from api.schemas.common import APIResponse
from api.schemas.stock import StockHistorySchema, StockResultSchema
from storage.sqlite_store import SQLiteStore

router = APIRouter(
    prefix="/api/v1/stocks",
    dependencies=[Depends(require_read_key)],
)

# Quality ordering for min_quality filtering
_QUALITY_ORDER: dict[str, int] = {
    "A+": 4, "A": 3, "B": 2, "C": 1, "FAIL": 0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_row(row: dict[str, Any]) -> StockResultSchema:
    """Build a StockResultSchema from a screen_results db row.

    The ``result_json`` column stores the full SEPAResult as JSON (written by
    screener/results.py::persist_results).  We load that first, then overlay
    the flat columns which are more authoritative for indexed fields.
    """
    raw: dict[str, Any] = {}
    result_json = row.get("result_json")
    if result_json:
        try:
            raw = json.loads(result_json)
        except (json.JSONDecodeError, TypeError):
            raw = {}

    # Flat columns override JSON for core indexed fields
    for key in (
        "symbol", "run_date", "score", "setup_quality", "stage",
        "trend_template_pass", "vcp_qualified", "breakout_triggered",
        "rs_rating", "entry_price", "stop_loss", "risk_pct",
    ):
        val = row.get(key)
        if val is not None:
            raw[key] = val

    return StockResultSchema.model_validate(raw)


def _resolve_date(date_str: str | None) -> date:
    """Coerce an optional YYYY-MM-DD string to a date (defaults to today)."""
    if not date_str:
        return date.today()
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid date: '{date_str}'. Expected YYYY-MM-DD.",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/top", response_model=APIResponse[list[StockResultSchema]])
async def get_top_stocks(
    quality: str | None = None,
    limit: int = 20,
    date: str | None = None,
    db: SQLiteStore = Depends(get_db),
    run_date: date = Depends(get_run_date),
) -> APIResponse[list[StockResultSchema]]:
    """Top-ranked SEPA candidates sorted by score DESC.

    Optional ``quality`` filter accepts A+, A, B, or C.
    Optional ``date`` overrides the default (today).
    """
    effective_date = _resolve_date(date) if date else run_date
    rows = db.get_results(effective_date)

    if quality:
        rows = [r for r in rows if r.get("setup_quality") == quality]

    rows = rows[:limit]
    data = [_parse_row(r) for r in rows]
    return APIResponse(success=True, data=data, meta={"count": len(data)})


@router.get("/trend", response_model=APIResponse[list[StockResultSchema]])
async def get_trend_stocks(
    min_rs: int = 0,
    stage: int | None = None,
    limit: int = 50,
    date: str | None = None,
    db: SQLiteStore = Depends(get_db),
) -> APIResponse[list[StockResultSchema]]:
    """All stocks that passed the Trend Template on a given date."""
    effective_date = _resolve_date(date)
    rows = db.get_results(effective_date)

    filtered = [
        r for r in rows
        if r.get("trend_template_pass") in (1, True)
        and int(r.get("rs_rating") or 0) >= min_rs
        and (stage is None or r.get("stage") == stage)
    ]

    data = [_parse_row(r) for r in filtered[:limit]]
    return APIResponse(success=True, data=data, meta={"count": len(data)})


@router.get("/vcp", response_model=APIResponse[list[StockResultSchema]])
async def get_vcp_stocks(
    min_quality: str = "B",
    limit: int = 30,
    date: str | None = None,
    db: SQLiteStore = Depends(get_db),
) -> APIResponse[list[StockResultSchema]]:
    """Stocks with a qualified VCP pattern on a given date."""
    effective_date = _resolve_date(date)
    rows = db.get_results(effective_date)
    min_rank = _QUALITY_ORDER.get(min_quality, 0)

    filtered = [
        r for r in rows
        if r.get("vcp_qualified") in (1, True)
        and _QUALITY_ORDER.get(r.get("setup_quality", "FAIL"), 0) >= min_rank
    ]

    data = [_parse_row(r) for r in filtered[:limit]]
    return APIResponse(success=True, data=data, meta={"count": len(data)})


@router.get("/{symbol}", response_model=APIResponse[StockResultSchema])
async def get_stock(
    symbol: str,
    date: str | None = None,
    db: SQLiteStore = Depends(get_db),
) -> APIResponse[StockResultSchema]:
    """Full SEPAResult for a single symbol. Returns 404 if not found."""
    effective_date = _resolve_date(date)
    row = db.get_result(symbol.upper(), effective_date)

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No result found for symbol '{symbol.upper()}' on {effective_date}.",
        )

    return APIResponse(success=True, data=_parse_row(row))


@router.get("/{symbol}/history", response_model=APIResponse[StockHistorySchema])
async def get_stock_history(
    symbol: str,
    days: int = 30,
    db: SQLiteStore = Depends(get_db),
) -> APIResponse[StockHistorySchema]:
    """Historical SEPA scores for a symbol over the last N trading days."""
    conn = db._connect()
    try:
        rows = conn.execute(
            """
            SELECT run_date, score, setup_quality AS quality, stage
            FROM   screen_results
            WHERE  symbol = ?
            ORDER  BY run_date DESC
            LIMIT  ?
            """,
            (symbol.upper(), days),
        ).fetchall()
    finally:
        conn.close()

    history = [dict(r) for r in rows]
    # Return chronological order (oldest → newest)
    history.reverse()

    schema = StockHistorySchema(symbol=symbol.upper(), history=history)
    return APIResponse(success=True, data=schema)
