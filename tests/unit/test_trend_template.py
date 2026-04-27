"""
tests/unit/test_trend_template.py
----------------------------------
Unit tests for rules/trend_template.py — Minervini's 8 Trend Template conditions.

Each test is self-contained.  The _make_passing_row() helper produces a row
that satisfies all 8 conditions out of the box; individual tests override
specific fields to isolate the condition under test.

Test matrix (maps to prompt spec):
  1. All 8 conditions pass              → passes=True, conditions_met=8
  2. Condition 1 fails (close<sma_150)  → passes=False, condition_1=False
  3. Condition 8 fails (rs_rating=55)   → passes=False, conditions_met=7
  4. Condition 6 fails (10% above low)  → passes=False, condition_6=False
  5. Condition 7 fails (30% below high) → passes=False, condition_7=False
  6. Missing sma_150 column             → condition_1=False, no exception
  7. Custom min_rs_rating=80, rating=75 → condition_8=False
  8. details dict contains all keys     → keys and types validated

Note on Condition 1 geometry (Test 2)
--------------------------------------
Mathematically, if C4 (sma_50 > sma_150 AND sma_50 > sma_200) and
C5 (close > sma_50) both hold, then transitively close > sma_150, so C1
cannot be False.  Therefore failing C1 alone — while keeping C4 and C5
True — is impossible.  Test 2 deliberately sets close below sma_150 and
accepts that C4 also fails; it verifies that condition_1 is False and
passes is False, which is the meaningful contract.
"""

import math

import pandas as pd
import pytest

from rules.trend_template import TrendTemplateResult, check_trend_template


# ---------------------------------------------------------------------------
# Shared helpers (as specified in the prompt)
# ---------------------------------------------------------------------------

def _make_passing_row() -> pd.Series:
    """
    A row where all 8 Trend Template conditions evaluate to True.

    Verification:
      C1: close(100) > sma_150(80) AND close(100) > sma_200(75)         ✓
      C2: sma_150(80) > sma_200(75)                                      ✓
      C3: ma_slope_200(0.02) > 0                                         ✓
      C4: sma_50(85) > sma_150(80) AND sma_50(85) > sma_200(75)         ✓
      C5: close(100) > sma_50(85)                                        ✓
      C6: 100 >= 60 * 1.25 = 75                                          ✓
      C7: 100 >= 110 * 0.75 = 82.5                                       ✓
      C8: rs_rating(82) >= 70                                            ✓
    """
    return pd.Series({
        "close": 100, "sma_50": 85, "sma_150": 80, "sma_200": 75,
        "ma_slope_200": 0.02, "high_52w": 110, "low_52w": 60, "rs_rating": 82,
    })


def _default_config() -> dict:
    return {"trend_template": {
        "pct_above_52w_low": 25.0,
        "pct_below_52w_high": 25.0,
        "min_rs_rating": 70,
    }}


# ---------------------------------------------------------------------------
# Test 1 — All 8 conditions pass
# ---------------------------------------------------------------------------

class TestAllPass:
    def test_all_8_conditions_pass(self):
        """Canonical passing row → passes=True, conditions_met=8."""
        result = check_trend_template(_make_passing_row(), _default_config())

        assert result.passes is True
        assert result.conditions_met == 8
        assert isinstance(result, TrendTemplateResult)

    def test_all_individual_conditions_are_true(self):
        result = check_trend_template(_make_passing_row(), _default_config())

        for i in range(1, 9):
            assert getattr(result, f"condition_{i}") is True, (
                f"condition_{i} should be True on the passing row"
            )

    def test_passes_requires_all_8_simultaneously(self):
        """passes must be the logical AND of all 8 — not a count threshold."""
        result = check_trend_template(_make_passing_row(), _default_config())
        assert result.passes == (result.conditions_met == 8)


# ---------------------------------------------------------------------------
# Test 2 — Condition 1 fails (close < sma_150)
# ---------------------------------------------------------------------------

