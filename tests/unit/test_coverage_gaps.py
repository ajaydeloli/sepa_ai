"""
tests/unit/test_coverage_gaps.py
----------------------------------
Targeted gap-fill tests to push critical-path modules to 100 % line coverage.

Modules addressed (in order of appearance in coverage report):
  - features/relative_strength.py  (run_rs_rating_pass error branches)
  - features/vcp.py                (_apply_vcp_rules early-exit branches; edge cases)
  - rules/scorer.py                (vol_ratio 0.5-0.8 branch; C-grade quality)
  - rules/stage.py                 (_stage1_confidence partial branch; _stage3_confidence 85)
  - rules/stop_loss.py             (ATR fallback path)
  - rules/trend_template.py        (_safe_float NaN / non-numeric log branches)
  - screener/pre_filter.py         (missing file + missing columns in build_features_index)

Every test is self-contained (tmp_path fixture for any I/O, no real HTTP).
"""
from __future__ import annotations

import math
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest


# ===========================================================================
# 1. features/relative_strength.py — run_rs_rating_pass error branches
# ===========================================================================

class TestRunRsRatingPassErrorPaths:
    """Cover InsufficientDataError + generic Exception branches in run_rs_rating_pass."""

    def _make_benchmark(self, n: int = 300) -> pd.DataFrame:
        dates = pd.bdate_range(start="2020-01-02", periods=n)
        return pd.DataFrame({"close": 18000.0 + np.arange(n) * 0.5}, index=dates)

    def _make_config(self, tmp_path: Path) -> dict:
        return {
            "data": {"processed_dir": str(tmp_path / "processed"),
                     "features_dir":  str(tmp_path / "features")},
            "rs":   {"period": 63},
        }

    def test_insufficient_data_assigns_zero_rating(self, tmp_path):
        """Symbols with < 65 processed rows get rating 0 (InsufficientDataError path)."""
        from features.relative_strength import run_rs_rating_pass

        cfg = self._make_config(tmp_path)
        proc_dir = Path(cfg["data"]["processed_dir"])
        proc_dir.mkdir(parents=True)

        # Write a file that is too short (only 10 rows)
        short_df = pd.DataFrame(
            {"close": range(10), "open": range(10),
             "high": range(10), "low": range(10), "volume": range(10)},
            index=pd.bdate_range("2024-01-01", periods=10),
        )
        short_df.to_parquet(proc_dir / "SHORTIE.parquet")

        bm = self._make_benchmark()
        ratings = run_rs_rating_pass(["SHORTIE"], date(2025, 1, 1), cfg, bm)

        assert "SHORTIE" in ratings
        assert ratings["SHORTIE"] == 0, (
            f"Symbol with insufficient rows must get rating 0, got {ratings['SHORTIE']}"
        )

    def test_missing_file_assigns_zero_rating(self, tmp_path):
        """Symbol with no processed file → generic Exception → rating 0."""
        from features.relative_strength import run_rs_rating_pass

        cfg = self._make_config(tmp_path)
        Path(cfg["data"]["processed_dir"]).mkdir(parents=True)

        bm = self._make_benchmark()
        # "NOSUCHFILE" has no parquet — triggers file-not-found exception branch
        ratings = run_rs_rating_pass(["NOSUCHFILE"], date(2025, 1, 1), cfg, bm)

        assert "NOSUCHFILE" in ratings
        assert ratings["NOSUCHFILE"] == 0

    def test_mix_valid_and_invalid_symbols(self, tmp_path):
        """Valid symbols get real ratings; invalid ones get 0."""
        from features.relative_strength import run_rs_rating_pass

        cfg = self._make_config(tmp_path)
        proc_dir = Path(cfg["data"]["processed_dir"])
        proc_dir.mkdir(parents=True)

        # Write a valid 300-row file for GOODSYM
        n = 300
        close = 100.0 * (1.003 ** np.arange(n))
        good_df = pd.DataFrame(
            {"close": close, "open": close, "high": close, "low": close,
             "volume": np.ones(n) * 1e6},
            index=pd.bdate_range("2020-01-02", periods=n),
        )
        good_df.to_parquet(proc_dir / "GOODSYM.parquet")

        bm = self._make_benchmark()
        ratings = run_rs_rating_pass(["GOODSYM", "BADSYM"], date(2025, 1, 1), cfg, bm)

        assert ratings["BADSYM"] == 0
        assert isinstance(ratings["GOODSYM"], int)
        assert 0 <= ratings["GOODSYM"] <= 99


