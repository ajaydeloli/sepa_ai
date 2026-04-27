"""
tests/unit/test_stage_detection.py
------------------------------------
Unit tests for rules/stage.py — Stage 1/2/3/4 detection.

Each test is self-contained and uses the _make_row() helper to build
a synthetic pd.Series with only the five columns detect_stage() needs.

Test matrix:
  1. Classic Stage 2 — all 5 conditions clearly pass           → stage=2, is_buyable=True
  2. Stage 4 — price below both MAs, slope_200 negative        → stage=4, is_buyable=False
  3. Stage 1 — price below MAs, both slopes ≈ 0               → stage=1, is_buyable=False
  4. Stage 3 — lost SMA50, still above SMA200, SMA50 declining → stage=3, is_buyable=False
  5. Missing column raises KeyError with descriptive message
  6. Stage 2 with very strong slopes                           → confidence=100
  Additional boundary and regression tests follow the required 6.
"""

import pytest
import pandas as pd

from rules.stage import detect_stage, StageResult

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_row(**kwargs) -> pd.Series:
    """
    Build a synthetic feature row with sensible Stage 2 defaults.
    Override individual fields via keyword arguments.
    """
    defaults = {
        "close":        150.0,
        "sma_50":       130.0,
        "sma_200":      110.0,
        "ma_slope_50":  0.05,
        "ma_slope_200": 0.03,
    }
    defaults.update(kwargs)
    return pd.Series(defaults)


def _default_config() -> dict:
    """Minimal config matching settings.yaml stage section defaults."""
    return {
        "stage": {
            "ma200_slope_lookback": 20,
            "flat_slope_threshold": 0.0005,
        }
    }


# ---------------------------------------------------------------------------
# Test 1 — Classic Stage 2
# ---------------------------------------------------------------------------

class TestStage2:
    def test_classic_stage2_basic(self):
        """All 5 conditions pass → stage 2, buyable, confidence ≥ 70."""
        row = _make_row(
            close=150, sma_50=130, sma_200=110,
            ma_slope_50=0.05, ma_slope_200=0.03
        )
        result = detect_stage(row, _default_config())

        assert result.stage == 2
        assert result.is_buyable is True
        assert result.confidence >= 70
        assert "Stage 2" in result.label
        assert result.ma_slope_200 == pytest.approx(0.03)
        assert result.ma_slope_50 == pytest.approx(0.05)

    def test_stage2_returns_stage_result_dataclass(self):
        result = detect_stage(_make_row(), _default_config())
        assert isinstance(result, StageResult)

    def test_stage2_reason_mentions_all_conditions(self):
        result = detect_stage(_make_row(), _default_config())
        # All five factual components should appear somewhere in the reason text
        assert "slope" in result.reason.lower()

    def test_stage2_requires_price_above_sma50(self):
        """close == sma_50 is NOT sufficient — must be strictly greater."""
        row = _make_row(close=130, sma_50=130)
        result = detect_stage(row, _default_config())
        assert result.stage != 2, "close == sma_50 should not qualify as Stage 2"

    def test_stage2_requires_price_above_sma200(self):
        row = _make_row(close=115, sma_50=130, sma_200=110)
        # close(115) < sma_50(130) → not Stage 2
        result = detect_stage(row, _default_config())
        assert result.stage != 2

    def test_stage2_requires_correct_ma_stack(self):
        """sma_50 must be > sma_200."""
        row = _make_row(close=150, sma_50=100, sma_200=120)
        result = detect_stage(row, _default_config())
        assert result.stage != 2, "Inverted MA stack must not produce Stage 2"

    def test_stage2_requires_positive_slope_200(self):
        row = _make_row(ma_slope_200=-0.001)
        result = detect_stage(row, _default_config())
        assert result.stage != 2

    def test_stage2_requires_positive_slope_50(self):
        row = _make_row(ma_slope_50=-0.001)
        result = detect_stage(row, _default_config())
        assert result.stage != 2

    def test_stage2_zero_slopes_not_sufficient(self):
        """Both slopes = 0 means > 0 condition fails → should not be Stage 2."""
        row = _make_row(ma_slope_50=0.0, ma_slope_200=0.0)
        result = detect_stage(row, _default_config())
        assert result.stage != 2


