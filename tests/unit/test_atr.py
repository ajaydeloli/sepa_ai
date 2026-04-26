"""
tests/unit/test_atr.py
----------------------
Unit tests for features/atr.py.

Test matrix
-----------
1. atr_14 is always positive (no NaN, no non-positive values).
2. atr_pct == atr_14 / close * 100 within float tolerance.
3. atr_14 matches manual Wilder's smoothing on a small toy DataFrame.
4. InsufficientDataError is raised when len(df) < 20.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.atr import compute
from utils.exceptions import InsufficientDataError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int, seed: int = 42) -> pd.DataFrame:
    """Return a synthetic OHLCV DataFrame with *n* business-day rows."""
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 1, n))
    high  = close + rng.uniform(0.5, 2.0, n)
    low   = close - rng.uniform(0.5, 2.0, n)
    open_ = close + rng.normal(0, 0.5, n)
    volume = rng.integers(500_000, 2_000_000, n).astype(float)

    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _make_toy_df() -> pd.DataFrame:
    """Tiny hand-crafted 5-row DataFrame for deterministic ATR verification."""
    idx = pd.bdate_range("2020-01-01", periods=5)
    data = {
        "open":   [10.0, 11.0, 10.5, 12.0, 11.5],
        "high":   [12.0, 13.0, 12.5, 14.0, 13.5],
        "low":    [ 9.0, 10.0,  9.5, 11.0, 10.5],
        "close":  [11.0, 12.0, 11.5, 13.0, 12.5],
        "volume": [1e6,  1e6,  1e6,  1e6,  1e6 ],
    }
    return pd.DataFrame(data, index=idx)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestATRPositive:
    """atr_14 must be strictly positive for every valid row."""

    def test_atr_positive_all_rows(self) -> None:
        df = _make_ohlcv(60)
        result = compute(df.copy(), {})
        valid = result["atr_14"].dropna()
        assert (valid > 0).all(), "atr_14 contains non-positive values"

    def test_no_unexpected_nans_after_warmup(self) -> None:
        """After the first row (which has NaN prev_close), ATR should be finite."""
        df = _make_ohlcv(60)
        result = compute(df.copy(), {})
        # EWM with adjust=False propagates from row 0, so all rows should be
        # finite once the Series is fully populated.
        assert result["atr_14"].isna().sum() == 0, "Unexpected NaN in atr_14"


class TestATRPercentage:
    """atr_pct == atr_14 / close * 100 within float tolerance."""

    def test_atr_pct_formula(self) -> None:
        df = _make_ohlcv(60)
        result = compute(df.copy(), {})
        expected = result["atr_14"] / result["close"] * 100.0
        pd.testing.assert_series_equal(
            result["atr_pct"],
            expected,
            check_names=False,
            rtol=1e-9,
        )


class TestATRWilderSmoothing:
    """atr_14 matches a manual Wilder's EWM on the toy DataFrame."""

    def test_matches_manual_ewm(self) -> None:
        """
        Compute TR manually and apply ewm(alpha=1/14, adjust=False).
        The result must match features.atr.compute column-for-column.
        """
        df = _make_toy_df()

        # Pad to 20 rows so InsufficientDataError is not raised.
        # Repeat the last row 15 times to reach 20.
        padding = pd.DataFrame(
            [df.iloc[-1]] * 15,
            index=pd.bdate_range(df.index[-1] + pd.offsets.BDay(1), periods=15),
        )
        df_full = pd.concat([df, padding])

        result = compute(df_full.copy(), {"atr": {"period": 14}})

        # Build expected TR for just the toy section
        close = df_full["close"]
        prev_close = close.shift(1)
        tr = pd.concat(
            [
                df_full["high"] - df_full["low"],
                (df_full["high"] - prev_close).abs(),
                (df_full["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)

        expected_atr = tr.ewm(alpha=1.0 / 14, adjust=False).mean()

        pd.testing.assert_series_equal(
            result["atr_14"],
            expected_atr,
            check_names=False,
            rtol=1e-9,
        )


class TestATRInsufficientData:
    """InsufficientDataError is raised when len(df) < 20."""

    @pytest.mark.parametrize("n_rows", [0, 1, 10, 19])
    def test_raises_for_short_df(self, n_rows: int) -> None:
        df = _make_ohlcv(n_rows) if n_rows > 0 else pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"]
        )
        with pytest.raises(InsufficientDataError) as exc_info:
            compute(df, {})
        assert exc_info.value.required == 20
        assert exc_info.value.available == n_rows

    def test_exactly_20_rows_does_not_raise(self) -> None:
        df = _make_ohlcv(20)
        result = compute(df.copy(), {})
        assert "atr_14" in result.columns