# ===========================================================================
# 2. features/vcp.py — uncovered branches in _apply_vcp_rules and helpers
# ===========================================================================

class TestVcpApplyRulesBranches:
    """Hit the early-exit conditions in _apply_vcp_rules not yet covered."""

    def _base_metrics(self, **overrides):
        from features.vcp import VCPMetrics
        defaults = dict(
            contraction_count=3, max_depth_pct=25.0, final_depth_pct=8.0,
            vol_contraction_ratio=0.6, base_length_weeks=8, base_low=100.0,
            is_valid_vcp=True, tightness_score=4.0,
        )
        defaults.update(overrides)
        return VCPMetrics(**defaults)

    _CFG = {
        "vcp": {
            "min_contractions": 2, "max_contractions": 5,
            "require_vol_contraction": True,
            "min_weeks": 3, "max_weeks": 52,
            "tightness_pct": 10.0, "max_depth_pct": 50.0,
            "require_declining_depth": True,
        }
    }

    def test_fails_when_vol_not_contracting(self):
        """require_vol_contraction=True and vol_ratio >= 1.0 → False."""
        from features.vcp import _apply_vcp_rules
        m = self._base_metrics(vol_contraction_ratio=1.5)
        assert _apply_vcp_rules(m, self._CFG) is False

    def test_fails_when_below_min_weeks(self):
        """base_length_weeks < min_weeks → False."""
        from features.vcp import _apply_vcp_rules
        m = self._base_metrics(base_length_weeks=1)  # below min_weeks=3
        assert _apply_vcp_rules(m, self._CFG) is False

    def test_fails_when_above_max_weeks(self):
        """base_length_weeks > max_weeks → False."""
        from features.vcp import _apply_vcp_rules
        m = self._base_metrics(base_length_weeks=60)  # above max_weeks=52
        assert _apply_vcp_rules(m, self._CFG) is False

    def test_fails_when_tightness_nan(self):
        """tightness_score=NaN → False (isnan check path)."""
        from features.vcp import _apply_vcp_rules
        m = self._base_metrics(tightness_score=float("nan"))
        assert _apply_vcp_rules(m, self._CFG) is False

    def test_fails_when_tightness_exceeds_threshold(self):
        """tightness_score >= tightness_pct → False."""
        from features.vcp import _apply_vcp_rules
        m = self._base_metrics(tightness_score=15.0)  # >= 10.0
        assert _apply_vcp_rules(m, self._CFG) is False

    def test_fails_when_max_depth_too_large(self):
        """max_depth_pct > config max_depth_pct → False."""
        from features.vcp import _apply_vcp_rules
        m = self._base_metrics(max_depth_pct=55.0)  # > 50.0
        assert _apply_vcp_rules(m, self._CFG) is False

    def test_fails_when_final_depth_not_shallower_than_max(self):
        """final_depth_pct >= max_depth_pct violates 'each leg shallower' rule."""
        from features.vcp import _apply_vcp_rules
        m = self._base_metrics(final_depth_pct=30.0, max_depth_pct=25.0)
        assert _apply_vcp_rules(m, self._CFG) is False

    def test_vol_ratio_nan_when_first_avg_zero(self):
        """_vol_ratio returns nan when first leg has zero average volume."""
        from features.vcp import RuleBasedVCPDetector

        n = 40
        dates = pd.bdate_range("2023-01-02", periods=n)
        df = pd.DataFrame({
            "open": np.ones(n) * 100, "high": np.ones(n) * 101,
            "low": np.ones(n) * 99,  "close": np.ones(n) * 100,
            "volume": np.zeros(n),   # zero volume → first_avg == 0
        }, index=dates)

        legs = [
            {"start_idx": 0, "end_idx": 9,  "high_price": 101, "low_price": 99, "depth": 2},
            {"start_idx": 10, "end_idx": 19, "high_price": 100.5, "low_price": 99.5, "depth": 1},
        ]
        result = RuleBasedVCPDetector._vol_ratio(df, legs)
        assert math.isnan(result), f"Expected nan when first_avg=0, got {result}"

    def test_compute_raises_configuration_error_for_unknown_detector(self):
        """get_detector raises ConfigurationError for unknown detector name."""
        from features.vcp import get_detector
        from utils.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError):
            get_detector({"vcp": {"detector": "nonexistent_detector"}})

    def test_compute_fills_nan_on_detection_exception(self):
        """vcp.compute fills NaN columns when detector raises."""
        from features.vcp import compute

        n = 50
        df = pd.DataFrame({
            "open": np.ones(n), "high": np.ones(n),
            "low": np.ones(n),  "close": np.ones(n),
            "volume": np.zeros(n),
        }, index=pd.bdate_range("2023-01-02", periods=n))

        # Force detection to raise by using a bad config that triggers ConfigurationError
        bad_cfg = {"vcp": {"detector": "no_such_detector"}}
        result = compute(df.copy(), bad_cfg)

        assert "vcp_valid" in result.columns
        assert result["vcp_valid"].iloc[-1] == False  # noqa: E712 — explicit False check


