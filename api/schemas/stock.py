"""
api/schemas/stock.py
--------------------
Pydantic schemas for SEPA screening results.

  TrendTemplateSchema  — 8-condition Minervini Trend Template verdict
  VCPSchema            — Volatility Contraction Pattern metrics
  StockResultSchema    — full per-symbol screening output (maps 1-to-1
                         with SEPAResult after dataclasses.asdict)
  StockHistorySchema   — historical score/quality series for a symbol

Mapping notes
-------------
SEPAResult.run_date is a datetime.date object; StockResultSchema.run_date
accepts both date objects and ISO-8601 strings via a field validator so
dataclasses.asdict() output round-trips without manual conversion.

SEPAResult.trend_template_details stores the numeric TrendTemplateResult
.details dict (close, sma_150 …), whereas TrendTemplateSchema carries
the boolean conditions.  When constructing StockResultSchema from a raw
SEPAResult the caller should populate trend_template_details from the
original TrendTemplateResult, not from the stored numeric details dict.
Similarly vcp_details should come from VCPMetrics, not from qualify_vcp's
bool details.  Both fields default to None so omitting them is safe.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Sub-schemas
# ---------------------------------------------------------------------------


class TrendTemplateSchema(BaseModel):
    """Boolean verdict for each of Minervini's 8 Trend Template conditions."""

    passes: bool
    conditions_met: int
    condition_1: bool   # price > SMA_150 AND price > SMA_200
    condition_2: bool   # SMA_150 > SMA_200
    condition_3: bool   # SMA_200 slope > 0
    condition_4: bool   # SMA_50 > SMA_150 AND SMA_50 > SMA_200
    condition_5: bool   # price > SMA_50
    condition_6: bool   # price >= N% above 52-week low
    condition_7: bool   # price within N% of 52-week high
    condition_8: bool   # RS Rating >= threshold


class VCPSchema(BaseModel):
    """Key VCP metrics surfaced to API consumers."""

    qualified: bool
    contraction_count: int | None = None
    max_depth_pct: float | None = None
    final_depth_pct: float | None = None
    vol_contraction_ratio: float | None = None
    base_length_weeks: int | None = None
    tightness_score: float | None = None


# ---------------------------------------------------------------------------
# Primary result schema
# ---------------------------------------------------------------------------


class StockResultSchema(BaseModel):
    """Full screening result for a single symbol on a single run date.

    Maps 1-to-1 with SEPAResult (rules/scorer.py) after dataclasses.asdict().
    Extra API-layer fields (is_watchlist, llm_brief) default to safe values.
    """

    symbol: str
    run_date: str           # ISO-8601 date string; validator coerces date → str
    score: int
    setup_quality: Literal["A+", "A", "B", "C", "FAIL"]
    stage: int
    stage_label: str
    stage_confidence: int
    trend_template_pass: bool
    conditions_met: int
    vcp_qualified: bool
    breakout_triggered: bool
    entry_price: float | None = None
    stop_loss: float | None = None
    risk_pct: float | None = None
    target_price: float | None = None
    reward_risk_ratio: float | None = None
    rs_rating: int
    news_score: float | None = None
    fundamental_pass: bool = False
    # API-layer extras — not present on SEPAResult; default to safe values
    is_watchlist: bool = False
    trend_template_details: TrendTemplateSchema | None = None
    vcp_details: VCPSchema | None = None
    llm_brief: str | None = None    # Phase 6 optional

    @field_validator("run_date", mode="before")
    @classmethod
    def coerce_date_to_str(cls, v: object) -> str:
        """Accept datetime.date objects as well as ISO-8601 strings."""
        if hasattr(v, "isoformat"):
            return v.isoformat()  # type: ignore[union-attr]
        return str(v)

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# History schema
# ---------------------------------------------------------------------------


class StockHistorySchema(BaseModel):
    """Historical score / quality time-series for a single symbol."""

    symbol: str
    history: list[dict]     # [{run_date, score, quality, stage}, …]
