"""
rules/scorer.py
---------------
SEPAResult dataclass and composite scorer for the Minervini SEPA screening system.

This is the final aggregation layer — it consumes outputs from every other
rule module and produces a single SEPAResult with a 0–100 score and a
setup quality grade (A+, A, B, C, FAIL).

Architecture notes:
  - score_symbol() is the sole public entry point.
  - Stage 2 is a hard gate: score == 0 for any non-Stage-2 stock.
  - All component scores are normalised to 0–100 before weighting.
  - SCORE_WEIGHTS must sum to exactly 1.0 (asserted at module load time).
  - fundamental_pass and news_score fields default to False/None for Phase 3;
    they are wired in Phase 5.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

import pandas as pd

from features.sector_rs import get_sector_score_bonus
from features.vcp import VCPMetrics
from rules.fundamental_template import FundamentalResult, check_fundamental_template
from rules.stage import StageResult
from rules.trend_template import TrendTemplateResult
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Score weights — must sum to exactly 1.0 (asserted at module load time).
# ---------------------------------------------------------------------------

SCORE_WEIGHTS: dict[str, float] = {
    "rs_rating":   0.30,
    "trend":       0.25,
    "vcp":         0.22,
    "volume":      0.10,
    "fundamental": 0.07,
    "news":        0.06,
}

assert abs(sum(SCORE_WEIGHTS.values()) - 1.0) < 1e-9, (
    f"SCORE_WEIGHTS must sum to 1.0, got {sum(SCORE_WEIGHTS.values()):.10f}"
)


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class SEPAResult:
    """Fully assembled screening result for a single symbol on a single run date."""

    symbol: str
    run_date: date
    stage: int
    stage_label: str
    stage_confidence: int
    trend_template_pass: bool
    trend_template_details: dict[str, Any]
    conditions_met: int                         # 0–8 trend template conditions
    fundamental_pass: bool = False              # Phase 5 — default False
    fundamental_details: dict[str, Any] = field(default_factory=dict)
    vcp_qualified: bool = False
    vcp_details: dict[str, Any] = field(default_factory=dict)
    breakout_triggered: bool = False
    entry_price: float | None = None
    stop_loss: float | None = None
    risk_pct: float | None = None
    target_price: float | None = None
    reward_risk_ratio: float | None = None
    rs_rating: int = 0
    sector_bonus: int = 0                       # 0 or 5
    news_score: float | None = None             # Phase 5 — default None
    setup_quality: Literal["A+", "A", "B", "C", "FAIL"] = "FAIL"
    score: int = 0                              # 0–100


# ---------------------------------------------------------------------------
# Internal component scorers — each returns a float in 0–100.
# ---------------------------------------------------------------------------

def _compute_vcp_score(vcp_metrics: VCPMetrics) -> float:
    """Return a 0–100 score reflecting VCP pattern quality.

    Qualified VCP:
      base 60 + contraction quality bonus (0–40).
      Bonus = (3 − |contractions − 3|) × 10   (ideal = 3 contractions)
            + 20 if vol_contraction_ratio < 0.5  (strong drying-up)
            + 10 if vol_contraction_ratio < 0.8  (moderate drying-up)
      Bonus is capped at 40.

    Unqualified VCP:
      max(0, contraction_count × 15) — partial credit for early-stage basing.
    """
    if vcp_metrics.is_valid_vcp:
        contraction_bonus = (3 - abs(vcp_metrics.contraction_count - 3)) * 10
        vol_ratio = vcp_metrics.vol_contraction_ratio
        vol_bonus = 0
        if not math.isnan(vol_ratio):
            if vol_ratio < 0.5:
                vol_bonus = 20
            elif vol_ratio < 0.8:
                vol_bonus = 10
        bonus = min(40, contraction_bonus + vol_bonus)
        return 60.0 + bonus
    else:
        return max(0.0, float(vcp_metrics.contraction_count) * 15.0)


def _compute_volume_score(row: pd.Series, breakout_triggered: bool) -> float:
    """Return a 0–100 score for volume quality.

    On a breakout day: vol_ratio / 3.0 × 100 (3× avg volume = perfect score).
    Otherwise: accumulation/distribution proxy from the acc_dist_score column.
    """
    if breakout_triggered:
        vol_ratio = float(row.get("vol_ratio", 1.0))
        return min(100.0, vol_ratio / 3.0 * 100.0)
    else:
        acc_dist = float(row.get("acc_dist_score", 0))
        return min(100.0, max(0.0, (acc_dist + 20.0) * 2.5))


# ---------------------------------------------------------------------------
# Setup quality gate
# ---------------------------------------------------------------------------

def _determine_quality(
    score: int,
    stage: int,
    conditions_met: int,
    vcp_qualified: bool,
) -> Literal["A+", "A", "B", "C", "FAIL"]:
    """Classify setup quality from score and individual gate conditions.

    Grade hierarchy (first match wins):
      A+  → score ≥ 85  AND  stage==2  AND  conditions_met==8  AND  vcp_qualified
      A   → score ≥ 70  AND  stage==2  AND  conditions_met==8
      B   → score ≥ 55  AND  stage==2  AND  conditions_met ≥ 6
      C   → score ≥ 40  AND  stage==2
      FAIL → everything else (non-Stage-2, score<40, or <6 TT conditions)
    """
    if stage != 2:
        return "FAIL"
    if score >= 85 and conditions_met == 8 and vcp_qualified:
        return "A+"
    if score >= 70 and conditions_met == 8:
        return "A"
    if score >= 55 and conditions_met >= 6:
        return "B"
    if score >= 40:
        return "C"
    return "FAIL"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_symbol(
    symbol: str,
    run_date: date,
    row: pd.Series,
    stage_result: StageResult,
    tt_result: TrendTemplateResult,
    vcp_metrics: VCPMetrics,
    sector_ranks: dict[str, int],
    symbol_info: pd.DataFrame,
    config: dict,
    fundamental_result: dict | None = None,   # Phase 5 — pass None for now
    news_score: float | None = None,          # Phase 5 — pass None for now
) -> SEPAResult:
    """Assemble all rule outputs into a final SEPAResult.

    Score calculation per component (each normalised 0–100 before weighting):

      rs_rating_score   = row["rs_rating"]                    (already 0–99; treat as 0–100)
      trend_score       = conditions_met / 8 * 100
      vcp_score         = _compute_vcp_score(vcp_metrics)
      volume_score      = _compute_volume_score(row, breakout_triggered)
      fundamental_score = fundamental_result["score"] if provided else 50  (neutral)
      news_score_norm   = (news_score + 100) / 2 if provided else 50

    weighted_score = Σ(component × weight)
    sector_bonus   = get_sector_score_bonus(symbol, sector_ranks, symbol_info)
    final_score    = int(min(100, weighted_score + sector_bonus))

    Hard gate: if stage_result.stage != 2 → final_score = 0.

    Parameters
    ----------
    symbol:
        Ticker symbol being scored.
    run_date:
        Date of the screening run.
    row:
        Latest feature row (pd.Series) for the symbol.
        Expected columns: close, rs_rating, vol_ratio, pivot_high,
        acc_dist_score, atr_14 (missing columns degrade gracefully to defaults).
    stage_result:
        Output of rules.stage.detect_stage().
    tt_result:
        Output of rules.trend_template.check_trend_template().
    vcp_metrics:
        Output of features.vcp VCPDetector.detect().
    sector_ranks:
        Output of features.sector_rs.compute_sector_ranks().
    symbol_info:
        DataFrame with at least ``symbol`` and ``sector`` columns.
    config:
        Project config dict.
    fundamental_result:
        Phase 5 placeholder — pass None; treated as neutral (score=50).
    news_score:
        Phase 5 placeholder — pass None; treated as neutral (50).

    Returns
    -------
    SEPAResult
        Fully populated dataclass.  score==0 and quality=="FAIL" when
        stage_result.stage != 2 (hard gate).
    """
    # ------------------------------------------------------------------
    # VCP qualification (imported here to keep module-level imports lean)
    # ------------------------------------------------------------------
    from rules.vcp_rules import qualify_vcp
    vcp_qualified, vcp_details = qualify_vcp(vcp_metrics, config)

    # ------------------------------------------------------------------
    # Entry trigger
    # ------------------------------------------------------------------
    from rules.entry_trigger import check_entry_trigger
    entry_trigger = check_entry_trigger(row, config)
    breakout_triggered: bool = entry_trigger.triggered
    entry_price: float | None = entry_trigger.entry_price

    # ------------------------------------------------------------------
    # Stop loss + risk %
    # ------------------------------------------------------------------
    from rules.stop_loss import compute_stop_loss
    base_low = vcp_metrics.base_low
    vcp_base_low: float | None = None if math.isnan(base_low) else base_low
    stop_loss, risk_pct, _sl_method = compute_stop_loss(row, vcp_base_low, config)

    # ------------------------------------------------------------------
    # Risk / reward — only computed when a valid entry/stop pair exists
    # ------------------------------------------------------------------
    target_price: float | None = None
    reward_risk_ratio: float | None = None
    if entry_price is not None and stop_loss is not None and entry_price > stop_loss:
        from rules.risk_reward import compute_risk_reward
        _target, _risk_amt, _rr = compute_risk_reward(entry_price, stop_loss, config)
        if _target != 0.0:
            target_price = _target
            reward_risk_ratio = _rr

    # ------------------------------------------------------------------
    # RS rating (integer, clipped to 0–100 for scoring)
    # ------------------------------------------------------------------
    rs_raw = row.get("rs_rating", 0)
    rs_rating: int = int(float(rs_raw)) if rs_raw is not None else 0

    # ------------------------------------------------------------------
    # Component scores, each normalised 0–100
    # ------------------------------------------------------------------
    rs_rating_score: float  = float(min(100, max(0, rs_rating)))
    trend_score: float      = tt_result.conditions_met / 8.0 * 100.0
    vcp_score: float        = _compute_vcp_score(vcp_metrics)
    volume_score: float     = _compute_volume_score(row, breakout_triggered)

    # Phase 5: call check_fundamental_template when fundamentals enabled + provided
    _fund_result: FundamentalResult | None = None
    if config.get("fundamentals", {}).get("enabled", True) and fundamental_result is not None:
        _fund_result = check_fundamental_template(fundamental_result, config)

    if _fund_result is not None:
        fundamental_score: float = float(_fund_result.score)
    else:
        fundamental_score = 50.0  # neutral when absent or disabled

    # News: raw range −100 to +100 → normalise to 0–100; neutral 50 if absent
    if news_score is not None:
        news_score_norm: float = (float(news_score) + 100.0) / 2.0
    else:
        news_score_norm = 50.0

    # ------------------------------------------------------------------
    # Weighted composite
    # ------------------------------------------------------------------
    weighted_score: float = (
        rs_rating_score     * SCORE_WEIGHTS["rs_rating"]
        + trend_score       * SCORE_WEIGHTS["trend"]
        + vcp_score         * SCORE_WEIGHTS["vcp"]
        + volume_score      * SCORE_WEIGHTS["volume"]
        + fundamental_score * SCORE_WEIGHTS["fundamental"]
        + news_score_norm   * SCORE_WEIGHTS["news"]
    )

    sector_bonus: int = get_sector_score_bonus(symbol, sector_ranks, symbol_info)
    final_score_float = min(100.0, weighted_score + sector_bonus)

    # ------------------------------------------------------------------
    # Hard gate: Stage 2 only
    # ------------------------------------------------------------------
    if stage_result.stage != 2:
        final_score_float = 0.0

    final_score: int = int(final_score_float)

    # ------------------------------------------------------------------
    # Setup quality
    # ------------------------------------------------------------------
    setup_quality = _determine_quality(
        final_score,
        stage_result.stage,
        tt_result.conditions_met,
        vcp_qualified,
    )

    # Phase 5: hard gate — fundamentals must pass when hard_gate=True
    if (
        config.get("fundamentals", {}).get("hard_gate", False)
        and _fund_result is not None
        and not _fund_result.passes
    ):
        setup_quality = "FAIL"

    log.debug(
        "score_symbol: %s stage=%d score=%d quality=%s "
        "rs=%.1f trend=%.1f vcp=%.1f vol=%.1f fund=%.1f news=%.1f bonus=%d",
        symbol, stage_result.stage, final_score, setup_quality,
        rs_rating_score, trend_score, vcp_score, volume_score,
        fundamental_score, news_score_norm, sector_bonus,
    )

    # ------------------------------------------------------------------
    # Assemble and return
    # ------------------------------------------------------------------
    # Phase 5: derive fundamental_pass / fundamental_details from FundamentalResult
    fundamental_pass = _fund_result.passes if _fund_result is not None else False
    fundamental_details = dict(vars(_fund_result)) if _fund_result is not None else {}

    return SEPAResult(
        symbol=symbol,
        run_date=run_date,
        stage=stage_result.stage,
        stage_label=stage_result.label,
        stage_confidence=stage_result.confidence,
        trend_template_pass=tt_result.passes,
        trend_template_details=tt_result.details,
        conditions_met=tt_result.conditions_met,
        fundamental_pass=fundamental_pass,
        fundamental_details=fundamental_details,
        vcp_qualified=vcp_qualified,
        vcp_details=vcp_details,
        breakout_triggered=breakout_triggered,
        entry_price=entry_price,
        stop_loss=stop_loss,
        risk_pct=risk_pct,
        target_price=target_price,
        reward_risk_ratio=reward_risk_ratio,
        rs_rating=rs_rating,
        sector_bonus=sector_bonus,
        news_score=news_score,
        setup_quality=setup_quality,
        score=final_score,
    )