# ===========================================================================
# 3. rules/scorer.py — vol_ratio 0.5-0.8 branch; C-grade quality
# ===========================================================================

class TestScorerGaps:
    """Cover _compute_vcp_score vol_ratio branch and C-grade _determine_quality."""

    def test_compute_vcp_score_vol_ratio_between_05_and_08(self):
        """When vol_contraction_ratio is in (0.5, 0.8), vol_bonus should be 10."""
        from features.vcp import VCPMetrics
        from rules.scorer import _compute_vcp_score

        m = VCPMetrics(
            contraction_count=3, max_depth_pct=25.0, final_depth_pct=8.0,
            vol_contraction_ratio=0.65,   # between 0.5 and 0.8 → vol_bonus=10
            base_length_weeks=8, base_low=100.0,
            is_valid_vcp=True, tightness_score=4.0,
        )
        score = _compute_vcp_score(m)

        # base 60 + contraction_bonus=(3-0)*10=30 + vol_bonus=10 = 100, capped at 100
        # BUT bonus = min(40, 30+10) = 40 → score = 60 + 40 = 100
        # With 3 contractions: bonus = (3 - |3-3|) * 10 = 30; +vol_bonus=10 → 40 → score=100
        assert score == 100.0, f"Expected 100.0, got {score}"

    def test_compute_vcp_score_vol_ratio_exactly_05_gets_bonus_20(self):
        """vol_ratio < 0.5 → vol_bonus=20."""
        from features.vcp import VCPMetrics
        from rules.scorer import _compute_vcp_score

        m = VCPMetrics(
            contraction_count=2, max_depth_pct=20.0, final_depth_pct=8.0,
            vol_contraction_ratio=0.3,    # < 0.5 → vol_bonus=20
            base_length_weeks=6, base_low=90.0,
            is_valid_vcp=True, tightness_score=3.0,
        )
        score = _compute_vcp_score(m)
        # contraction_bonus = (3 - |2-3|) * 10 = 20; vol_bonus=20 → bonus=min(40,40)=40
        assert score == 100.0

    def test_determine_quality_c_grade(self):
        """score ≥ 40 with stage==2 but conditions_met < 6 → quality C."""
        from rules.scorer import _determine_quality

        q = _determine_quality(score=45, stage=2, conditions_met=5, vcp_qualified=False)
        assert q == "C", f"Expected 'C', got {q!r}"

    def test_determine_quality_fail_non_stage2(self):
        """Any stage != 2 → FAIL regardless of score."""
        from rules.scorer import _determine_quality

        for stage in (1, 3, 4):
            q = _determine_quality(score=99, stage=stage, conditions_met=8, vcp_qualified=True)
            assert q == "FAIL", f"stage={stage}: expected FAIL, got {q!r}"

    def test_determine_quality_fail_score_below_40(self):
        """score < 40 with stage==2 → FAIL."""
        from rules.scorer import _determine_quality

        q = _determine_quality(score=30, stage=2, conditions_met=8, vcp_qualified=True)
        assert q == "FAIL"


# ===========================================================================
# 4. rules/stage.py — uncovered confidence branches
# ===========================================================================

