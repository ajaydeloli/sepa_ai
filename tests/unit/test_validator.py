"""
tests/unit/test_validator.py
-----------------------------
Unit tests for ingestion/validator.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from datetime import date

from ingestion.validator import validate
from utils.exceptions import DataValidationError, InsufficientDataError


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int = 60, start: str = "2023-01-02") -> pd.DataFrame:
    """Return a clean OHLCV DataFrame with *n* rows and a DatetimeIndex."""
    dates = pd.bdate_range(start=start, periods=n, freq="B")
    rng = np.random.default_rng(42)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    open_ = close - rng.uniform(-1.0, 1.0, n)
    volume = rng.integers(100_000, 5_000_000, n).astype(float)

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_passes():
    """Valid OHLCV data should pass validation without raising."""
    df = _make_ohlcv(n=60)
    result = validate(df, "TEST")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 60
    assert list(result.columns) == ["open", "high", "low", "close", "volume"]
    # Index must be sorted ascending
    assert result.index.is_monotonic_increasing


# ---------------------------------------------------------------------------
# Schema checks
# ---------------------------------------------------------------------------


def test_missing_close_raises():
    """DataFrame without 'close' column should raise DataValidationError."""
    df = _make_ohlcv(n=60).drop(columns=["close"])
    with pytest.raises(DataValidationError, match="close"):
        validate(df, "TEST")


def test_missing_multiple_columns_raises():
    """Missing open + volume should be reported in the error."""
    df = _make_ohlcv(n=60).drop(columns=["open", "volume"])
    with pytest.raises(DataValidationError):
        validate(df, "TEST")


# ---------------------------------------------------------------------------
# Sanity-row checks
# ---------------------------------------------------------------------------


def test_high_lt_low_drops_row_and_raises_if_too_many():
    """Rows with high < low should be dropped; >5% triggers DataValidationError."""
    df = _make_ohlcv(n=60)
    # Make 10 rows (16.7 %) have high < low — well above the 5 % threshold
    df.iloc[:10, df.columns.get_loc("high")] = df.iloc[:10]["low"] - 5.0
    with pytest.raises(DataValidationError, match="5"):
        validate(df, "TEST")


def test_single_bad_high_low_row_is_dropped_silently():
    """A single bad row (< 5 %) should be silently dropped, not raise."""
    df = _make_ohlcv(n=60)
    # Inject exactly 1 bad row (1/60 ≈ 1.7 % — under the 5 % threshold)
    df.iloc[0, df.columns.get_loc("high")] = df.iloc[0]["low"] - 1.0
    result = validate(df, "TEST")
    assert len(result) == 59  # one row dropped


def test_volume_zero_exceeds_threshold_raises():
    """More than 5 % of rows with volume = 0 should raise DataValidationError."""
    df = _make_ohlcv(n=60)
    # Set 6 rows (10 %) to volume = 0
    df.iloc[:6, df.columns.get_loc("volume")] = 0.0
    with pytest.raises(DataValidationError):
        validate(df, "TEST")


def test_volume_zero_within_threshold_drops_silently():
    """Exactly 1 row with volume = 0 (< 5 %) should be dropped without raising."""
    df = _make_ohlcv(n=60)
    df.iloc[0, df.columns.get_loc("volume")] = 0.0
    result = validate(df, "TEST")
    assert len(result) == 59


# ---------------------------------------------------------------------------
# InsufficientDataError
# ---------------------------------------------------------------------------


def test_too_few_rows_raises_insufficient_data():
    """Fewer than 50 rows should raise InsufficientDataError."""
    df = _make_ohlcv(n=30)
    with pytest.raises(InsufficientDataError) as exc_info:
        validate(df, "TEST")
    assert exc_info.value.required == 50
    assert exc_info.value.available == 30


def test_exactly_50_rows_passes():
    """Exactly 50 rows should NOT raise InsufficientDataError."""
    df = _make_ohlcv(n=50)
    result = validate(df, "TEST")
    assert len(result) == 50


# ---------------------------------------------------------------------------
# Index handling
# ---------------------------------------------------------------------------


def test_unsorted_index_is_sorted():
    """Unsorted DatetimeIndex should be silently sorted ascending."""
    df = _make_ohlcv(n=60)
    # Reverse the index order
    df_reversed = df.iloc[::-1].copy()
    assert not df_reversed.index.is_monotonic_increasing  # precondition
    result = validate(df_reversed, "TEST")
    assert result.index.is_monotonic_increasing


def test_non_datetime_index_is_converted():
    """String dates as index should be converted to DatetimeIndex."""
    df = _make_ohlcv(n=60)
    df.index = df.index.strftime("%Y-%m-%d")  # convert to strings
    result = validate(df, "TEST")
    assert isinstance(result.index, pd.DatetimeIndex)
