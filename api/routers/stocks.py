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
from api.deps import get_config, get_db, get_run_date
from api.schemas.common import APIResponse
from api.schemas.stock import StockHistorySchema, StockResultSchema, OHLCVResponseSchema
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

    The ``result_json`` column stores either:
      (a) the full SEPAResult dict as JSON  (correct — written by fixed code), or
      (b) a shallow row_dict whose own ``result_json`` key holds the full JSON
          as a nested string  (legacy — written by the double-encode bug).

    We handle both cases so that already-stored rows still deserialise cleanly.
    """
    raw: dict[str, Any] = {}
    result_json = row.get("result_json")
    if result_json:
        try:
            raw = json.loads(result_json)
        except (json.JSONDecodeError, TypeError):
            raw = {}

    # Legacy double-encode: raw itself has a "result_json" string key that
    # contains the actual full SEPAResult JSON — parse and merge it in.
    nested_json = raw.get("result_json")
    if isinstance(nested_json, str):
        try:
            nested = json.loads(nested_json)
            # Merge richer nested fields without overwriting already-present values
            for k, v in nested.items():
                if k != "result_json" and (k not in raw or raw[k] is None):
                    raw[k] = v
        except (json.JSONDecodeError, TypeError):
            pass

    # Flat columns override JSON for core indexed fields
    for key in (
        "symbol", "run_date", "score", "setup_quality", "stage",
        "trend_template_pass", "vcp_qualified", "breakout_triggered",
        "rs_rating", "entry_price", "stop_loss", "risk_pct",
        "llm_brief",
    ):
        val = row.get(key)
        if val is not None:
            raw[key] = val

    # ── Legacy-data guard ──────────────────────────────────────────────
    # Pre-fix rows stored the wrong dicts here; null them so the frontend
    # shows "No detail data" rather than all conditions defaulting to False.
    #
    # trend_template_details was set to tt_result.details (numeric values
    # like close/sma_50) instead of the boolean condition_1…condition_8 dict.
    tt_det = raw.get("trend_template_details")
    if isinstance(tt_det, dict) and "condition_1" not in tt_det:
        raw["trend_template_details"] = None

    # vcp_details was set to qualify_vcp()'s rule-pass dict
    # (contraction_count_min, declining_depth…) instead of VCP metrics.
    vcp_det = raw.get("vcp_details")
    if isinstance(vcp_det, dict) and "qualified" not in vcp_det:
        raw["vcp_details"] = None

    # ── Fundamental score ──────────────────────────────────────────────
    # Extract the real 0–100 score from fundamental_details.score stored in
    # result_json.  When fundamentals were not evaluated the backend used a
    # neutral 50, so we default to 50 here to keep the breakdown consistent.
    if "fundamental_score" not in raw:
        fund_details = raw.get("fundamental_details") or {}
        if isinstance(fund_details, dict) and "score" in fund_details:
            raw["fundamental_score"] = int(fund_details["score"])
        else:
            # No fundamental evaluation → use neutral 50 (mirrors scorer.py)
            raw["fundamental_score"] = 50

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
    When no explicit date is given and today has no results, falls back to
    the most recent date that has screening data.
    """
    effective_date = _resolve_date(date) if date else run_date
    rows = db.get_results(effective_date)

    # Fall back to the latest available run date when today has no data
    if not rows and not date:
        latest = db.get_last_run_date()
        if latest and latest != effective_date:
            rows = db.get_results(latest)

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

    # Fall back to the latest available run date when today has no data
    if not rows and not date:
        latest = db.get_last_run_date()
        if latest and latest != effective_date:
            rows = db.get_results(latest)

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

    # Fall back to the latest available run date when today has no data
    if not rows and not date:
        latest = db.get_last_run_date()
        if latest and latest != effective_date:
            rows = db.get_results(latest)
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


@router.get("/{symbol}/ohlcv", response_model=APIResponse[OHLCVResponseSchema])
async def get_stock_ohlcv(
    symbol: str,
    days: int = 90,
) -> APIResponse[OHLCVResponseSchema]:
    """Last *days* of OHLCV bars + SMA50/150/200 from the feature Parquet.

    Resolution order:
      1. data/features/{symbol}.parquet  — has OHLCV + MA columns
      2. data/processed/{symbol}.parquet — OHLCV only; MA series will be null
    Returns 404 only when neither file exists / both are empty.
    """
    from pathlib import Path
    import pandas as pd
    from storage.parquet_store import read_parquet

    sym = symbol.upper()
    feature_path   = Path("data/features")  / f"{sym}.parquet"
    processed_path = Path("data/processed") / f"{sym}.parquet"

    df = read_parquet(feature_path)
    if df.empty:
        df = read_parquet(processed_path)

    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No OHLCV data found for '{sym}'.",
        )

    df = df.tail(days)

    def _date_str(idx: object) -> str:
        if hasattr(idx, "date"):
            return str(idx.date())  # type: ignore[union-attr]
        return str(idx)

    ohlcv = [
        {
            "time":  _date_str(idx),
            "open":  float(row["open"]),
            "high":  float(row["high"]),
            "low":   float(row["low"]),
            "close": float(row["close"]),
        }
        for idx, row in df.iterrows()
    ]

    def _ma_series(col: str) -> list[dict] | None:
        if col not in df.columns:
            return None
        return [
            {"time": _date_str(idx), "value": float(v)}
            for idx, v in df[col].dropna().items()
        ]

    data = OHLCVResponseSchema(
        symbol=sym,
        ohlcv=ohlcv,
        sma50=_ma_series("sma_50"),
        sma150=_ma_series("sma_150"),
        sma200=_ma_series("sma_200"),
    )
    return APIResponse(success=True, data=data)