class TestStageGaps:
    """Cover _stage1_confidence partial-match branch and _stage3_confidence 85."""

    def test_stage1_confidence_partial_match_returns_70(self):
        """_stage1_confidence returns 70 when only one of (both_flat, below_both) holds."""
        from rules.stage import _stage1_confidence

        # both_flat=True, below_both=False → partial → 70
        conf = _stage1_confidence(
            close=105.0, sma_50=100.0, sma_200=98.0,
            slope_50=0.0001, slope_200=0.0001,   # near zero → both_flat=True
            threshold=0.0005,
        )
        assert conf == 70, f"Expected 70, got {conf}"

    def test_stage1_confidence_both_flat_and_below_returns_85(self):
        """_stage1_confidence returns 85 when both_flat AND below_both."""
        from rules.stage import _stage1_confidence

        conf = _stage1_confidence(
            close=90.0, sma_50=100.0, sma_200=98.0,  # below both MAs
            slope_50=0.0001, slope_200=0.0001,         # flat slopes
            threshold=0.0005,
        )
        assert conf == 85, f"Expected 85, got {conf}"

    def test_stage1_confidence_neither_returns_60(self):
        """_stage1_confidence returns 60 when neither condition holds."""
        from rules.stage import _stage1_confidence

        conf = _stage1_confidence(
            close=105.0, sma_50=100.0, sma_200=98.0,  # above both → below_both=False
            slope_50=0.01, slope_200=0.01,              # not flat → both_flat=False
            threshold=0.0005,
        )
        assert conf == 60, f"Expected 60, got {conf}"

    def test_stage3_confidence_all_three_returns_85(self):
        """_stage3_confidence returns 85 when price below 50, above 200, slope_50 < 0."""
        from rules.stage import _stage3_confidence

        conf = _stage3_confidence(
            close=105.0, sma_50=110.0, sma_200=100.0,  # below_50, above_200
            slope_50=-0.01, slope_200=0.001,
        )
        assert conf == 85, f"Expected 85, got {conf}"

    def test_stage3_confidence_below_and_above_no_negative_slope_returns_70(self):
        """_stage3_confidence returns 70 when below_50 and above_200 but slope_50 >= 0."""
        from rules.stage import _stage3_confidence

        conf = _stage3_confidence(
            close=105.0, sma_50=110.0, sma_200=100.0,
            slope_50=0.001,   # positive → sma50_declining=False
            slope_200=0.001,
        )
        assert conf == 70, f"Expected 70, got {conf}"

    def test_detect_stage_returns_stage3_when_lost_sma50_above_sma200(self):
        """detect_stage returns Stage 3 when price is below SMA50 but above SMA200."""
        from rules.stage import detect_stage

        row = pd.Series({
            "close": 105.0, "sma_50": 110.0, "sma_200": 100.0,
            "ma_slope_50": -0.01, "ma_slope_200": 0.001,
        })
        cfg = {"stage": {"flat_slope_threshold": 0.0005}}
        result = detect_stage(row, cfg)
        assert result.stage == 3
        assert result.is_buyable is False

    def test_detect_stage_missing_columns_raises_key_error(self):
        """Missing required columns must raise KeyError with informative message."""
        from rules.stage import detect_stage

        row = pd.Series({"close": 100.0})   # missing sma_50, sma_200, ma_slope_*
        with pytest.raises(KeyError):
            detect_stage(row, {})


# ===========================================================================
# 5. rules/stop_loss.py — ATR fallback and fixed-pct paths
# ===========================================================================

