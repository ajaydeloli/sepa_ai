"""
rules/trend_template.py
-----------------------
Minervini's 8 Trend Template conditions for the SEPA stock screening system.

Evaluates a single pd.Series feature row and returns a TrendTemplateResult
dataclass.  Missing or NaN columns are handled gracefully — the corresponding
condition evaluates to False and a warning is logged; no exception is raised.

Condition summary
-----------------
  1  price > SMA_150  AND  price > SMA_200
  2  SMA_150 > SMA_200
  3  SMA_200 slope > 0  (pre-computed ma_slope_200 — do NOT recompute here)
  4  SMA_50 > SMA_150  AND  SMA_50 > SMA_200
  5  price > SMA_50
  6  price >= low_52w * (1 + pct_above_52w_low / 100)
  7  price >= high_52w * (1 − pct_below_52w_high / 100)
  8  rs_rating >= min_rs_rating

Design constraints (from PROJECT_DESIGN.md §7.2 and Appendix A)
---------------------------------------------------------------
- Operates on a single pd.Series row — no DataFrame loading.
- SMA_150 is a REQUIRED dedicated column; never substituted by SMA_200.
- ma_slope_200 is pre-computed upstream (features/moving_averages.py).
- No imports from screener/, pipeline/, api/, or dashboard/.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class TrendTemplateResult:
    passes: bool                         # True only when ALL 8 conditions pass
    conditions_met: int                  # exact count of True conditions (0–8)
    condition_1: bool  # price > SMA_150 AND price > SMA_200
    condition_2: bool  # SMA_150 > SMA_200
    condition_3: bool  # SMA_200 slope > 0
    condition_4: bool  # SMA_50 > SMA_150 AND SMA_50 > SMA_200
    condition_5: bool  # price > SMA_50
    condition_6: bool  # price >= N% above 52-week low
    condition_7: bool  # price within N% of 52-week high
    condition_8: bool  # RS Rating >= min_rs_rating
    details: dict = field(default_factory=dict)  # numeric values for debuggability


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_float(row: pd.Series, col: str) -> tuple[float | None, bool]:
    """
    Safely retrieve *col* from *row* as a float.

    Returns
    -------
    (value, ok)
        ok=True  → *value* is a finite float ready to use.
        ok=False → *value* is None; the caller must set the condition to False.

    A warning is logged for every failure so operators can trace data gaps.
    """
    if col not in row.index:
        log.warning(
            "check_trend_template: required column '%s' is missing from the "
            "feature row — the dependent condition(s) will be set to False.",
            col,
        )
        return None, False

    raw = row[col]
    try:
        fval = float(raw)
    except (TypeError, ValueError):
        log.warning(
            "check_trend_template: column '%s' has a non-numeric value (%r) — "
            "dependent condition(s) will be set to False.",
            col, raw,
        )
        return None, False

    if math.isnan(fval):
        log.warning(
            "check_trend_template: column '%s' is NaN — "
            "dependent condition(s) will be set to False.",
            col,
        )
        return None, False

    return fval, True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_trend_template(row: pd.Series, config: dict) -> TrendTemplateResult:
    """
    Evaluates all 8 Minervini Trend Template conditions against a single feature row.

    Parameters
    ----------
    row:
        A pd.Series — one row from the feature Parquet file.
        Required columns: close, sma_50, sma_150, sma_200, ma_slope_200,
                          high_52w, low_52w, rs_rating.

    config:
        Project config dict.  Relevant keys (all under "trend_template")::

            config["trend_template"]["pct_above_52w_low"]   default: 25.0
            config["trend_template"]["pct_below_52w_high"]  default: 25.0
            config["trend_template"]["min_rs_rating"]       default: 70

    Returns
    -------
    TrendTemplateResult
        Dataclass with passes, conditions_met (0–8), one bool per condition,
        and a details dict containing the raw numeric values used.
        *passes* is True ONLY when all 8 conditions are True.
        Missing columns yield False for the affected condition(s); no exception
        is raised — TrendTemplateResult(passes=False) is the graceful output.
    """
    # ------------------------------------------------------------------
    # Config extraction
    # ------------------------------------------------------------------
    cfg: dict = config.get("trend_template", {})
    pct_above_52w_low: float  = float(cfg.get("pct_above_52w_low",  25.0))
    pct_below_52w_high: float = float(cfg.get("pct_below_52w_high", 25.0))
    min_rs_rating: int        = int(cfg.get("min_rs_rating", 70))

    # ------------------------------------------------------------------
    # Safely read all required columns
    # ------------------------------------------------------------------
    close,       close_ok  = _safe_float(row, "close")
    sma_50,      sma50_ok  = _safe_float(row, "sma_50")
    sma_150,     sma150_ok = _safe_float(row, "sma_150")
    sma_200,     sma200_ok = _safe_float(row, "sma_200")
    ma_slope_200, slope_ok = _safe_float(row, "ma_slope_200")
    high_52w,    high_ok   = _safe_float(row, "high_52w")
    low_52w,     low_ok    = _safe_float(row, "low_52w")
    rs_rating,   rs_ok     = _safe_float(row, "rs_rating")

    # ------------------------------------------------------------------
    # Condition evaluation
    # ------------------------------------------------------------------

    # Condition 1 — price > SMA_150  AND  price > SMA_200
    # NOTE: sma_150 is its own required column — never approximate with sma_200.
    #       If sma_150 is missing or NaN, condition_1 AND condition_2 both → False.
    if close_ok and sma150_ok and sma200_ok:
        condition_1 = bool(close > sma_150 and close > sma_200)
    else:
        condition_1 = False

    # Condition 2 — SMA_150 > SMA_200
    if sma150_ok and sma200_ok:
        condition_2 = bool(sma_150 > sma_200)
    else:
        condition_2 = False

    # Condition 3 — SMA_200 slope > 0  (pre-computed; do NOT recompute here)
    if slope_ok:
        condition_3 = bool(ma_slope_200 > 0)
    else:
        condition_3 = False

    # Condition 4 — SMA_50 > SMA_150  AND  SMA_50 > SMA_200
    if sma50_ok and sma150_ok and sma200_ok:
        condition_4 = bool(sma_50 > sma_150 and sma_50 > sma_200)
    else:
        condition_4 = False

    # Condition 5 — price > SMA_50
    if close_ok and sma50_ok:
        condition_5 = bool(close > sma_50)
    else:
        condition_5 = False

    # Condition 6 — price >= low_52w * (1 + pct_above_52w_low / 100)
    if close_ok and low_ok:
        threshold_low = low_52w * (1.0 + pct_above_52w_low / 100.0)
        condition_6 = bool(close >= threshold_low)
    else:
        condition_6 = False

    # Condition 7 — price >= high_52w * (1 − pct_below_52w_high / 100)
    if close_ok and high_ok:
        threshold_high = high_52w * (1.0 - pct_below_52w_high / 100.0)
        condition_7 = bool(close >= threshold_high)
    else:
        condition_7 = False

    # Condition 8 — rs_rating >= min_rs_rating
    if rs_ok:
        condition_8 = bool(int(rs_rating) >= min_rs_rating)
    else:
        condition_8 = False

    # ------------------------------------------------------------------
    # Aggregate
    # ------------------------------------------------------------------
    all_conditions = [
        condition_1, condition_2, condition_3, condition_4,
        condition_5, condition_6, condition_7, condition_8,
    ]
    conditions_met: int = sum(all_conditions)
    passes: bool = all(all_conditions)

    # ------------------------------------------------------------------
    # Details dict — raw numeric values for downstream debugging / logging
    # ------------------------------------------------------------------
    details: dict = {
        "close":              close       if close_ok  else float("nan"),
        "sma_50":             sma_50      if sma50_ok  else float("nan"),
        "sma_150":            sma_150     if sma150_ok else float("nan"),
        "sma_200":            sma_200     if sma200_ok else float("nan"),
        "ma_slope_200":       ma_slope_200 if slope_ok else float("nan"),
        "high_52w":           high_52w    if high_ok   else float("nan"),
        "low_52w":            low_52w     if low_ok    else float("nan"),
        "rs_rating":          int(rs_rating) if rs_ok  else None,
        "pct_above_52w_low":  pct_above_52w_low,
        "pct_below_52w_high": pct_below_52w_high,
    }

    log.debug(
        "check_trend_template: ticker=<from_row> passes=%s conditions_met=%d/8 "
        "close=%s sma_150=%s sma_200=%s rs_rating=%s",
        passes,
        conditions_met,
        f"{close:.2f}" if close_ok else "N/A",
        f"{sma_150:.2f}" if sma150_ok else "N/A",
        f"{sma_200:.2f}" if sma200_ok else "N/A",
        int(rs_rating) if rs_ok else "N/A",
    )

    return TrendTemplateResult(
        passes=passes,
        conditions_met=conditions_met,
        condition_1=condition_1,
        condition_2=condition_2,
        condition_3=condition_3,
        condition_4=condition_4,
        condition_5=condition_5,
        condition_6=condition_6,
        condition_7=condition_7,
        condition_8=condition_8,
        details=details,
    )
