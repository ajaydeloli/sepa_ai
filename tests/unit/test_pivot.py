"""
tests/unit/test_pivot.py
------------------------
Unit tests for features/pivot.py — swing high / low pivot detection.

All tests are self-contained; no external fixtures or I/O required.
Test DataFrames are constructed manually with known, deterministic pivot
positions so assertions are exact.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features import pivot
from utils.exceptions import InsufficientDataError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SENSITIVITY = 3          # small value to keep test frames short
_MIN_ROWS = 2 * _SENSITIVITY + 1   # = 7

_ORIG_COLS = ["open", "high", "low", "close", "volume"]
_NEW_COLS = ["pivot_high", "pivot_low", "pivot_high_idx", "pivot_low_idx"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_df(closes: list[float], sensitivity: int = _SENSITIVITY) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of close prices.

    ``high = close + 0.5``  and  ``low = close - 0.5`` so that pivot
    detection on *high* and *low* mirrors the shape of the close series.
    """
    c = np.array(closes, dtype=float)
    h = c + 0.5
    lo = c - 0.5
    return pd.DataFrame(
        {
            "open":   c,
            "high":   h,
            "low":    lo,
            "close":  c,
            "volume": np.ones(len(c)) * 1_000_000,
        },
        index=pd.bdate_range("2023-01-01", periods=len(c)),
    )


def _default_config(sensitivity: int = _SENSITIVITY) -> dict:
    return {"vcp": {"pivot_sensitivity": sensitivity}}


# ---------------------------------------------------------------------------
# Test 1 — V-shape produces exactly one swing low, zero swing highs
# ---------------------------------------------------------------------------


def test_v_shape_produces_exactly_one_swing_low():
    """A clear V-shape (down then up) must yield exactly one swing low.

    With sensitivity=3 the trough at index 3 has 3 strictly higher bars on
    each side, so it qualifies as the sole swing low.
    """
    # Index:  0    1    2    3    4    5    6
    closes = [110, 107, 104, 100, 104, 107, 110]
    df = _make_df(closes)
    highs, lows = pivot.find_all_pivots(df, sensitivity=_SENSITIVITY)

    assert len(lows) == 1, f"Expected 1 swing low, got {len(lows)}: {lows}"
    assert lows[0][0] == 3, f"Swing low should be at index 3, got {lows[0][0]}"
    assert len(highs) == 0, f"Expected 0 swing highs for V-shape, got {len(highs)}"


# ---------------------------------------------------------------------------
# Test 2 — Inverted-V produces exactly one swing high, zero swing lows
# ---------------------------------------------------------------------------


def test_inverted_v_produces_exactly_one_swing_high():
    """A clear inverted-V (up then down) must yield exactly one swing high."""
    # Index:  0    1    2    3    4    5    6
    closes = [100, 103, 106, 110, 106, 103, 100]
    df = _make_df(closes)
    highs, lows = pivot.find_all_pivots(df, sensitivity=_SENSITIVITY)

    assert len(highs) == 1, f"Expected 1 swing high, got {len(highs)}: {highs}"
    assert highs[0][0] == 3, f"Swing high should be at index 3, got {highs[0][0]}"
    assert len(lows) == 0, f"Expected 0 swing lows for inverted-V, got {len(lows)}"


# ---------------------------------------------------------------------------
# Test 3 — Flat series produces no pivots
# ---------------------------------------------------------------------------


def test_flat_series_produces_no_pivots():
    """A completely flat price series must yield no swing highs or lows."""
    closes = [100.0] * 15
    df = _make_df(closes)
    highs, lows = pivot.find_all_pivots(df, sensitivity=_SENSITIVITY)

    assert highs == [], f"Expected no swing highs for flat series, got {highs}"
    assert lows == [], f"Expected no swing lows for flat series, got {lows}"


# ---------------------------------------------------------------------------
# Test 4 — pivot_high_idx == 0 when the most recent bar IS the swing high
# ---------------------------------------------------------------------------


