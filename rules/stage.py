"""
rules/stage.py
--------------
Stage 1/2/3/4 detection for the Minervini SEPA screening system.

This module is the **hard gate** that runs FIRST in the rule engine.
A stock not classified as Stage 2 is eliminated immediately, regardless
of whether every other Trend Template condition passes.

Stage reference (from Appendix C of PROJECT_DESIGN.md):
  Stage 1 — Basing / Neglect      : price below both MAs, slopes flat.   Wait.
  Stage 2 — Advancing / Momentum  : correct MA stack, both slopes up.     BUY zone.
  Stage 3 — Topping / Distribution: lost SMA50, still above SMA200.       Tighten.
  Stage 4 — Declining / Markdown   : below both MAs, both slopes falling.  Never buy.

Design constraints (from anti-patterns in PROJECT_DESIGN.md):
  - Operates on a single pd.Series row — no DataFrame loading.
  - No imports from screener/, pipeline/, api/, or dashboard/.
  - Stage 2 requires ALL 5 conditions — no partial / short-circuit promotion.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Required columns — KeyError raised with a clear message if any are absent
# ---------------------------------------------------------------------------
_REQUIRED_COLUMNS = ("close", "sma_50", "sma_200", "ma_slope_50", "ma_slope_200")


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class StageResult:
    stage: int          # 1 | 2 | 3 | 4
    label: str          # e.g. "Stage 2 — Advancing"
    confidence: int     # 0–100
    reason: str         # human-readable explanation of the classification
    ma_slope_200: float # computed value (positive = trending up)
    ma_slope_50: float
    is_buyable: bool    # True only when stage == 2


# ---------------------------------------------------------------------------
# Stage labels
# ---------------------------------------------------------------------------

_STAGE_LABELS: dict[int, str] = {
    1: "Stage 1 — Basing",
    2: "Stage 2 — Advancing",
    3: "Stage 3 — Topping",
    4: "Stage 4 — Declining",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_required_columns(row: pd.Series) -> None:
    """Raise KeyError with a descriptive message if any required column is missing."""
    missing = [col for col in _REQUIRED_COLUMNS if col not in row.index]
    if missing:
        raise KeyError(
            f"detect_stage() received a row missing required column(s): {missing}. "
            f"Expected all of: {list(_REQUIRED_COLUMNS)}. "
            f"Received index: {list(row.index)}"
        )


def _stage2_confidence(
    close: float,
    sma_50: float,
    sma_200: float,
    slope_50: float,
    slope_200: float,
    threshold: float,
) -> int:
    """
    Return a confidence score (70–100) for a confirmed Stage 2 classification.

    100  → all five conditions are *clearly* satisfied (both slopes > 2× threshold,
           meaningful price separation above both MAs).
    90   → slopes clearly positive but price is close to one MA.
    70   → slopes barely positive (between threshold and 2× threshold).
    """
    strong_slope_threshold = threshold * 2
    slopes_strong = slope_200 > strong_slope_threshold and slope_50 > strong_slope_threshold
    price_clear_of_50 = close > sma_50 * 1.005
    price_clear_of_200 = close > sma_200 * 1.005

    if slopes_strong and price_clear_of_50 and price_clear_of_200:
        return 100
    if slopes_strong:
        return 90
    return 70


def _stage1_confidence(
    close: float,
    sma_50: float,
    sma_200: float,
    slope_50: float,
    slope_200: float,
    threshold: float,
) -> int:
    """Confidence for Stage 1 — higher when both slopes are near zero."""
    both_flat = abs(slope_200) < threshold and abs(slope_50) < threshold
    below_both = close < sma_50 and close < sma_200
    if both_flat and below_both:
        return 85
    if both_flat or below_both:
        return 70
    return 60


def _stage3_confidence(
    close: float,
    sma_50: float,
    sma_200: float,
    slope_50: float,
    slope_200: float,
) -> int:
    """Confidence for Stage 3 — higher when SMA50 is clearly declining."""
    below_50 = close < sma_50
    above_200 = close > sma_200
    sma50_declining = slope_50 < 0

    if below_50 and above_200 and sma50_declining:
        return 85
    if below_50 and above_200:
        return 70
    return 60


def _stage4_confidence(slope_50: float, slope_200: float) -> int:
    """Confidence for Stage 4 — higher when both slopes are clearly negative."""
    if slope_200 < -0.001 and slope_50 < -0.001:
        return 90
    if slope_200 < 0:
        return 75
    return 60


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_stage(row: pd.Series, config: dict) -> StageResult:
    """
    Classify a stock into Stage 1/2/3/4 using the most recent feature row.

    Parameters
    ----------
    row:
        A pd.Series — one row from the feature Parquet file.
        Required columns: close, sma_50, sma_200, ma_slope_50, ma_slope_200.

    config:
        Project config dict. Relevant keys::

            config["stage"]["flat_slope_threshold"]   default: 0.0005
            config["stage"]["ma200_slope_lookback"]   used upstream, not here

    Returns
    -------
    StageResult
        Dataclass with stage, label, confidence, reason, slopes, and is_buyable.

    Raises
    ------
    KeyError
        If any required column is missing from *row*.
    """
    _check_required_columns(row)

    close: float = float(row["close"])
    sma_50: float = float(row["sma_50"])
    sma_200: float = float(row["sma_200"])
    slope_50: float = float(row["ma_slope_50"])
    slope_200: float = float(row["ma_slope_200"])

    stage_cfg: dict = config.get("stage", {})
    threshold: float = float(stage_cfg.get("flat_slope_threshold", 0.0005))

    # ------------------------------------------------------------------
    # Stage 2 — ALL 5 conditions must be simultaneously true (no short-circuit)
    # ------------------------------------------------------------------
    c1_price_above_50 = close > sma_50
    c2_price_above_200 = close > sma_200
    c3_ma_stack = sma_50 > sma_200
    c4_slope_200_up = slope_200 > 0
    c5_slope_50_up = slope_50 > 0

    is_stage2 = c1_price_above_50 and c2_price_above_200 and c3_ma_stack and c4_slope_200_up and c5_slope_50_up

    if is_stage2:
        confidence = _stage2_confidence(
            close, sma_50, sma_200, slope_50, slope_200, threshold
        )
        failed = [
            desc for flag, desc in [
                (c1_price_above_50, f"close({close:.2f}) > sma_50({sma_50:.2f})"),
                (c2_price_above_200, f"close({close:.2f}) > sma_200({sma_200:.2f})"),
                (c3_ma_stack, f"sma_50({sma_50:.2f}) > sma_200({sma_200:.2f})"),
                (c4_slope_200_up, f"slope_200({slope_200:.5f}) > 0"),
                (c5_slope_50_up, f"slope_50({slope_50:.5f}) > 0"),
            ] if flag
        ]
        reason = (
            f"All 5 Stage 2 conditions satisfied: {'; '.join(failed)}. "
            f"MA slopes: 200d={slope_200:.5f}, 50d={slope_50:.5f}."
        )
        log.debug("Stage 2 detected | confidence=%d | close=%.2f", confidence, close)
        return StageResult(
            stage=2,
            label=_STAGE_LABELS[2],
            confidence=confidence,
            reason=reason,
            ma_slope_200=slope_200,
            ma_slope_50=slope_50,
            is_buyable=True,
        )

    # ------------------------------------------------------------------
    # Stage 4 — Declining: below both MAs, SMA200 slope negative
    # ------------------------------------------------------------------
    if close < sma_50 and close < sma_200 and slope_200 < 0:
        confidence = _stage4_confidence(slope_50, slope_200)
        reason = (
            f"Stage 4 — price({close:.2f}) below sma_50({sma_50:.2f}) and "
            f"sma_200({sma_200:.2f}); slope_200={slope_200:.5f} < 0 (declining). "
            f"Never buy."
        )
        log.debug("Stage 4 detected | close=%.2f | slope_200=%.5f", close, slope_200)
        return StageResult(
            stage=4,
            label=_STAGE_LABELS[4],
            confidence=confidence,
            reason=reason,
            ma_slope_200=slope_200,
            ma_slope_50=slope_50,
            is_buyable=False,
        )

    # ------------------------------------------------------------------
    # Stage 3 — Topping: lost SMA50, still above SMA200
    # OR SMA50 declining while price was recently above both MAs.
    # ------------------------------------------------------------------
    if (close < sma_50 and close > sma_200) or (slope_50 < 0 and close > sma_200):
        confidence = _stage3_confidence(close, sma_50, sma_200, slope_50, slope_200)
        lost_50 = close < sma_50
        above_200 = close > sma_200
        reason = (
            f"Stage 3 — "
            + ("price({:.2f}) below sma_50({:.2f}); ".format(close, sma_50) if lost_50 else "")
            + ("price({:.2f}) above sma_200({:.2f}); ".format(close, sma_200) if above_200 else "")
            + f"slope_50={slope_50:.5f}"
            + (" (declining)" if slope_50 < 0 else "")
            + ". Do not initiate new positions."
        )
        log.debug("Stage 3 detected | close=%.2f | slope_50=%.5f", close, slope_50)
        return StageResult(
            stage=3,
            label=_STAGE_LABELS[3],
            confidence=confidence,
            reason=reason,
            ma_slope_200=slope_200,
            ma_slope_50=slope_50,
            is_buyable=False,
        )

    # ------------------------------------------------------------------
    # Stage 1 — Basing: everything else (price below / between MAs, slopes flat)
    # ------------------------------------------------------------------
    confidence = _stage1_confidence(close, sma_50, sma_200, slope_50, slope_200, threshold)
    both_flat = abs(slope_200) < threshold and abs(slope_50) < threshold
    reason = (
        f"Stage 1 — price({close:.2f}) below or between MAs "
        f"[sma_50={sma_50:.2f}, sma_200={sma_200:.2f}]; "
        f"slopes {'near zero (flat)' if both_flat else 'mixed / insufficient for Stage 2'}: "
        f"slope_200={slope_200:.5f}, slope_50={slope_50:.5f}. "
        f"Wait for Stage 2 breakout."
    )
    log.debug("Stage 1 detected | close=%.2f | flat=%s", close, both_flat)
    return StageResult(
        stage=1,
        label=_STAGE_LABELS[1],
        confidence=confidence,
        reason=reason,
        ma_slope_200=slope_200,
        ma_slope_50=slope_50,
        is_buyable=False,
    )