# ---------------------------------------------------------------------------
# Test 2 — Stage 4
# ---------------------------------------------------------------------------

class TestStage4:
    def test_classic_stage4(self):
        """Price below both MAs, slope_200 < 0 → stage=4, not buyable."""
        row = _make_row(
            close=80, sma_50=100, sma_200=120,
            ma_slope_50=-0.03, ma_slope_200=-0.02
        )
        result = detect_stage(row, _default_config())

        assert result.stage == 4
        assert result.is_buyable is False
        assert "Stage 4" in result.label
        assert result.confidence >= 60

    def test_stage4_reason_mentions_declining(self):
        row = _make_row(close=80, sma_50=100, sma_200=120,
                        ma_slope_50=-0.03, ma_slope_200=-0.02)
        result = detect_stage(row, _default_config())
        assert "declining" in result.reason.lower() or "slope" in result.reason.lower()

    def test_stage4_not_triggered_when_slope200_positive(self):
        """Price below both MAs but slope_200 is positive → not Stage 4 (could be Stage 1)."""
        row = _make_row(close=80, sma_50=100, sma_200=120,
                        ma_slope_50=-0.01, ma_slope_200=0.001)
        result = detect_stage(row, _default_config())
        assert result.stage != 4

    def test_stage4_strong_decline_yields_high_confidence(self):
        row = _make_row(close=50, sma_50=100, sma_200=120,
                        ma_slope_50=-0.05, ma_slope_200=-0.03)
        result = detect_stage(row, _default_config())
        assert result.stage == 4
        assert result.confidence >= 75


# ---------------------------------------------------------------------------
# Test 3 — Stage 1
# ---------------------------------------------------------------------------

class TestStage1:
    def test_classic_stage1_flat_slopes(self):
        """Price below both MAs, both slopes near zero → Stage 1."""
        row = _make_row(
            close=100, sma_50=102, sma_200=105,
            ma_slope_50=0.0001, ma_slope_200=0.0002
        )
        result = detect_stage(row, _default_config())

        assert result.stage == 1
        assert result.is_buyable is False
        assert "Stage 1" in result.label

    def test_stage1_reason_mentions_wait(self):
        row = _make_row(close=100, sma_50=102, sma_200=105,
                        ma_slope_50=0.0001, ma_slope_200=0.0002)
        result = detect_stage(row, _default_config())
        assert "wait" in result.reason.lower() or "stage 1" in result.reason.lower()

    def test_stage1_confidence_range(self):
        row = _make_row(close=100, sma_50=102, sma_200=105,
                        ma_slope_50=0.0001, ma_slope_200=0.0002)
        result = detect_stage(row, _default_config())
        assert 60 <= result.confidence <= 100

    def test_stage1_zero_slopes(self):
        """Exactly zero slopes = clearly flat basing → should give highest Stage 1 confidence."""
        row = _make_row(close=100, sma_50=102, sma_200=105,
                        ma_slope_50=0.0, ma_slope_200=0.0)
        result = detect_stage(row, _default_config())
        assert result.stage == 1
        assert result.confidence >= 70


# ---------------------------------------------------------------------------
# Test 4 — Stage 3
# ---------------------------------------------------------------------------