class TestStopLossGaps:
    """Cover ATR-fallback and fixed-% paths in compute_stop_loss."""

    _CFG = {"stop_loss": {
        "stop_buffer_pct": 0.005, "max_risk_pct": 5.0,
        "atr_multiplier": 2.0,   "fixed_stop_pct": 0.07,
    }}

    def test_atr_fallback_when_vcp_risk_too_large(self):
        """VCP base_low exists but risk > max_risk_pct → falls back to ATR method."""
        from rules.stop_loss import compute_stop_loss

        close = 100.0
        # vcp_base_low so far below close that risk > 5 % → triggers fallback
        vcp_base_low = 50.0   # risk ≈ 50 % — way above max_risk_pct=5%

        row = pd.Series({"close": close, "atr_14": 2.0})
        stop, risk, method = compute_stop_loss(row, vcp_base_low, self._CFG)

        assert method == "atr", f"Expected 'atr' fallback, got {method!r}"
        assert stop is not None
        assert stop == pytest.approx(close - 2.0 * 2.0, abs=0.01)

    def test_atr_fallback_when_no_vcp_base_low(self):
        """vcp_base_low=None with valid ATR → ATR method."""
        from rules.stop_loss import compute_stop_loss

        row = pd.Series({"close": 150.0, "atr_14": 3.0})
        stop, risk, method = compute_stop_loss(row, None, self._CFG)

        assert method == "atr"
        assert stop == pytest.approx(150.0 - 2.0 * 3.0, abs=0.01)

    def test_fixed_pct_fallback_when_no_atr(self):
        """No vcp_base_low, ATR is NaN → fixed % fallback."""
        from rules.stop_loss import compute_stop_loss

        row = pd.Series({"close": 200.0, "atr_14": float("nan")})
        stop, risk, method = compute_stop_loss(row, None, self._CFG)

        assert method == "pct"
        assert stop == pytest.approx(200.0 * (1.0 - 0.07), abs=0.01)

    def test_no_data_when_close_is_nan(self):
        """close=NaN → returns (None, None, 'no_data')."""
        from rules.stop_loss import compute_stop_loss

        row = pd.Series({"close": float("nan"), "atr_14": 2.0})
        stop, risk, method = compute_stop_loss(row, None, self._CFG)

        assert stop is None
        assert risk is None
        assert method == "no_data"

    def test_no_data_when_close_is_zero(self):
        """close=0 → returns (None, None, 'no_data')."""
        from rules.stop_loss import compute_stop_loss

        row = pd.Series({"close": 0.0, "atr_14": 2.0})
        stop, risk, method = compute_stop_loss(row, None, self._CFG)
        assert method == "no_data"


# ===========================================================================
# 6. rules/trend_template.py — _safe_float warning paths
# ===========================================================================

class TestTrendTemplateGaps:
    """Cover the _safe_float warning branches (non-numeric, NaN, missing col)."""

    _CFG = {"trend_template": {
        "pct_above_52w_low": 25.0, "pct_below_52w_high": 25.0, "min_rs_rating": 70,
    }}

    def _full_row(self, **overrides) -> pd.Series:
        """Return a row with all required columns at valid Stage-2 values."""
        base = {
            "close": 150.0, "sma_50": 130.0, "sma_150": 120.0, "sma_200": 110.0,
            "ma_slope_200": 0.04, "high_52w": 160.0, "low_52w": 90.0,
            "rs_rating": 85.0,
        }
        base.update(overrides)
        return pd.Series(base)

    def test_missing_column_gives_false_condition(self):
        """A missing required column must yield False for all dependent conditions."""
        from rules.trend_template import check_trend_template

        # Drop sma_150 → conditions 1, 2, 4 depend on it → all False
        row = pd.Series({
            "close": 150.0, "sma_50": 130.0,
            "sma_200": 110.0, "ma_slope_200": 0.04,
            "high_52w": 160.0, "low_52w": 90.0,
            "rs_rating": 85.0,
            # sma_150 intentionally absent
        })
        result = check_trend_template(row, self._CFG)

        assert result.condition_1 is False
        assert result.condition_2 is False
        assert result.passes is False

    def test_non_numeric_column_gives_false_condition(self):
        """A column with a non-numeric value → _safe_float logs warning, returns False."""
        from rules.trend_template import check_trend_template

        row = self._full_row(rs_rating="NOT_A_NUMBER")
        result = check_trend_template(row, self._CFG)

        assert result.condition_8 is False, (
            "Non-numeric rs_rating must make condition_8 False"
        )

    def test_nan_column_gives_false_condition(self):
        """A NaN column value → _safe_float logs warning, returns (None, False)."""
        from rules.trend_template import check_trend_template

        row = self._full_row(ma_slope_200=float("nan"))
        result = check_trend_template(row, self._CFG)

        assert result.condition_3 is False, (
            "NaN ma_slope_200 must make condition_3 False"
        )

    def test_all_valid_row_passes_all_8(self):
        """A clean Stage-2 row must pass all 8 conditions."""
        from rules.trend_template import check_trend_template

        row = self._full_row()
        result = check_trend_template(row, self._CFG)

        assert result.passes is True
        assert result.conditions_met == 8

    def test_missing_close_makes_conditions_1_5_false(self):
        """Missing 'close' column → conditions 1, 5 (and anything depending on close) → False."""
        from rules.trend_template import check_trend_template

        row = pd.Series({
            "sma_50": 130.0, "sma_150": 120.0, "sma_200": 110.0,
            "ma_slope_200": 0.04, "high_52w": 160.0, "low_52w": 90.0,
            "rs_rating": 85.0,
            # close absent
        })
        result = check_trend_template(row, self._CFG)
        assert result.condition_1 is False
        assert result.condition_5 is False