class TestCondition1Fails:
    def test_close_below_sma150_fails_condition1(self):
        """
        Set close below sma_150 so C1 evaluates to False.
        Because C4 (sma_50 > sma_150) and C5 (close > sma_50) are
        geometrically coupled to C1, C4 also fails when close < sma_150.
        The contract: condition_1 is False and passes is False.
        """
        row = _make_passing_row()
        row["sma_150"] = 105   # close(100) < sma_150(105) → C1 fails
        result = check_trend_template(row, _default_config())

        assert result.condition_1 is False
        assert result.passes is False
        assert result.conditions_met < 8

    def test_close_equals_sma150_fails_condition1(self):
        """Strict inequality — close == sma_150 is not sufficient."""
        row = _make_passing_row()
        row["sma_150"] = 100   # close == sma_150 → not strictly greater
        result = check_trend_template(row, _default_config())

        assert result.condition_1 is False
        assert result.passes is False

    def test_close_below_sma200_fails_condition1(self):
        """C1 also fails when close < sma_200 (second sub-condition)."""
        row = _make_passing_row()
        row["sma_200"] = 110   # close(100) < sma_200(110) → C1 fails
        result = check_trend_template(row, _default_config())

        assert result.condition_1 is False
        assert result.passes is False


# ---------------------------------------------------------------------------
# Test 3 — Condition 8 fails (rs_rating=55 < threshold=70)
# ---------------------------------------------------------------------------

class TestCondition8Fails:
    def test_rs_rating_below_threshold(self):
        """rs_rating=55 with min_rs_rating=70 → condition_8=False, conditions_met=7."""
        row = _make_passing_row()
        row["rs_rating"] = 55
        result = check_trend_template(row, _default_config())

        assert result.condition_8 is False
        assert result.passes is False
        assert result.conditions_met == 7

    def test_rs_rating_exactly_at_threshold_passes(self):
        """rs_rating == min_rs_rating is acceptable (>=)."""
        row = _make_passing_row()
        row["rs_rating"] = 70
        result = check_trend_template(row, _default_config())

        assert result.condition_8 is True

    def test_rs_rating_one_below_threshold_fails(self):
        """Boundary: one below the threshold must fail."""
        row = _make_passing_row()
        row["rs_rating"] = 69
        result = check_trend_template(row, _default_config())

        assert result.condition_8 is False
        assert result.passes is False


# ---------------------------------------------------------------------------
# Test 4 — Condition 6 fails (only 10% above 52w low, threshold=25%)
# ---------------------------------------------------------------------------

class TestCondition6Fails:
    def test_close_only_10pct_above_52w_low(self):
        """
        close = low_52w * 1.10 — only 10 % above the low.
        With threshold=25 %, condition_6 must be False.
        Remaining 7 conditions pass → conditions_met=7.
        """
        low = 60.0
        row = _make_passing_row()
        row["low_52w"] = low
        row["close"]   = low * 1.10    # 66.0 — below the 75.0 threshold
        # Recalculate high_52w so C7 still passes: close >= high*(1-0.25)
        # close=66, high*(0.75)<=66 → high<=88. Use high=80.
        row["high_52w"] = 80.0
        # Also ensure sma ordering still makes C1/C5 pass:
        row["sma_50"]  = 60.0
        row["sma_150"] = 55.0
        row["sma_200"] = 50.0
        result = check_trend_template(row, _default_config())

        assert result.condition_6 is False
        assert result.passes is False

    def test_close_exactly_at_25pct_above_low_passes(self):
        """close == low * 1.25 — right at the threshold (>=)."""
        low = 60.0
        row = _make_passing_row()
        row["low_52w"] = low
        row["close"]   = low * 1.25   # exactly 75.0
        row["sma_50"]  = 65.0
        row["sma_150"] = 60.0
        row["sma_200"] = 55.0
        row["high_52w"] = 80.0
        result = check_trend_template(row, _default_config())

        assert result.condition_6 is True


# ---------------------------------------------------------------------------
# Test 5 — Condition 7 fails (close is 30% below 52w high, threshold=25%)
# ---------------------------------------------------------------------------

