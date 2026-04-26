"""
tests/unit/test_pre_filter.py
-----------------------------
Unit tests for screener/pre_filter.py  — pre_filter() only.
All tests are pure (no I/O); build_features_index() is covered separately
via integration tests that mock the Parquet store.
"""

from __future__ import annotations

import pytest

from screener.pre_filter import pre_filter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_CFG: dict = {}  # rely on all defaults inside pre_filter


def _make_feat(
    close: float,
    high_52w: float,
    rs_rating: float,
    sma_200: float,
) -> dict:
    return {
        "close": close,
        "high_52w": high_52w,
        "rs_rating": rs_rating,
        "sma_200": sma_200,
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_passes_all_criteria():
    """Symbol with close=100, high_52w=100, rs_rating=80, sma_200=90 → PASSES."""
    features_index = {"AAA": _make_feat(close=100, high_52w=100, rs_rating=80, sma_200=90)}
    result = pre_filter(features_index, _DEFAULT_CFG)
    assert result == ["AAA"]


def test_filtered_below_52w_high_threshold():
    """close=60 is only 60% of high_52w=100 — below the 70% floor → FILTERED."""
    features_index = {"BBB": _make_feat(close=60, high_52w=100, rs_rating=80, sma_200=50)}
    result = pre_filter(features_index, _DEFAULT_CFG)
    assert result == []


def test_filtered_low_rs_rating():
    """rs_rating=40 is below the default floor of 50 → FILTERED."""
    features_index = {"CCC": _make_feat(close=90, high_52w=100, rs_rating=40, sma_200=70)}
    result = pre_filter(features_index, _DEFAULT_CFG)
    assert result == []


def test_filtered_below_sma200():
    """close=80 < sma_200=90 → FILTERED (not in Stage 2)."""
    features_index = {"DDD": _make_feat(close=80, high_52w=100, rs_rating=75, sma_200=90)}
    result = pre_filter(features_index, _DEFAULT_CFG)
    assert result == []


def test_filtered_missing_close_key():
    """Symbol dict missing the 'close' key → FILTERED without raising."""
    features_index = {
        "EEE": {
            "high_52w": 100.0,
            "rs_rating": 80.0,
            "sma_200": 70.0,
            # 'close' intentionally absent
        }
    }
    result = pre_filter(features_index, _DEFAULT_CFG)
    assert result == []


def test_empty_features_index():
    """Empty input → returns empty list."""
    result = pre_filter({}, _DEFAULT_CFG)
    assert result == []


def test_all_symbols_pass():
    """When every symbol meets all criteria, all are returned."""
    features_index = {
        "RELIANCE": _make_feat(close=2800, high_52w=3000, rs_rating=72, sma_200=2500),
        "TCS": _make_feat(close=3500, high_52w=3600, rs_rating=85, sma_200=3200),
        "INFY": _make_feat(close=1500, high_52w=1600, rs_rating=68, sma_200=1400),
    }
    result = pre_filter(features_index, _DEFAULT_CFG)
    assert sorted(result) == ["INFY", "RELIANCE", "TCS"]


# ---------------------------------------------------------------------------
# Config-override tests
# ---------------------------------------------------------------------------


def test_custom_thresholds_stricter():
    """Raising min_rs_rating to 90 filters a symbol that would otherwise pass."""
    features_index = {"FFF": _make_feat(close=100, high_52w=100, rs_rating=80, sma_200=90)}
    cfg = {"pre_filter": {"min_rs_rating": 90, "min_close_pct_of_52w_high": 0.70}}
    result = pre_filter(features_index, cfg)
    assert result == []


def test_custom_thresholds_looser():
    """Lowering min_close_pct_of_52w_high to 0.55 allows a 60%-of-high symbol through."""
    features_index = {"GGG": _make_feat(close=60, high_52w=100, rs_rating=60, sma_200=50)}
    cfg = {"pre_filter": {"min_close_pct_of_52w_high": 0.55, "min_rs_rating": 50}}
    result = pre_filter(features_index, cfg)
    assert result == ["GGG"]


def test_exactly_at_thresholds_passes():
    """Values exactly on the boundary (close == 0.70 * high_52w, rs == 50) → PASSES."""
    features_index = {
        "HHH": _make_feat(
            close=70.0,   # exactly 70% of 100
            high_52w=100.0,
            rs_rating=50.0,  # exactly at floor
            sma_200=69.9,    # close just above sma_200
        )
    }
    result = pre_filter(features_index, _DEFAULT_CFG)
    assert result == ["HHH"]


def test_mixed_pass_fail():
    """Only symbols meeting all three criteria are returned."""
    features_index = {
        "PASS1": _make_feat(close=100, high_52w=110, rs_rating=65, sma_200=90),
        "FAIL_RS": _make_feat(close=100, high_52w=110, rs_rating=30, sma_200=90),
        "FAIL_PCT": _make_feat(close=50, high_52w=110, rs_rating=65, sma_200=40),
        "PASS2": _make_feat(close=200, high_52w=210, rs_rating=75, sma_200=180),
    }
    result = pre_filter(features_index, _DEFAULT_CFG)
    assert sorted(result) == ["PASS1", "PASS2"]
