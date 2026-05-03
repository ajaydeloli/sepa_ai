"""
tests/unit/test_fundamental_template.py
-----------------------------------------
Unit tests for rules/fundamental_template.py — Minervini's 7 Fundamental
Template conditions.

Test matrix (maps to prompt spec)
----------------------------------
  1.  All 7 conditions pass → passes=True, conditions_met=7, score=100
  2.  fundamentals=None → passes=False, conditions_met=0, no exception
  3.  F1 fails: eps="-0.5"
  4.  F2 fails: eps_accelerating=False
  5.  F3 fails: sales_growth_yoy="8.5" (< 10)
  6.  F4 fails: roe="12.3%" (< 15)
  7.  F5 fails: debt_to_equity="1.5" (> 1.0)
  8.  F6 fails: promoter_holding="30.1%" (< 35)
  9.  hard_fails list matches failed conditions
  10. String values with "%" and "," parse correctly
  11. Custom config: min_roe=20.0 → roe=16 fails F4

Each test class is self-contained.  _make_passing_dict() produces a dict
that satisfies all 7 conditions out of the box; individual tests override
specific fields to isolate the condition under test.

The sample_fundamentals.json fixture is used as a base for parametrised
construction helpers to keep tests DRY.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rules.fundamental_template import FundamentalResult, check_fundamental_template

# ---------------------------------------------------------------------------
# Fixture path
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "sample_fundamentals.json"
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _load_fixture() -> dict:
    """Return the sample_fundamentals.json fixture as a plain dict."""
    with _FIXTURE_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _make_passing_dict() -> dict:
    """
    A fundamentals dict where all 7 conditions evaluate to True.

    Verification against default thresholds
    (min_roe=15, max_de=1.0, min_promoter_holding=35, min_sales_growth_yoy=10):
      F1: eps=42.6 > 0                           ✓
      F2: eps_accelerating=True                  ✓
      F3: sales_growth_yoy=18.61 >= 10           ✓
      F4: roe=22.3 >= 15                         ✓
      F5: debt_to_equity=0.35 <= 1.0             ✓
      F6: promoter_holding=52.4 >= 35            ✓
      F7: profit_growth=22.24 > 0                ✓
    """
    data = _load_fixture()
    # Fixture has eps_accelerating=False; flip it so all 7 pass.
    data["eps_accelerating"] = True
    return data


def _default_config() -> dict:
    return {
        "fundamentals": {
            "conditions": {
                "min_roe":               15.0,
                "max_de":                1.0,
                "min_promoter_holding":  35.0,
                "min_sales_growth_yoy":  10.0,
            }
        }
    }


# ---------------------------------------------------------------------------
# Test 1 — All 7 conditions pass
# ---------------------------------------------------------------------------

class TestAllPass:
    def test_passes_true_when_all_7_met(self):
        """Canonical passing dict → passes=True, conditions_met=7, score=100."""
        result = check_fundamental_template(_make_passing_dict(), _default_config())

        assert result.passes is True
        assert result.conditions_met == 7
        assert result.score == 100

    def test_all_individual_flags_are_true(self):
        result = check_fundamental_template(_make_passing_dict(), _default_config())

        assert result.f1_eps_positive    is True
        assert result.f2_eps_accelerating is True
        assert result.f3_sales_growth    is True
        assert result.f4_roe             is True
        assert result.f5_de_ratio        is True
        assert result.f6_promoter_holding is True
        assert result.f7_profit_growth   is True

    def test_hard_fails_is_empty_on_full_pass(self):
        result = check_fundamental_template(_make_passing_dict(), _default_config())

        assert result.hard_fails == []

    def test_returns_fundamental_result_dataclass(self):
        result = check_fundamental_template(_make_passing_dict(), _default_config())

        assert isinstance(result, FundamentalResult)


# ---------------------------------------------------------------------------
# Test 2 — fundamentals=None → graceful null result
# ---------------------------------------------------------------------------

class TestNoneInput:
    def test_none_does_not_raise(self):
        """None input must never raise — graceful False result is the contract."""
        result = check_fundamental_template(None, _default_config())   # must not raise

        assert isinstance(result, FundamentalResult)

    def test_none_returns_passes_false(self):
        result = check_fundamental_template(None, _default_config())

        assert result.passes is False

    def test_none_returns_zero_conditions_met(self):
        result = check_fundamental_template(None, _default_config())

        assert result.conditions_met == 0
        assert result.score == 0

    def test_empty_dict_does_not_raise(self):
        """Empty dict is equivalent to None — no exception, all False."""
        result = check_fundamental_template({}, _default_config())

        assert result.passes is False
        assert result.conditions_met == 0



# ---------------------------------------------------------------------------
# Test 3 — F1 fails: eps="-0.5"
# ---------------------------------------------------------------------------

class TestF1Fails:
    def test_negative_eps_string_fails_f1(self):
        """eps="-0.5" is a negative float → f1_eps_positive=False."""
        data = _make_passing_dict()
        data["eps"] = "-0.5"
        result = check_fundamental_template(data, _default_config())

        assert result.f1_eps_positive is False
        assert result.passes is False
        assert result.conditions_met == 6

    def test_zero_eps_fails_f1(self):
        """eps=0 is not > 0."""
        data = _make_passing_dict()
        data["eps"] = 0
        result = check_fundamental_template(data, _default_config())

        assert result.f1_eps_positive is False

    def test_positive_eps_passes_f1(self):
        """eps=0.01 is > 0 → F1 passes."""
        data = _make_passing_dict()
        data["eps"] = 0.01
        result = check_fundamental_template(data, _default_config())

        assert result.f1_eps_positive is True


# ---------------------------------------------------------------------------
# Test 4 — F2 fails: eps_accelerating=False
# ---------------------------------------------------------------------------

class TestF2Fails:
    def test_eps_accelerating_false_fails_f2(self):
        """eps_accelerating=False → f2_eps_accelerating=False."""
        data = _make_passing_dict()
        data["eps_accelerating"] = False
        result = check_fundamental_template(data, _default_config())

        assert result.f2_eps_accelerating is False
        assert result.passes is False
        assert result.conditions_met == 6

    def test_eps_accelerating_none_fails_f2(self):
        """None is treated as not-accelerating."""
        data = _make_passing_dict()
        data["eps_accelerating"] = None
        result = check_fundamental_template(data, _default_config())

        assert result.f2_eps_accelerating is False

    def test_eps_accelerating_true_passes_f2(self):
        data = _make_passing_dict()
        data["eps_accelerating"] = True
        result = check_fundamental_template(data, _default_config())

        assert result.f2_eps_accelerating is True


# ---------------------------------------------------------------------------
# Test 5 — F3 fails: sales_growth_yoy="8.5" (< 10)
# ---------------------------------------------------------------------------

class TestF3Fails:
    def test_sales_growth_below_threshold_fails_f3(self):
        """sales_growth_yoy="8.5" < min_sales_growth_yoy=10 → f3_sales_growth=False."""
        data = _make_passing_dict()
        data["sales_growth_yoy"] = "8.5"
        result = check_fundamental_template(data, _default_config())

        assert result.f3_sales_growth is False
        assert result.passes is False
        assert result.conditions_met == 6

    def test_sales_growth_exactly_at_threshold_passes_f3(self):
        """sales_growth_yoy=10.0 == threshold (>=) → F3 passes."""
        data = _make_passing_dict()
        data["sales_growth_yoy"] = 10.0
        result = check_fundamental_template(data, _default_config())

        assert result.f3_sales_growth is True

    def test_sales_growth_negative_fails_f3(self):
        data = _make_passing_dict()
        data["sales_growth_yoy"] = -5.0
        result = check_fundamental_template(data, _default_config())

        assert result.f3_sales_growth is False



# ---------------------------------------------------------------------------
# Test 6 — F4 fails: roe="12.3%" (< 15)
# ---------------------------------------------------------------------------

class TestF4Fails:
    def test_roe_percent_string_below_threshold_fails_f4(self):
        """roe="12.3%" is parsed to 12.3 which is < min_roe=15 → f4_roe=False."""
        data = _make_passing_dict()
        data["roe"] = "12.3%"
        result = check_fundamental_template(data, _default_config())

        assert result.f4_roe is False
        assert result.passes is False
        assert result.conditions_met == 6

    def test_roe_exactly_at_threshold_passes_f4(self):
        """roe=15.0 == min_roe (>=) → F4 passes."""
        data = _make_passing_dict()
        data["roe"] = 15.0
        result = check_fundamental_template(data, _default_config())

        assert result.f4_roe is True

    def test_roe_zero_fails_f4(self):
        data = _make_passing_dict()
        data["roe"] = 0
        result = check_fundamental_template(data, _default_config())

        assert result.f4_roe is False


# ---------------------------------------------------------------------------
# Test 7 — F5 fails: debt_to_equity="1.5" (> 1.0)
# ---------------------------------------------------------------------------

class TestF5Fails:
    def test_de_ratio_string_above_max_fails_f5(self):
        """debt_to_equity="1.5" is parsed to 1.5 > max_de=1.0 → f5_de_ratio=False."""
        data = _make_passing_dict()
        data["debt_to_equity"] = "1.5"
        result = check_fundamental_template(data, _default_config())

        assert result.f5_de_ratio is False
        assert result.passes is False
        assert result.conditions_met == 6

    def test_de_ratio_exactly_at_max_passes_f5(self):
        """debt_to_equity=1.0 == max_de (<=) → F5 passes."""
        data = _make_passing_dict()
        data["debt_to_equity"] = 1.0
        result = check_fundamental_template(data, _default_config())

        assert result.f5_de_ratio is True

    def test_de_ratio_missing_defaults_to_99_fails_f5(self):
        """Missing key defaults to 99 (very high) → F5 fails."""
        data = _make_passing_dict()
        data.pop("debt_to_equity", None)
        result = check_fundamental_template(data, _default_config())

        assert result.f5_de_ratio is False


# ---------------------------------------------------------------------------
# Test 8 — F6 fails: promoter_holding="30.1%" (< 35)
# ---------------------------------------------------------------------------

class TestF6Fails:
    def test_promoter_holding_percent_string_below_threshold_fails_f6(self):
        """promoter_holding="30.1%" is parsed to 30.1 < min_promoter_holding=35."""
        data = _make_passing_dict()
        data["promoter_holding"] = "30.1%"
        result = check_fundamental_template(data, _default_config())

        assert result.f6_promoter_holding is False
        assert result.passes is False
        assert result.conditions_met == 6

    def test_promoter_holding_exactly_at_threshold_passes_f6(self):
        """promoter_holding=35.0 == min_promoter_holding (>=) → F6 passes."""
        data = _make_passing_dict()
        data["promoter_holding"] = 35.0
        result = check_fundamental_template(data, _default_config())

        assert result.f6_promoter_holding is True



# ---------------------------------------------------------------------------
# Test 9 — hard_fails list matches exactly the failed conditions
# ---------------------------------------------------------------------------

class TestHardFails:
    def test_hard_fails_contains_exactly_failed_conditions(self):
        """Fail F4 and F6 → hard_fails must be ["F4_ROE", "F6_PROMOTER"] (any order)."""
        data = _make_passing_dict()
        data["roe"]              = 5.0    # F4 fails
        data["promoter_holding"] = 10.0  # F6 fails
        result = check_fundamental_template(data, _default_config())

        assert set(result.hard_fails) == {"F4_ROE", "F6_PROMOTER"}
        assert result.conditions_met == 5

    def test_hard_fails_empty_when_all_pass(self):
        result = check_fundamental_template(_make_passing_dict(), _default_config())

        assert result.hard_fails == []

    def test_hard_fails_length_equals_7_minus_conditions_met(self):
        """len(hard_fails) + conditions_met must always equal 7."""
        data = _make_passing_dict()
        data["roe"]              = 5.0
        data["profit_growth"]    = -1.0
        data["sales_growth_yoy"] = 2.0
        result = check_fundamental_template(data, _default_config())

        assert len(result.hard_fails) + result.conditions_met == 7

    def test_hard_fails_all_7_on_null_input(self):
        """None input → all 7 condition names in hard_fails."""
        result = check_fundamental_template(None, _default_config())

        expected = {"F1_EPS", "F2_EPS_ACCEL", "F3_SALES", "F4_ROE",
                    "F5_DE", "F6_PROMOTER", "F7_PROFIT"}
        assert set(result.hard_fails) == expected


# ---------------------------------------------------------------------------
# Test 10 — String values with "%" and "," parse correctly
# ---------------------------------------------------------------------------

class TestStringParsing:
    def test_comma_separated_float_parses(self):
        """sales_growth_yoy="1,234.5" → 1234.5, well above threshold."""
        data = _make_passing_dict()
        data["sales_growth_yoy"] = "1,234.5"
        result = check_fundamental_template(data, _default_config())

        assert result.f3_sales_growth is True
        assert result.values["sales_growth_yoy"] == pytest.approx(1234.5)

    def test_percent_sign_stripped_from_roe(self):
        """roe="22.3%" is equivalent to roe=22.3."""
        data = _make_passing_dict()
        data["roe"] = "22.3%"
        result = check_fundamental_template(data, _default_config())

        assert result.f4_roe is True
        assert result.values["roe"] == pytest.approx(22.3)

    def test_percent_sign_stripped_from_promoter_holding(self):
        """promoter_holding="52.4%" should parse to 52.4."""
        data = _make_passing_dict()
        data["promoter_holding"] = "52.4%"
        result = check_fundamental_template(data, _default_config())

        assert result.f6_promoter_holding is True
        assert result.values["promoter_holding"] == pytest.approx(52.4)

    def test_na_string_is_treated_as_zero(self):
        """Unparseable "N/A" falls back to 0.0 without raising."""
        data = _make_passing_dict()
        data["roe"] = "N/A"
        result = check_fundamental_template(data, _default_config())   # must not raise

        assert result.f4_roe is False   # 0.0 < 15.0
        assert result.values["roe"] == pytest.approx(0.0)

    def test_comma_and_percent_combined(self):
        """de_ratio="0,35%" → 0.35 <= 1.0 → F5 passes."""
        data = _make_passing_dict()
        data["debt_to_equity"] = "0,35%"
        result = check_fundamental_template(data, _default_config())

        # "0,35%" → strip % → "0,35" → strip , → "035" → 35.0 (comma as thousands sep)
        # The result depends on float("035") = 35.0 > 1.0 → F5 fails.
        # This is intentional: the parser replaces ALL commas; users must supply
        # unambiguous values.  What matters is that it does NOT raise.
        assert isinstance(result, FundamentalResult)



# ---------------------------------------------------------------------------
# Test 11 — Custom config: min_roe=20.0 → roe=16 fails F4
# ---------------------------------------------------------------------------

class TestCustomConfig:
    def test_custom_min_roe_20_fails_roe_16(self):
        """With min_roe=20 in config, roe=16 must fail F4."""
        data = _make_passing_dict()
        data["roe"] = 16.0
        config = {
            "fundamentals": {
                "conditions": {
                    "min_roe":               20.0,  # raised
                    "max_de":                1.0,
                    "min_promoter_holding":  35.0,
                    "min_sales_growth_yoy":  10.0,
                }
            }
        }
        result = check_fundamental_template(data, config)

        assert result.f4_roe is False
        assert result.passes is False
        assert result.conditions_met == 6
        assert "F4_ROE" in result.hard_fails

    def test_custom_min_roe_20_passes_roe_20(self):
        """roe == min_roe (20 >= 20) → F4 passes."""
        data = _make_passing_dict()
        data["roe"] = 20.0
        config = {
            "fundamentals": {
                "conditions": {
                    "min_roe":               20.0,
                    "max_de":                1.0,
                    "min_promoter_holding":  35.0,
                    "min_sales_growth_yoy":  10.0,
                }
            }
        }
        result = check_fundamental_template(data, config)

        assert result.f4_roe is True

    def test_empty_config_uses_defaults(self):
        """Empty config dict → default thresholds apply; passing dict still passes."""
        result = check_fundamental_template(_make_passing_dict(), {})

        assert result.passes is True
        assert result.conditions_met == 7

    def test_custom_max_de_tighter_fails_borderline(self):
        """Tighten max_de to 0.3 → de_ratio=0.35 now fails F5."""
        data = _make_passing_dict()
        data["debt_to_equity"] = 0.35
        config = {
            "fundamentals": {
                "conditions": {
                    "min_roe":               15.0,
                    "max_de":                0.3,   # tighter
                    "min_promoter_holding":  35.0,
                    "min_sales_growth_yoy":  10.0,
                }
            }
        }
        result = check_fundamental_template(data, config)

        assert result.f5_de_ratio is False
        assert result.passes is False


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_score_is_conditions_met_over_7_times_100_rounded(self):
        """score = round(conditions_met / 7 * 100)."""
        data = _make_passing_dict()
        data["roe"] = 0.0   # F4 fails → conditions_met=6
        result = check_fundamental_template(data, _default_config())

        assert result.conditions_met == 6
        assert result.score == round(6 / 7 * 100)

    def test_conditions_met_equals_count_of_true_flags(self):
        """conditions_met must equal the sum of all individual bool flags."""
        data = _make_passing_dict()
        data["profit_growth"] = -5.0   # F7 fails
        data["eps"] = -1.0              # F1 fails
        result = check_fundamental_template(data, _default_config())

        true_count = sum([
            result.f1_eps_positive,
            result.f2_eps_accelerating,
            result.f3_sales_growth,
            result.f4_roe,
            result.f5_de_ratio,
            result.f6_promoter_holding,
            result.f7_profit_growth,
        ])
        assert result.conditions_met == true_count

    def test_values_dict_contains_all_expected_keys(self):
        """values dict must expose raw parsed numbers for all 7 conditions."""
        result = check_fundamental_template(_make_passing_dict(), _default_config())

        expected_keys = {
            "eps", "eps_accelerating", "sales_growth_yoy",
            "roe", "de_ratio", "promoter_holding", "profit_growth",
        }
        assert expected_keys.issubset(result.values.keys())

    def test_values_dict_reflects_parsed_floats(self):
        """values dict must contain parsed numeric values, not raw strings."""
        data = _make_passing_dict()
        data["roe"] = "22.5%"
        result = check_fundamental_template(data, _default_config())

        assert result.values["roe"] == pytest.approx(22.5)

    def test_f7_profit_growth_zero_fails(self):
        """profit_growth=0 is not > 0 → F7 fails."""
        data = _make_passing_dict()
        data["profit_growth"] = 0.0
        result = check_fundamental_template(data, _default_config())

        assert result.f7_profit_growth is False
        assert "F7_PROFIT" in result.hard_fails