class TestCondition7Fails:
    def test_close_30pct_below_52w_high(self):
        """
        close = high_52w * 0.70 — 30 % below the high.
        With pct_below_52w_high=25 %, the threshold is high*0.75.
        close(77) < threshold(82.5) → condition_7=False.
        Only C7 fails → conditions_met=7.
        """
        high = 110.0
        row = _make_passing_row()
        row["high_52w"] = high
        row["close"]    = high * 0.70   # 77.0 — below threshold 82.5
        # Ensure other conditions still hold with close=77:
        row["sma_50"]  = 70.0
        row["sma_150"] = 65.0
        row["sma_200"] = 60.0
        row["low_52w"] = 55.0   # 77 >= 55*1.25=68.75 → C6 passes
        result = check_trend_template(row, _default_config())

        assert result.condition_7 is False
        assert result.passes is False
        assert result.conditions_met == 7

    def test_close_exactly_at_threshold_passes_condition7(self):
        """close == high*(1 − 0.25) — right at the boundary (>=)."""
        high = 110.0
        row = _make_passing_row()
        row["high_52w"] = high
        row["close"]    = high * 0.75   # 82.5
        row["sma_50"]  = 75.0
        row["sma_150"] = 70.0
        row["sma_200"] = 65.0
        row["low_52w"] = 60.0
        result = check_trend_template(row, _default_config())

        assert result.condition_7 is True


# ---------------------------------------------------------------------------
# Test 6 — Missing sma_150 column → condition_1=False, no exception
# ---------------------------------------------------------------------------

class TestMissingSma150:
    def test_missing_sma150_no_exception(self):
        """Dropping sma_150 must NOT raise — graceful False is the contract."""
        row = _make_passing_row().drop("sma_150")
        result = check_trend_template(row, _default_config())   # must not raise

        assert isinstance(result, TrendTemplateResult)

    def test_missing_sma150_sets_condition1_false(self):
        """C1 uses sma_150 → must be False when the column is absent."""
        row = _make_passing_row().drop("sma_150")
        result = check_trend_template(row, _default_config())

        assert result.condition_1 is False
        assert result.passes is False

    def test_missing_sma150_sets_condition2_false(self):
        """C2 (SMA_150 > SMA_200) also requires sma_150 → False."""
        row = _make_passing_row().drop("sma_150")
        result = check_trend_template(row, _default_config())

        assert result.condition_2 is False

    def test_missing_sma150_nan_treated_same_as_missing(self):
        """A NaN sma_150 value is equivalent to a missing column."""
        row = _make_passing_row()
        row["sma_150"] = float("nan")
        result = check_trend_template(row, _default_config())

        assert result.condition_1 is False
        assert result.condition_2 is False
        assert result.passes is False


# ---------------------------------------------------------------------------
# Test 7 — Custom config: min_rs_rating=80 → rs_rating=75 fails condition_8
# ---------------------------------------------------------------------------

class TestCustomConfig:
    def test_custom_min_rs_rating_80_fails_at_75(self):
        """With min_rs_rating=80 in config, rs_rating=75 must fail condition_8."""
        row = _make_passing_row()
        row["rs_rating"] = 75
        config = {"trend_template": {
            "pct_above_52w_low": 25.0,
            "pct_below_52w_high": 25.0,
            "min_rs_rating": 80,
        }}
        result = check_trend_template(row, config)

        assert result.condition_8 is False
        assert result.passes is False
        assert result.conditions_met == 7

    def test_custom_min_rs_rating_80_passes_at_80(self):
        """rs_rating == min_rs_rating (80 >= 80) → condition_8 passes."""
        row = _make_passing_row()
        row["rs_rating"] = 80
        config = {"trend_template": {
            "pct_above_52w_low": 25.0,
            "pct_below_52w_high": 25.0,
            "min_rs_rating": 80,
        }}
        result = check_trend_template(row, config)

        assert result.condition_8 is True

    def test_custom_pct_thresholds_are_respected(self):
        """Config overrides for pct_above_52w_low / pct_below_52w_high are applied."""
        # Tighten the 52w-low threshold to 30 % — same close=100, low=60 now barely misses.
        # Required: close >= 60 * 1.30 = 78 → still passes (100 >= 78).
        row = _make_passing_row()
        config = {"trend_template": {
            "pct_above_52w_low": 30.0,
            "pct_below_52w_high": 25.0,
            "min_rs_rating": 70,
        }}
        result = check_trend_template(row, config)
        assert result.condition_6 is True   # 100 >= 78 → still passes

        # Now set close very close to low to trigger failure.
        row["close"] = 65.0   # 65 < 60*1.30=78 → C6 fails
        row["sma_50"] = 58.0
        row["sma_150"] = 55.0
        row["sma_200"] = 50.0
        row["high_52w"] = 70.0
        result2 = check_trend_template(row, config)
        assert result2.condition_6 is False

    def test_empty_trend_template_section_uses_defaults(self):
        """Missing trend_template key → defaults (25%, 25%, 70) apply."""
        result = check_trend_template(_make_passing_row(), {})
        assert result.passes is True
        assert result.conditions_met == 8