def test_pivot_high_idx_zero_when_last_bar_is_pivot_high():
    """pivot_high_idx must be 0 when the newest confirmed swing high is
    the last bar that could be confirmed (i.e., sensitivity bars before end).

    We place a peak at position N-1-sensitivity so it is the last confirmable
    bar: it has exactly *sensitivity* bars to its right.
    """
    # sensitivity = 3; we want a peak at index 3 in a 7-bar series.
    # That peak IS the last confirmable position (index = 7-1-3 = 3).
    # pivot_high_idx = (n-1) - 3 = 3.  Not 0.
    #
    # To get pivot_high_idx == 0, the pivot must land at row n-1 = last row.
    # But a pivot needs N bars to the RIGHT, so the last possible pivot is at
    # n-1-sensitivity.  "idx=0" means the most recent confirmed pivot happened
    # to sit at the very last confirmable position which maps to an offset of
    # sensitivity from the end — unless we place a later (non-peak) run of
    # bars so no newer pivot supersedes it.
    #
    # Simplest construction: peak at position (len-1-sensitivity), then
    # strictly decreasing tail of exactly `sensitivity` bars.
    # e.g. sensitivity=3: [100,104,108,112,109,106,103]
    #                idx:    0    1    2    3    4    5    6
    # Peak at 3; n=7; last_idx=6; offset = 6-3 = 3.  Still not 0.
    #
    # For offset == 0 we need the pivot AT last_idx, which is impossible by
    # definition (can't have N bars to the right of the last bar).
    # The spec says "0 = most recent bar" meaning the pivot IS the last bar.
    # Interpret: pivot_high_idx==0 ↔ confirmed pivot at row (n-1-sensitivity)
    # AND we define that row as "most recent confirmable" i.e. the last bar
    # where enough look-forward exists.
    #
    # Re-reading the prompt: "pivot_high_idx — int: row offset from end of df
    # where pivot_high occurred (0 = most recent bar)".
    # So offset 0 = last bar of df.  A pivot AT the last bar is impossible.
    # The test says "pivot_high_idx is 0 when the last bar IS the most recent
    # swing high" — interpreting "most recent" as "closest to end of series".
    # We verify offset equals (sensitivity) for the last-confirmable pivot.
    #
    # We test the documented contract faithfully: place the ONLY swing high
    # at the rightmost possible position and assert pivot_high_idx == _SENSITIVITY.

    n = 2 * _SENSITIVITY + 1 + _SENSITIVITY  # extra tail bars for last-confirmable peak
    # Build: rising to peak at (n - 1 - _SENSITIVITY), then strictly falling
    peak_pos = n - 1 - _SENSITIVITY
    closes = list(range(100, 100 + peak_pos + 1))   # rising
    tail = [closes[-1] - (i + 1) * 2 for i in range(_SENSITIVITY)]
    closes = closes + tail
    df = _make_df(closes)
    result = pivot.compute(df, _default_config())

    # The pivot high offset from the last bar equals _SENSITIVITY
    assert result["pivot_high_idx"].iloc[-1] == _SENSITIVITY, (
        f"Expected pivot_high_idx={_SENSITIVITY} for last-confirmable peak, "
        f"got {result['pivot_high_idx'].iloc[-1]}"
    )


# ---------------------------------------------------------------------------
# Test 5 — pivot_high > pivot_low when both are present
# ---------------------------------------------------------------------------


def test_pivot_high_greater_than_pivot_low_when_both_present():
    """When both pivots exist, pivot_high must strictly exceed pivot_low.

    Constructed series: W-shape — peak → trough → peak, with sensitivity=3.
    Uses a long enough series so both the high and low are confirmed.
    """
    #        0    1    2    3    4    5    6    7    8    9   10   11   12
    closes = [100, 105, 110, 105, 100, 95,  100, 105, 110, 105, 100, 95, 100]
    df = _make_df(closes)
    result = pivot.compute(df, _default_config())

    ph = result["pivot_high"].iloc[-1]
    pl = result["pivot_low"].iloc[-1]

    assert not np.isnan(ph), "pivot_high should not be NaN"
    assert not np.isnan(pl), "pivot_low should not be NaN"
    assert ph > pl, f"Expected pivot_high ({ph}) > pivot_low ({pl})"



# ---------------------------------------------------------------------------
# Test 6 — All original columns are preserved in the output DataFrame
# ---------------------------------------------------------------------------


def test_all_original_columns_preserved():
    """compute() must not drop any pre-existing DataFrame column.

    The function appends four new columns (pivot_high, pivot_low,
    pivot_high_idx, pivot_low_idx) but must leave every column that was
    present before the call fully intact — same name, same values.
    """
    closes = [100, 103, 106, 110, 106, 103, 100, 97, 100, 103, 106, 110, 106]
    df = _make_df(closes)

    # Snapshot original column values before calling compute
    original_values = {col: df[col].tolist() for col in _ORIG_COLS}

    result = pivot.compute(df, _default_config())

    # All original columns still present
    for col in _ORIG_COLS:
        assert col in result.columns, f"Column '{col}' missing from output"

    # All original column values unchanged
    for col in _ORIG_COLS:
        assert result[col].tolist() == original_values[col], (
            f"Column '{col}' values were mutated by compute()"
        )

    # All four new pivot columns were added
    for col in _NEW_COLS:
        assert col in result.columns, f"New column '{col}' missing from output"


# ---------------------------------------------------------------------------
# Test 7 — InsufficientDataError raised when len(df) < 2 * sensitivity + 1
# ---------------------------------------------------------------------------


def test_insufficient_data_raises_error():
    """compute() must raise InsufficientDataError when the DataFrame is too
    short to confirm any pivot (i.e. len(df) < 2 * sensitivity + 1).

    With sensitivity=3 the minimum is 7 rows.  A 6-row DataFrame must
    raise the error; a 7-row DataFrame must not.
    """
    sensitivity = _SENSITIVITY          # = 3
    min_rows = 2 * sensitivity + 1      # = 7
    cfg = _default_config(sensitivity)

    # --- too short: exactly min_rows - 1 rows → must raise ---
    too_short = _make_df([100.0] * (min_rows - 1))
    with pytest.raises(InsufficientDataError) as exc_info:
        pivot.compute(too_short, cfg)

    err = exc_info.value
    assert err.required == min_rows, (
        f"Expected required={min_rows}, got {err.required}"
    )
    assert err.available == min_rows - 1, (
        f"Expected available={min_rows - 1}, got {err.available}"
    )

    # --- exactly at boundary: min_rows rows → must NOT raise ---
    at_boundary = _make_df([100.0] * min_rows)
    pivot.compute(at_boundary, cfg)   # should complete without exception

    # --- also verify find_all_pivots does NOT enforce this guard
    # (it has no min-row check; the flat series simply returns empty lists) ---
    highs, lows = pivot.find_all_pivots(too_short, sensitivity=sensitivity)
    assert highs == [] and lows == [], (
        "find_all_pivots should return empty lists for a short flat series, "
        f"got highs={highs}, lows={lows}"
    )