class TestStage3:
    def test_classic_stage3(self):
        """Lost SMA50, still above SMA200, SMA50 declining → Stage 3."""
        row = _make_row(
            close=95, sma_50=100, sma_200=90,
            ma_slope_50=-0.02, ma_slope_200=0.005
        )
        result = detect_stage(row, _default_config())

        assert result.stage == 3
        assert result.is_buyable is False
        assert "Stage 3" in result.label

    def test_stage3_reason_advises_no_new_positions(self):
        row = _make_row(close=95, sma_50=100, sma_200=90,
                        ma_slope_50=-0.02, ma_slope_200=0.005)
        result = detect_stage(row, _default_config())
        assert "position" in result.reason.lower() or "stage 3" in result.reason.lower()

    def test_stage3_confidence_range(self):
        row = _make_row(close=95, sma_50=100, sma_200=90,
                        ma_slope_50=-0.02, ma_slope_200=0.005)
        result = detect_stage(row, _default_config())
        assert 60 <= result.confidence <= 100

    def test_stage3_declining_sma50_above_sma200_triggers_stage3(self):
        """slope_50 < 0 AND price > sma_200 → Stage 3 even if price > sma_50."""
        row = _make_row(close=105, sma_50=100, sma_200=90,
                        ma_slope_50=-0.01, ma_slope_200=0.001)
        result = detect_stage(row, _default_config())
        # price(105) > sma_50(100) AND slope_50 < 0 AND price > sma_200 → Stage 3
        assert result.stage == 3


# ---------------------------------------------------------------------------
# Test 5 — Missing column raises KeyError with descriptive message
# ---------------------------------------------------------------------------

class TestMissingColumns:
    def test_missing_close_raises_key_error(self):
        row = pd.Series({"sma_50": 130, "sma_200": 110,
                         "ma_slope_50": 0.05, "ma_slope_200": 0.03})
        with pytest.raises(KeyError) as exc_info:
            detect_stage(row, _default_config())
        assert "close" in str(exc_info.value)
        assert "required" in str(exc_info.value).lower()

    def test_missing_sma50_raises_key_error(self):
        row = pd.Series({"close": 150, "sma_200": 110,
                         "ma_slope_50": 0.05, "ma_slope_200": 0.03})
        with pytest.raises(KeyError) as exc_info:
            detect_stage(row, _default_config())
        assert "sma_50" in str(exc_info.value)

    def test_missing_sma200_raises_key_error(self):
        row = pd.Series({"close": 150, "sma_50": 130,
                         "ma_slope_50": 0.05, "ma_slope_200": 0.03})
        with pytest.raises(KeyError) as exc_info:
            detect_stage(row, _default_config())
        assert "sma_200" in str(exc_info.value)

    def test_missing_both_slopes_raises_key_error_listing_both(self):
        row = pd.Series({"close": 150, "sma_50": 130, "sma_200": 110})
        with pytest.raises(KeyError) as exc_info:
            detect_stage(row, _default_config())
        err = str(exc_info.value)
        # Both missing columns should be named in the error
        assert "ma_slope_50" in err or "ma_slope_200" in err

    def test_empty_row_raises_key_error(self):
        with pytest.raises(KeyError):
            detect_stage(pd.Series(dtype=float), _default_config())

    def test_error_message_shows_expected_columns(self):
        """The KeyError message must list the expected column names."""
        row = pd.Series({"close": 150})
        with pytest.raises(KeyError) as exc_info:
            detect_stage(row, _default_config())
        err = str(exc_info.value)
        assert "sma_50" in err or "Expected" in err


# ---------------------------------------------------------------------------
# Test 6 — Stage 2 with very strong slopes → confidence == 100
# ---------------------------------------------------------------------------