# ===========================================================================
# 7. screener/pre_filter.py — build_features_index missing-file & missing-cols
# ===========================================================================

class TestBuildFeaturesIndexGaps:
    """Cover warning branches in build_features_index."""

    def _make_config(self, tmp_path: Path) -> dict:
        return {
            "data": {
                "features_dir":  str(tmp_path / "features"),
                "processed_dir": str(tmp_path / "processed"),
            }
        }

    def test_missing_feature_file_symbol_skipped(self, tmp_path):
        """Symbols with no feature parquet are silently omitted from the index."""
        from screener.pre_filter import build_features_index

        cfg = self._make_config(tmp_path)
        Path(cfg["data"]["features_dir"]).mkdir(parents=True)
        Path(cfg["data"]["processed_dir"]).mkdir(parents=True)

        # NO parquet files written → symbol should be skipped
        index = build_features_index(["NOFEATFILE"], cfg)
        assert "NOFEATFILE" not in index

    def test_feature_file_missing_required_columns_skipped(self, tmp_path):
        """Feature file that lacks close/sma_200/rs_rating columns is skipped."""
        from screener.pre_filter import build_features_index

        cfg = self._make_config(tmp_path)
        feat_dir = Path(cfg["data"]["features_dir"])
        proc_dir = Path(cfg["data"]["processed_dir"])
        feat_dir.mkdir(parents=True)
        proc_dir.mkdir(parents=True)

        # Write a feature file with ONLY 'open' — no close/sma_200/rs_rating
        bad_df = pd.DataFrame(
            {"open": [100.0] * 10},
            index=pd.bdate_range("2024-01-02", periods=10),
        )
        bad_df.to_parquet(feat_dir / "BADCOLS.parquet")

        index = build_features_index(["BADCOLS"], cfg)
        assert "BADCOLS" not in index, (
            "Symbol with missing required feature columns must be excluded from index"
        )

    def test_missing_processed_file_skipped(self, tmp_path):
        """Symbol with feature file but no processed file (for high_52w) is skipped."""
        from screener.pre_filter import build_features_index

        cfg = self._make_config(tmp_path)
        feat_dir = Path(cfg["data"]["features_dir"])
        proc_dir = Path(cfg["data"]["processed_dir"])
        feat_dir.mkdir(parents=True)
        proc_dir.mkdir(parents=True)

        # Write a valid feature file
        feat_df = pd.DataFrame(
            {"close": [150.0], "sma_200": [110.0], "rs_rating": [85.0]},
            index=pd.bdate_range("2024-06-01", periods=1),
        )
        feat_df.to_parquet(feat_dir / "NOPROC.parquet")
        # NO processed file → high_52w cannot be derived → symbol skipped

        index = build_features_index(["NOPROC"], cfg)
        assert "NOPROC" not in index

    def test_valid_symbol_appears_in_index(self, tmp_path):
        """A symbol with both feature and processed files appears in the index."""
        from screener.pre_filter import build_features_index

        cfg = self._make_config(tmp_path)
        feat_dir = Path(cfg["data"]["features_dir"])
        proc_dir = Path(cfg["data"]["processed_dir"])
        feat_dir.mkdir(parents=True)
        proc_dir.mkdir(parents=True)

        n = 10
        dates = pd.bdate_range("2024-01-02", periods=n)

        feat_df = pd.DataFrame(
            {"close": [150.0] * n, "sma_200": [110.0] * n, "rs_rating": [85.0] * n},
            index=dates,
        )
        feat_df.to_parquet(feat_dir / "VALIDFEAT.parquet")

        proc_df = pd.DataFrame(
            {"open": [148.0] * n, "high": [155.0] * n,
             "low": [145.0] * n, "close": [150.0] * n, "volume": [1e6] * n},
            index=dates,
        )
        proc_df.to_parquet(proc_dir / "VALIDFEAT.parquet")

        index = build_features_index(["VALIDFEAT"], cfg)
        assert "VALIDFEAT" in index
        assert index["VALIDFEAT"]["close"] == pytest.approx(150.0)
        assert index["VALIDFEAT"]["high_52w"] == pytest.approx(155.0)
