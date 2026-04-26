"""
features/pivot.py
-----------------
Swing high / swing low pivot detection for the Minervini SEPA screener.

Purpose
-------
Pivots are local price extremes used by VCP detection (Step 5) to identify
contraction legs.  The rule engine consumes ``pivot_high`` as the breakout
level.

Algorithm (ZigZag / N-bar pivot)
---------------------------------
A swing HIGH at index *i* is confirmed when::

    high[i] > high[i-N … i-1]  AND  high[i] > high[i+1 … i+N]

A swing LOW at index *i* is confirmed when::

    low[i] < low[i-N … i-1]   AND  low[i] < low[i+1 … i+N]

Only the MOST RECENT confirmed pivot (high or low) is stored in the row.
The full pivot list is exposed via ``find_all_pivots`` for VCP internals.

Implements the standard ``compute`` interface contract:
  - Pure function: no I/O, no side effects, no global state.
  - Appends indicator columns to the input DataFrame and returns it.
  - Raises InsufficientDataError when len(df) < 2 * sensitivity + 1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.exceptions import InsufficientDataError
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_SENSITIVITY: int = 5  # bars on each side required to confirm a pivot

# ---------------------------------------------------------------------------
# Public API — compute()
# ---------------------------------------------------------------------------


def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Append pivot indicator columns to *df* and return it.

    Parameters
    ----------
    df:
        Cleaned OHLCV DataFrame with columns ``high`` and ``low`` (at minimum).
    config:
        Screening configuration dict.  Relevant key::

            config["vcp"]["pivot_sensitivity"]  (default 5)

        Controls how many bars on each side must be lower / higher for a
        point to qualify as a pivot.

    Returns
    -------
    pd.DataFrame
        The same DataFrame with four new columns appended:

        ``pivot_high``
            float — price of the most recent confirmed swing high
            (``NaN`` if no pivot high was found).
        ``pivot_low``
            float — price of the most recent confirmed swing low
            (``NaN`` if no pivot low was found).
        ``pivot_high_idx``
            int — row offset from the *end* of df where ``pivot_high``
            occurred (0 = most recent bar, 1 = one bar ago, …).
            ``-1`` when no pivot high exists.
        ``pivot_low_idx``
            int — same convention for ``pivot_low``.

    Raises
    ------
    InsufficientDataError
        When ``len(df) < 2 * sensitivity + 1`` — not enough bars to
        confirm any pivot.
    """
    sensitivity: int = config.get("vcp", {}).get(
        "pivot_sensitivity", _DEFAULT_SENSITIVITY
    )
    min_rows: int = 2 * sensitivity + 1
    n_rows: int = len(df)

    if n_rows < min_rows:
        raise InsufficientDataError(
            "DataFrame too short for pivot detection",
            required=min_rows,
            available=n_rows,
        )

    log.debug(
        "pivot.compute: rows=%d sensitivity=%d",
        n_rows,
        sensitivity,
    )

    swing_highs, swing_lows = find_all_pivots(df, sensitivity=sensitivity)

    # ------------------------------------------------------------------
    # Resolve the most-recent confirmed pivot high / low
    # ------------------------------------------------------------------
    last_idx = n_rows - 1  # absolute row index of the final bar

    if swing_highs:
        # swing_highs is sorted ascending by row index; last entry is newest
        ph_abs_idx, ph_price = swing_highs[-1]
        pivot_high: float = ph_price
        pivot_high_idx: int = last_idx - ph_abs_idx
    else:
        pivot_high = float("nan")
        pivot_high_idx = -1

    if swing_lows:
        pl_abs_idx, pl_price = swing_lows[-1]
        pivot_low: float = pl_price
        pivot_low_idx: int = last_idx - pl_abs_idx
    else:
        pivot_low = float("nan")
        pivot_low_idx = -1

    # ------------------------------------------------------------------
    # Broadcast scalar results to every row (consistent with the module
    # contract — all rows carry the same "current" indicator value)
    # ------------------------------------------------------------------
    df["pivot_high"] = pivot_high
    df["pivot_low"] = pivot_low
    df["pivot_high_idx"] = pivot_high_idx
    df["pivot_low_idx"] = pivot_low_idx

    log.debug(
        "pivot.compute finished: pivot_high=%.4f (idx=%d) pivot_low=%.4f (idx=%d)",
        pivot_high if not np.isnan(pivot_high) else -1,
        pivot_high_idx,
        pivot_low if not np.isnan(pivot_low) else -1,
        pivot_low_idx,
    )
    return df


# ---------------------------------------------------------------------------
# Shared utility — find_all_pivots()
# ---------------------------------------------------------------------------


def find_all_pivots(
    df: pd.DataFrame,
    sensitivity: int = _DEFAULT_SENSITIVITY,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Return every confirmed swing high and swing low in *df*.

    This function is **not** called by :func:`compute` directly; it is a
    shared utility consumed by ``vcp.py`` for contraction counting.

    Parameters
    ----------
    df:
        OHLCV DataFrame.  Uses ``df["high"]`` for swing highs and
        ``df["low"]`` for swing lows.
    sensitivity:
        Number of bars on each side that must be strictly lower (for a high)
        or strictly higher (for a low) to confirm the pivot.

    Returns
    -------
    tuple[list[tuple[int, float]], list[tuple[int, float]]]
        ``(swing_highs, swing_lows)`` where each element is a list of
        ``(row_index, price)`` tuples sorted in ascending order of
        ``row_index``.  Row index refers to the integer position in *df*
        (i.e. ``df.iloc[row_index]``).
    """
    highs: pd.Series = df["high"].to_numpy(dtype=float)
    lows: pd.Series = df["low"].to_numpy(dtype=float)
    n: int = len(highs)

    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []

    # Confirmed pivots require N bars on both sides, so the valid range
    # is [sensitivity, n - sensitivity - 1] inclusive.
    for i in range(sensitivity, n - sensitivity):
        h = highs[i]
        left_h = highs[i - sensitivity: i]
        right_h = highs[i + 1: i + sensitivity + 1]

        if h > left_h.max() and h > right_h.max():
            swing_highs.append((i, float(h)))

        lo = lows[i]
        left_l = lows[i - sensitivity: i]
        right_l = lows[i + 1: i + sensitivity + 1]

        if lo < left_l.min() and lo < right_l.min():
            swing_lows.append((i, float(lo)))

    return swing_highs, swing_lows