@router.post("/{symbol}/brief", response_model=APIResponse[str])
async def generate_stock_brief(
    symbol: str,
    date: str | None = None,
    db: SQLiteStore = Depends(get_db),
    config: dict = Depends(get_config),
) -> APIResponse[str]:
    """Generate (or regenerate) an AI brief for a single symbol on demand.

    Reconstructs the SEPAResult from the stored result_json, loads the last
    5 OHLCV rows from the feature parquet, calls generate_trade_brief(), and
    upserts the result into screen_results.llm_brief.

    Returns 404 when no screening result exists for the symbol/date.
    Returns 503 when no LLM provider is configured.
    Returns 422 when the symbol's quality grade does not qualify for a brief.
    """
    import json
    from datetime import datetime as _dt

    from llm.explainer import generate_trade_brief
    from llm.llm_client import get_llm_client
    from rules.scorer import SEPAResult
    from storage.parquet_store import read_last_n_rows

    sym = symbol.upper()
    effective_date = _resolve_date(date)

    row = db.get_result(sym, effective_date)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No screening result for '{sym}' on {effective_date}.",
        )

    # ── Reconstruct SEPAResult from stored JSON ──────────────────────────
    raw: dict = {}
    result_json_str = row.get("result_json")
    if result_json_str:
        try:
            raw = json.loads(result_json_str)
        except (json.JSONDecodeError, TypeError):
            raw = {}

    # Handle legacy double-encode (nested result_json string)
    nested = raw.get("result_json")
    if isinstance(nested, str):
        try:
            for k, v in json.loads(nested).items():
                if k != "result_json" and k not in raw:
                    raw[k] = v
        except Exception:
            pass

    # Flat DB columns always win over JSON for core indexed fields
    for key in (
        "symbol", "score", "setup_quality", "stage", "rs_rating",
        "entry_price", "stop_loss", "risk_pct",
        "trend_template_pass", "vcp_qualified", "breakout_triggered",
    ):
        val = row.get(key)
        if val is not None:
            raw[key] = val

    run_date_raw = raw.get("run_date", str(effective_date))
    try:
        run_date_obj = _dt.strptime(str(run_date_raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        run_date_obj = effective_date

    sepa_result = SEPAResult(
        symbol=raw.get("symbol", sym),
        run_date=run_date_obj,
        stage=int(raw.get("stage", 1)),
        stage_label=str(raw.get("stage_label", "")),
        stage_confidence=int(raw.get("stage_confidence", 0)),
        trend_template_pass=bool(raw.get("trend_template_pass")),
        trend_template_details=raw.get("trend_template_details") or {},
        conditions_met=int(raw.get("conditions_met", 0)),
        fundamental_pass=bool(raw.get("fundamental_pass")),
        fundamental_details=raw.get("fundamental_details") or {},
        vcp_qualified=bool(raw.get("vcp_qualified")),
        vcp_details=raw.get("vcp_details") or {},
        breakout_triggered=bool(raw.get("breakout_triggered")),
        entry_price=raw.get("entry_price"),
        stop_loss=raw.get("stop_loss"),
        risk_pct=raw.get("risk_pct"),
        target_price=raw.get("target_price"),
        reward_risk_ratio=raw.get("reward_risk_ratio"),
        rs_rating=int(raw.get("rs_rating", 0)),
        sector_bonus=int(raw.get("sector_bonus", 0)),
        news_score=raw.get("news_score"),
        setup_quality=raw.get("setup_quality", "FAIL"),  # type: ignore[arg-type]
        score=int(raw.get("score", 0)),
    )

    # ── Load last 5 OHLCV rows from feature/processed parquet ────────────
    from pathlib import Path
    import pandas as pd

    feature_path   = Path("data/features")  / f"{sym}.parquet"
    processed_path = Path("data/processed") / f"{sym}.parquet"
    ohlcv_tail = read_last_n_rows(feature_path, 5)
    if ohlcv_tail.empty:
        ohlcv_tail = read_last_n_rows(processed_path, 5)

    # ── Acquire LLM client ───────────────────────────────────────────────
    client = get_llm_client(config)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No LLM provider is available. "
                "Set a provider API key (e.g. GROQ_API_KEY) in your .env file "
                "and restart the server."
            ),
        )

    # ── Generate and validate brief ──────────────────────────────────────
    # Check the quality gate explicitly first so we can tell the caller
    # *why* the brief is None: quality gate vs. LLM call failure.
    only_for = config.get("llm", {}).get("only_for_quality", ["A+", "A"])
    if sepa_result.setup_quality not in only_for:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"'{sym}' has quality {sepa_result.setup_quality!r} — "
                f"AI briefs are only produced for: {only_for}."
            ),
        )

    brief = generate_trade_brief(sepa_result, ohlcv_tail, config, client=client)
    if brief is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "LLM call succeeded but returned an unusable response. "
                "Check server logs for details. "
                "If using Ollama, ensure the model is pulled: "
                f"ollama pull {config.get('llm', {}).get('model', 'llama3.2')}"
            ),
        )

    # ── Persist brief into screen_results.llm_brief ──────────────────────
    db.save_result(
        effective_date,
        {
            "symbol":               sym,
            "stage":                row.get("stage"),
            "score":                row.get("score"),
            "setup_quality":        row.get("setup_quality"),
            "trend_template_pass":  row.get("trend_template_pass"),
            "vcp_qualified":        row.get("vcp_qualified"),
            "breakout_triggered":   row.get("breakout_triggered"),
            "rs_rating":            row.get("rs_rating"),
            "entry_price":          row.get("entry_price"),
            "stop_loss":            row.get("stop_loss"),
            "risk_pct":             row.get("risk_pct"),
            "result_json":          row.get("result_json"),
            "llm_brief":            brief,
        },
    )

    return APIResponse(success=True, data=brief)
