"""
tests/unit/test_volume.py
-------------------------
Unit tests for features/volume.py.

Test matrix
-----------
1. vol_ratio == 1.0 when volume equals its 50-day rolling average.
2. acc_dist_score == up_vol_days - down_vol_days at every row.
3. acc_dist_score is positive on a rising DataFrame where up-volume dominates.
4. InsufficientDataError when len(df) < 55.
5. All original OHLCV columns preserved in output.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.volume import compute
from utils.exceptions import InsufficientDataError


# ---------------------------------------------------------------------------
# Constants that mirror module defaults
# ---------------------------------------------------------------------------

_AVG_PERIOD   = 50   # matches config default
_LOOKBACK     = 20   # matches config default

# Index of first row where BOTH rolling windows are fully warmed up:
#   avg_period-1 (first non-NaN avg row) + lookback (one full lookback window)
_FIRST_VALID  = _AVG_PERIOD + _LOOKBACK - 1   # = 69

_OHLCV_COLS = ["open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int, seed: int = 42) -> pd.DataFrame:
    """Return a synthetic OHLCV DataFrame with *n* business-day rows."""
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    high  = close + rng.uniform(0.2, 1.5, n)
    low   = close - rng.uniform(0.2, 1.5, n)
    open_ = close + rng.normal(0, 0.3, n)
    volume = rng.integers(500_000, 2_000_000, n).astype(float)

    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _make_constant_volume_df(n: int = 80, vol: float = 1_000_000.0) -> pd.DataFrame:
    """
    Return a DataFrame where every bar has the *same* volume.

    Because vol_50d_avg is a simple mean of identical values, vol_ratio
    should be exactly 1.0 for every bar after the 50-row warm-up.
    """
    rng = np.random.default_rng(0)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(
        {
            "open":   close,
            "high":   close + 1.0,
            "low":    close - 1.0,
            "close":  close,
            "volume": vol,          # constant across all rows
        },
        index=idx,
    )


def _make_rising_high_volume_df(n: int = 100) -> pd.DataFrame:
    """
    Return a DataFrame designed so acc_dist_score is strongly positive:

    * Close rises monotonically — always close > prev_close (no down days).
    * First 50 bars carry low volume (500k) to prime the 50-day average.
    * Bars 50..n carry high volume (3M) — well above the rolling average
      once the window settles, so ``volume > vol_50d_avg`` is always True
      for those bars.

    From bar ``_FIRST_VALID`` (= avg_period + lookback - 1 = 69) onward,
    every 20-bar lookback window falls entirely within the high-volume
    section, giving up_vol_days = 20 and down_vol_days = 0 → score = 20.
    """
    close  = 100.0 + np.arange(n, dtype=float)
    # Two-phase volume: low then high so avg is clearly below 3M at row 69+
    volume = np.concatenate([np.full(50, 500_000.0), np.full(n - 50, 3_000_000.0)])

    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(
        {
            "open":   close - 0.1,
            "high":   close + 0.5,
            "low":    close - 0.5,
            "close":  close,
            "volume": volume,
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestVolRatioEqualsOne:
    """vol_ratio == 1.0 when volume equals its own 50-day rolling average."""

    def test_constant_volume_ratio_is_one(self) -> None:
        df = _make_constant_volume_df(n=80)
        result = compute(df.copy(), {})

        # After the 50-day warm-up the rolling average equals the constant
        # volume, so vol_ratio must be 1.0.
        valid_ratio = result["vol_ratio"].iloc[_AVG_PERIOD - 1:]
        np.testing.assert_allclose(
            valid_ratio.values,
            1.0,
            rtol=1e-9,
            err_msg="vol_ratio should be 1.0 when volume is constant",
        )


class TestAccDistScore:
    """acc_dist_score == up_vol_days - down_vol_days at every non-NaN row."""

    def test_formula_holds_elementwise(self) -> None:
        df = _make_ohlcv(100)
        result = compute(df.copy(), {})

        # acc_dist_score may be NaN where the 20-bar window is not yet full
        mask = result["acc_dist_score"].notna()
        expected = result.loc[mask, "up_vol_days"] - result.loc[mask, "down_vol_days"]
        pd.testing.assert_series_equal(
            result.loc[mask, "acc_dist_score"],
            expected,
            check_names=False,
            rtol=1e-9,
        )


class TestAccDistScorePositiveOnRisingMarket:
    """
    acc_dist_score > 0 for every bar once both rolling windows are fully
    warmed up (row >= _FIRST_VALID = avg_period + lookback - 1 = 69).

    At that point every 20-bar lookback window lies entirely within the
    high-volume section where close is always rising.
    """

    def test_positive_score_on_rising_df(self) -> None:
        df = _make_rising_high_volume_df(n=100)
        result = compute(df.copy(), {})

        # Slice to the section where both rolling windows have full data
        fully_valid = result["acc_dist_score"].iloc[_FIRST_VALID:]
        assert len(fully_valid) > 0, "No fully-valid rows — increase n"
        assert (fully_valid > 0).all(), (
            f"Expected all acc_dist_scores > 0 from row {_FIRST_VALID}; "
            f"got min={fully_valid.min()}, values={fully_valid.values}"
        )


class TestInsufficientData:
    """InsufficientDataError is raised when len(df) < 55."""

    @pytest.mark.parametrize("n_rows", [0, 1, 30, 54])
    def test_raises_for_short_df(self, n_rows: int) -> None:
        df = _make_ohlcv(n_rows) if n_rows > 0 else pd.DataFrame(
            columns=_OHLCV_COLS
        )
        with pytest.raises(InsufficientDataError) as exc_info:
            compute(df, {})
        assert exc_info.value.required == 55
        assert exc_info.value.available == n_rows

    def test_exactly_55_rows_does_not_raise(self) -> None:
        df = _make_ohlcv(55)
        result = compute(df.copy(), {})
        assert "vol_50d_avg" in result.columns


class TestOHLCVColumnsPreserved:
    """All original OHLCV columns must survive compute() unchanged."""

    def test_original_columns_present(self) -> None:
        df = _make_ohlcv(80)
        original = df[_OHLCV_COLS].copy()
        result = compute(df.copy(), {})

        for col in _OHLCV_COLS:
            assert col in result.columns, f"Column '{col}' missing from output"
            pd.testing.assert_series_equal(
                result[col],
                original[col],
                check_names=True,
                rtol=1e-12,
            )

    def test_no_columns_renamed_or_dropped(self) -> None:
        df = _make_ohlcv(80)
        result = compute(df.copy(), {})
        for col in _OHLCV_COLS:
            assert col in result.columns
        # Output should have MORE columns than input (indicators appended)
        assert len(result.columns) > len(_OHLCV_COLS)
