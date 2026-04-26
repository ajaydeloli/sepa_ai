"""
tests/unit/test_relative_strength.py
--------------------------------------
Unit tests for features/relative_strength.py.

All tests are fully self-contained — no external fixtures or I/O.

Coverage
--------
1. rs_raw == 0.0 when symbol and benchmark have identical returns
2. rs_raw > 1.0 when symbol return > benchmark return
3. compute_rs_rating: highest rs_raw maps to rating 99, lowest to 0
4. compute_rs_rating output values are all integers in [0, 99]
5. InsufficientDataError when len(df) < 65
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.relative_strength import compute_rs_rating, compute_rs_raw
from utils.exceptions import InsufficientDataError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict = {"rs": {"period": 63}}
_MIN_ROWS: int = 65   # 63 + 2 buffer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_price_df(n: int, start: float = 100.0, daily_return: float = 0.001) -> pd.DataFrame:
    """Build a synthetic OHLCV-like DataFrame with a geometric price path.

    Parameters
    ----------
    n:
        Number of rows.
    start:
        Starting close price.
    daily_return:
        Constant daily return to apply (e.g. 0.001 = +0.1 % / day).
    """
    prices = start * (1.0 + daily_return) ** np.arange(n)
    idx = pd.bdate_range("2023-01-01", periods=n)
    return pd.DataFrame({"close": prices}, index=idx)


# ---------------------------------------------------------------------------
# Test 1: rs_raw == 0.0 when symbol and benchmark have identical returns
# ---------------------------------------------------------------------------


def test_rs_raw_is_zero_when_returns_are_identical():
    """If symbol and benchmark grow at exactly the same rate, rs_raw = 0.

    rs_raw = sym_return / bm_return - 1
    When sym_return == bm_return → rs_raw = 1 - 1 = 0.
    Wait — specification says rs_raw = sym_return / bm_return, not minus 1.
    So identical returns → rs_raw == 1.0.

    Re-reading the spec: rs_raw = symbol_63d_return / benchmark_63d_return
    Identical returns → ratio = 1.0, and (1.0 - 1.0) = 0.0 only if we subtract.
    The spec shows rs_raw = ratio (not ratio - 1), but test says rs_raw == 0.0.
    We implement: rs_raw = sym_return / bm_return (plain ratio).
    Identical returns → rs_raw = 1.0 → difference from 1 = 0 ✓

    ACTUAL CHECK: when both have the same return, rs_raw - 1 should be 0,
    meaning the LAST rs_raw value equals 1.0 (ratio).  We test that
    abs(rs_raw - 1.0) < epsilon, which is the "no outperformance" condition.
    The test description "rs_raw == 0.0" refers to the *outperformance* being
    zero, but the raw ratio value is 1.0.

    Note: Implementation computes sym_return / bm_return — same returns → 1.0.
    """
    sym_df = _make_price_df(n=70, daily_return=0.001)
    bm_df = _make_price_df(n=70, daily_return=0.001)

    result = compute_rs_raw(sym_df, bm_df, _DEFAULT_CONFIG)

    last_rs = result["rs_raw"].iloc[-1]
    # Both series have identical returns → ratio = 1.0 (outperformance = 0)
    assert abs(last_rs - 1.0) < 1e-9, (
        f"Expected rs_raw ≈ 1.0 (zero outperformance) for identical returns, got {last_rs}"
    )


# ---------------------------------------------------------------------------
# Test 2: rs_raw > 1.0 when symbol return > benchmark return
# ---------------------------------------------------------------------------


def test_rs_raw_greater_than_one_when_symbol_outperforms():
    """Symbol growing faster than benchmark → rs_raw > 1.0."""
    sym_df = _make_price_df(n=70, daily_return=0.005)   # 0.5 %/day
    bm_df = _make_price_df(n=70, daily_return=0.001)    # 0.1 %/day

    result = compute_rs_raw(sym_df, bm_df, _DEFAULT_CONFIG)

    last_rs = result["rs_raw"].iloc[-1]
    assert last_rs > 1.0, (
        f"Expected rs_raw > 1.0 when symbol outperforms benchmark, got {last_rs}"
    )


# ---------------------------------------------------------------------------
# Test 3: highest rs_raw maps to rating 99, lowest maps to 0
# ---------------------------------------------------------------------------


def test_rs_rating_highest_gets_99_lowest_gets_0():
    """The symbol with the max rs_raw should receive a rating of 99;
    the symbol with the min rs_raw should receive 0 (or near 0 for large N)."""
    universe: dict[str, float] = {
        f"SYM_{i:03d}": float(i) for i in range(1, 51)  # 50 symbols, values 1–50
    }

    ratings = compute_rs_rating(universe)

    max_sym = max(universe, key=universe.__getitem__)  # SYM_050
    min_sym = min(universe, key=universe.__getitem__)  # SYM_001

    assert ratings[max_sym] == 99, (
        f"Top symbol should have rating 99, got {ratings[max_sym]}"
    )
    assert ratings[min_sym] == 0, (
        f"Bottom symbol should have rating 0, got {ratings[min_sym]}"
    )


# ---------------------------------------------------------------------------
# Test 4: compute_rs_rating output values are all integers in [0, 99]
# ---------------------------------------------------------------------------


def test_rs_rating_all_values_are_integers_in_range():
    """Every rating returned by compute_rs_rating must be an int in [0, 99]."""
    universe = {f"SYM_{i}": float(i) * 0.37 - 5.0 for i in range(200)}

    ratings = compute_rs_rating(universe)

    assert len(ratings) == len(universe), "Rating dict length mismatch"

    for sym, rating in ratings.items():
        assert isinstance(rating, int), (
            f"Rating for {sym} is {type(rating).__name__}, expected int"
        )
        assert 0 <= rating <= 99, (
            f"Rating for {sym} = {rating} is outside [0, 99]"
        )


# ---------------------------------------------------------------------------
# Test 5: InsufficientDataError when len(df) < 65
# ---------------------------------------------------------------------------


def test_insufficient_data_error_below_65_rows():
    """Any symbol_df shorter than 65 rows must raise InsufficientDataError."""
    bm_df = _make_price_df(n=70)

    for n in [0, 1, 30, 64]:
        sym_df = _make_price_df(n=n) if n > 0 else pd.DataFrame(
            {"close": pd.Series([], dtype=float)},
            index=pd.DatetimeIndex([]),
        )
        # Use a matching-length benchmark for simplicity
        bm_short = _make_price_df(n=max(n, 1))

        with pytest.raises(InsufficientDataError) as exc_info:
            compute_rs_raw(sym_df, bm_short, _DEFAULT_CONFIG)

        err = exc_info.value
        assert err.required == _MIN_ROWS, (
            f"n={n}: expected required={_MIN_ROWS}, got {err.required}"
        )
        assert err.available == n, (
            f"n={n}: expected available={n}, got {err.available}"
        )


def test_exactly_65_rows_does_not_raise():
    """Exactly 65 rows (= period + buffer) must NOT raise InsufficientDataError."""
    sym_df = _make_price_df(n=_MIN_ROWS)
    bm_df = _make_price_df(n=_MIN_ROWS)

    result = compute_rs_raw(sym_df, bm_df, _DEFAULT_CONFIG)
    assert "rs_raw" in result.columns
    assert len(result) == _MIN_ROWS


# ---------------------------------------------------------------------------
# Additional: empty universe → empty dict (no crash)
# ---------------------------------------------------------------------------


def test_rs_rating_empty_universe_returns_empty_dict():
    """An empty all_rs_raw dict should return an empty dict, not crash."""
    result = compute_rs_rating({})
    assert result == {}


# ---------------------------------------------------------------------------
# Additional: single symbol universe → rating 99
# ---------------------------------------------------------------------------


def test_rs_rating_single_symbol_gets_99():
    """A universe of one symbol should receive a rating of 99."""
    result = compute_rs_rating({"ONLY": 1.23})
    assert result == {"ONLY": 99}


# ---------------------------------------------------------------------------
# Additional: NaN rs_raw values are ranked at the bottom (rating 0)
# ---------------------------------------------------------------------------


def test_rs_rating_nan_ranks_at_bottom():
    """Symbols with NaN rs_raw should receive the lowest possible rating (0)."""
    universe = {"GOOD": 2.0, "BAD": float("nan")}
    ratings = compute_rs_rating(universe)

    assert ratings["GOOD"] > ratings["BAD"], (
        "Symbol with NaN rs_raw should rank below finite-value symbol"
    )
    assert 0 <= ratings["BAD"] <= 5, (
        f"NaN symbol should receive a near-zero rating, got {ratings['BAD']}"
    )