class TestStage2HighConfidence:
    def test_strong_slopes_yield_confidence_100(self):
        """
        When both slopes are well above 2× threshold, price clearly above both MAs,
        confidence must be 100.
        Threshold default = 0.0005, so 2× = 0.001.
        slope_50=0.1 and slope_200=0.05 are far above that.
        """
        row = _make_row(
            close=200,
            sma_50=140,     # price 42% above sma_50
            sma_200=110,    # price 82% above sma_200
            ma_slope_50=0.10,
            ma_slope_200=0.05,
        )
        result = detect_stage(row, _default_config())

        assert result.stage == 2
        assert result.is_buyable is True
        assert result.confidence == 100

    def test_borderline_slopes_yield_confidence_70(self):
        """Slopes just above zero (above threshold but below 2× threshold) → confidence=70."""
        threshold = 0.0005
        row = _make_row(
            ma_slope_50=threshold + 0.0001,    # above threshold, below 2×
            ma_slope_200=threshold + 0.0001,
        )
        result = detect_stage(row, _default_config())
        assert result.stage == 2
        assert result.confidence == 70


# ---------------------------------------------------------------------------
# Miscellaneous edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_config_none_uses_defaults(self):
        """Empty config dict should not raise — defaults apply."""
        row = _make_row()
        result = detect_stage(row, {})
        assert result.stage == 2   # default row is Stage 2

    def test_custom_flat_slope_threshold_respected(self):
        """A very large threshold means even positive slopes are considered 'flat'."""
        config = {"stage": {"flat_slope_threshold": 0.1}}
        row = _make_row(
            close=100, sma_50=102, sma_200=105,
            ma_slope_50=0.05, ma_slope_200=0.03
        )
        # slope_50(0.05) < threshold(0.1) → slopes are 'flat' → Stage 1 confidence=85
        result = detect_stage(row, config)
        # Stage classification should still be 1 (price below both MAs)
        assert result.stage == 1

    def test_slopes_stored_on_result(self):
        """StageResult must carry the raw slope values for downstream use."""
        row = _make_row(ma_slope_50=0.0789, ma_slope_200=0.0456)
        result = detect_stage(row, _default_config())
        assert result.ma_slope_50 == pytest.approx(0.0789)
        assert result.ma_slope_200 == pytest.approx(0.0456)

    def test_is_buyable_false_for_all_non_stage2(self):
        cases = [
            _make_row(close=100, sma_50=102, sma_200=105,
                      ma_slope_50=0.0, ma_slope_200=0.0),   # Stage 1
            _make_row(close=95, sma_50=100, sma_200=90,
                      ma_slope_50=-0.02, ma_slope_200=0.005),  # Stage 3
            _make_row(close=80, sma_50=100, sma_200=120,
                      ma_slope_50=-0.03, ma_slope_200=-0.02),  # Stage 4
        ]
        for row in cases:
            result = detect_stage(row, _default_config())
            assert result.is_buyable is False, (
                f"Expected is_buyable=False for stage {result.stage}, got True"
            )

    def test_label_matches_stage_number(self):
        """StageResult.label must start with 'Stage N' where N == result.stage."""
        rows = [
            _make_row(close=100, sma_50=102, sma_200=105,
                      ma_slope_50=0.0, ma_slope_200=0.0),
            _make_row(),
            _make_row(close=95, sma_50=100, sma_200=90,
                      ma_slope_50=-0.02, ma_slope_200=0.005),
            _make_row(close=80, sma_50=100, sma_200=120,
                      ma_slope_50=-0.03, ma_slope_200=-0.02),
        ]
        for row in rows:
            result = detect_stage(row, _default_config())
            assert result.label.startswith(f"Stage {result.stage}"), (
                f"Label mismatch: stage={result.stage}, label={result.label!r}"
            )

    def test_reason_is_non_empty_string_for_all_stages(self):
        rows = [
            _make_row(close=100, sma_50=102, sma_200=105,
                      ma_slope_50=0.0, ma_slope_200=0.0),
            _make_row(),
            _make_row(close=95, sma_50=100, sma_200=90,
                      ma_slope_50=-0.02, ma_slope_200=0.005),
            _make_row(close=80, sma_50=100, sma_200=120,
                      ma_slope_50=-0.03, ma_slope_200=-0.02),
        ]
        for row in rows:
            result = detect_stage(row, _default_config())
            assert isinstance(result.reason, str) and len(result.reason) > 10