# ---------------------------------------------------------------------------
# Test 8 — details dict contains all required numeric values
# ---------------------------------------------------------------------------

class TestDetailsDict:
    _REQUIRED_KEYS = (
        "close", "sma_50", "sma_150", "sma_200",
        "ma_slope_200", "high_52w", "low_52w",
        "rs_rating", "pct_above_52w_low", "pct_below_52w_high",
    )

    def test_details_contains_all_required_keys(self):
        result = check_trend_template(_make_passing_row(), _default_config())

        for key in self._REQUIRED_KEYS:
            assert key in result.details, f"details missing key: '{key}'"

    def test_details_values_match_row_input(self):
        """Numeric values in details must reflect the actual row values."""
        row = _make_passing_row()
        result = check_trend_template(row, _default_config())
        d = result.details

        assert d["close"]        == pytest.approx(100.0)
        assert d["sma_50"]       == pytest.approx(85.0)
        assert d["sma_150"]      == pytest.approx(80.0)
        assert d["sma_200"]      == pytest.approx(75.0)
        assert d["ma_slope_200"] == pytest.approx(0.02)
        assert d["high_52w"]     == pytest.approx(110.0)
        assert d["low_52w"]      == pytest.approx(60.0)
        assert d["rs_rating"]    == 82

    def test_details_contains_config_thresholds(self):
        """Config-derived thresholds are echoed into details for auditability."""
        result = check_trend_template(_make_passing_row(), _default_config())
        d = result.details

        assert d["pct_above_52w_low"]  == pytest.approx(25.0)
        assert d["pct_below_52w_high"] == pytest.approx(25.0)

    def test_details_has_nan_for_missing_columns(self):
        """Missing columns produce NaN (not KeyError) in the details dict."""
        row = _make_passing_row().drop("sma_150")
        result = check_trend_template(row, _default_config())

        assert "sma_150" in result.details
        assert math.isnan(result.details["sma_150"])

    def test_details_has_nan_for_nan_column_values(self):
        """A NaN column value should propagate as NaN into details."""
        row = _make_passing_row()
        row["sma_50"] = float("nan")
        result = check_trend_template(row, _default_config())

        assert math.isnan(result.details["sma_50"])


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_returns_trend_template_result_dataclass(self):
        result = check_trend_template(_make_passing_row(), _default_config())
        assert isinstance(result, TrendTemplateResult)

    def test_conditions_met_is_always_exact_count(self):
        """conditions_met must equal the number of True condition_N fields."""
        row = _make_passing_row()
        row["rs_rating"] = 55   # C8 fails
        row["ma_slope_200"] = -0.01   # C3 also fails
        result = check_trend_template(row, _default_config())

        true_count = sum(
            getattr(result, f"condition_{i}") for i in range(1, 9)
        )
        assert result.conditions_met == true_count

    def test_missing_multiple_columns_no_exception(self):
        """Even with several columns dropped the function must not raise."""
        row = pd.Series({"close": 100, "rs_rating": 82})
        result = check_trend_template(row, _default_config())

        assert isinstance(result, TrendTemplateResult)
        assert result.passes is False

    def test_passes_is_false_when_conditions_met_lt_8(self):
        """passes must be exactly False for any conditions_met < 8."""
        row = _make_passing_row()
        row["rs_rating"] = 50
        result = check_trend_template(row, _default_config())

        assert result.conditions_met == 7
        assert result.passes is False

    def test_condition_3_uses_precomputed_slope_not_recomputed(self):
        """C3 reads ma_slope_200 from the row — negative slope → False."""
        row = _make_passing_row()
        row["ma_slope_200"] = -0.005
        result = check_trend_template(row, _default_config())

        assert result.condition_3 is False
        assert result.passes is False
        assert result.conditions_met == 7

    def test_missing_rs_rating_sets_condition8_false_no_raise(self):
        row = _make_passing_row().drop("rs_rating")
        result = check_trend_template(row, _default_config())

        assert result.condition_8 is False
        assert result.passes is False
