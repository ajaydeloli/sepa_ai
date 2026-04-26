"""
tests/unit/test_moving_averages.py
-----------------------------------
Unit tests for features/moving_averages.py.

All tests are self-contained; no external fixtures or I/O required.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features import moving_averages
from utils.exceptions import InsufficientDataError

# ---------------------------------------------------------------------------
# Constants that mirror the module's defaults
# ---------------------------------------------------------------------------

_NEW_COLS = [
    "sma_10",
    "sma_21",
    "sma_50",
    "sma_150",
    "sma_200",
    "ema_21",
    "ma_slope_50",
    "ma_slope_200",
]

_ORIG_COLS = ["open", "high", "low", "close", "volume"]

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 250, trend: str = "flat") -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame with *n* business-day rows.

    Parameters
    ----------
    n:
        Number of rows.
    trend:
        ``"flat"``  – random walk centred on 0 (no drift).
        ``"up"``    – +0.5 per day drift (monotonically increasing close).
        ``"down"``  – -0.5 per day drift.
    """
    rng = np.random.default_rng(42)

    if trend == "up":
        # Strictly increasing: start at 100, add 0.5 each step + tiny noise
        close = 100.0 + np.arange(n) * 0.5 + rng.uniform(0, 0.001, n)
    elif trend == "down":
        close = 100.0 - np.arange(n) * 0.5 + rng.uniform(0, 0.001, n)
    else:  # flat / random walk
        close = 100.0 + np.cumsum(rng.normal(0, 1, n))

    spread = rng.uniform(0.5, 2.0, n)
    high = close + spread
    low = close - spread
    open_ = close + rng.uniform(-0.5, 0.5, n)
    volume = rng.integers(100_000, 5_000_000, n).astype(float)

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=pd.bdate_range("2020-01-01", periods=n),
    )


def _default_config() -> dict:
    """Return a config dict with default stage parameters."""
    return {"stage": {"ma200_slope_lookback": 20, "ma50_slope_lookback": 10}}


# ---------------------------------------------------------------------------
# Test 1: Happy path — all 8 columns present, last row fully populated
# ---------------------------------------------------------------------------


def test_happy_path_all_columns_present_no_nan_in_last_row():
    """250-row DataFrame → 8 new columns; last row has no NaN."""
    df = _make_ohlcv(n=250)
    result = moving_averages.compute(df, _default_config())

    for col in _NEW_COLS:
        assert col in result.columns, f"Missing column: {col}"

    last = result.iloc[-1]
    for col in _NEW_COLS:
        assert not pd.isna(last[col]), f"NaN in last row for column: {col}"


# ---------------------------------------------------------------------------
# Test 2: SMA_50 matches pandas rolling(50).mean() exactly
# ---------------------------------------------------------------------------


def test_sma_50_matches_pandas_rolling():
    """sma_50 must be bit-for-bit identical to pandas rolling(50).mean()."""
    df = _make_ohlcv(n=250)
    result = moving_averages.compute(df.copy(), _default_config())

    expected = df["close"].rolling(window=50, min_periods=50).mean()
    pd.testing.assert_series_equal(
        result["sma_50"].rename("close"),
        expected,
        check_names=False,
        check_exact=False,
        rtol=1e-10,
    )


# ---------------------------------------------------------------------------
# Test 3: EMA_21 matches pandas ewm(span=21, adjust=False).mean() exactly
# ---------------------------------------------------------------------------


def test_ema_21_matches_pandas_ewm():
    """ema_21 must equal ewm(span=21, adjust=False).mean()."""
    df = _make_ohlcv(n=250)
    result = moving_averages.compute(df.copy(), _default_config())

    expected = df["close"].ewm(span=21, adjust=False).mean()
    pd.testing.assert_series_equal(
        result["ema_21"].rename("close"),
        expected,
        check_names=False,
        check_exact=False,
        rtol=1e-10,
    )


# ---------------------------------------------------------------------------
# Test 4: slope_200 is positive for an upward-trending price series
# ---------------------------------------------------------------------------


def test_slope_200_positive_for_uptrend():
    """ma_slope_200 in last row must be positive when price trends up."""
    df = _make_ohlcv(n=250, trend="up")
    result = moving_averages.compute(df, _default_config())

    slope = result["ma_slope_200"].iloc[-1]
    assert slope > 0, f"Expected positive slope for uptrend, got {slope}"


# ---------------------------------------------------------------------------
# Test 5: InsufficientDataError raised when len(df) < 200
# ---------------------------------------------------------------------------


def test_insufficient_data_error_raised_below_200_rows():
    """Any DataFrame shorter than 200 rows must raise InsufficientDataError."""
    for n in [0, 1, 50, 199]:
        df = _make_ohlcv(n=n) if n > 0 else pd.DataFrame(
            columns=_ORIG_COLS,
            index=pd.DatetimeIndex([]),
        )
        with pytest.raises(InsufficientDataError) as exc_info:
            moving_averages.compute(df, _default_config())

        err = exc_info.value
        assert err.required == 200, f"n={n}: expected required=200, got {err.required}"
        assert err.available == n, f"n={n}: expected available={n}, got {err.available}"


def test_exactly_200_rows_does_not_raise():
    """Exactly 200 rows must not raise InsufficientDataError."""
    df = _make_ohlcv(n=200)
    result = moving_averages.compute(df, _default_config())
    assert len(result) == 200


# ---------------------------------------------------------------------------
# Test 6: Output has all original columns + exactly 8 new ones (no duplication)
# ---------------------------------------------------------------------------


def test_output_preserves_original_columns_and_adds_exactly_8():
    """compute() must keep every original column and add exactly 8 new ones."""
    df = _make_ohlcv(n=250)
    original_cols = list(df.columns)

    result = moving_averages.compute(df, _default_config())

    result_cols = list(result.columns)

    # Every original column is still present
    for col in original_cols:
        assert col in result_cols, f"Original column lost: {col}"

    # Exactly 8 new columns added
    new_cols = [c for c in result_cols if c not in original_cols]
    assert len(new_cols) == 8, (
        f"Expected exactly 8 new columns, got {len(new_cols)}: {new_cols}"
    )

    # No duplicate columns
    assert len(result_cols) == len(set(result_cols)), (
        f"Duplicate columns detected: {result_cols}"
    )

    # The 8 new columns are exactly the expected set
    assert set(new_cols) == set(_NEW_COLS), (
        f"New column mismatch.\n  Expected: {sorted(_NEW_COLS)}\n  Got: {sorted(new_cols)}"
    )


# ---------------------------------------------------------------------------
# Additional: SMA_150 uses exactly 150 rows (not an approximation)
# ---------------------------------------------------------------------------


def test_sma_150_uses_exactly_150_rows():
    """sma_150 row N must be NaN until row 149 and valid from row 150 onward."""
    df = _make_ohlcv(n=250)
    result = moving_averages.compute(df, _default_config())

    # Row index 149 is the 150th row (0-based); that's the first valid SMA-150
    assert pd.isna(result["sma_150"].iloc[148]), (
        "sma_150 should be NaN at row 149 (only 149 rows of data)"
    )
    assert not pd.isna(result["sma_150"].iloc[149]), (
        "sma_150 should be valid at row 150 (exactly 150 rows of data)"
    )

    # The value at row 149 must match pandas exactly
    expected_first_valid = df["close"].iloc[:150].mean()
    assert abs(result["sma_150"].iloc[149] - expected_first_valid) < 1e-10
