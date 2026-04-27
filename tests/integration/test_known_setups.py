"""
tests/integration/test_known_setups.py
---------------------------------------
Integration tests that wire ALL rule modules together end-to-end and
verify emergent behaviour on known, hand-crafted stock setups.

These tests use the REAL detect_stage(), check_trend_template(),
qualify_vcp(), and score_symbol() implementations — no mocks.

Test matrix:
  test_stage4_blocked_despite_tt_pass
      Stage 4 MA arrangement → score==0 and quality=="FAIL" even when a
      manually-injected TrendTemplateResult says all 8 conditions pass.
      (Demonstrates that the stage gate is checked inside score_symbol
      independently of TrendTemplateResult.passes.)

  test_stage2_a_plus_e2e
      A well-formed Stage 2 / VCP / breakout row → A+ quality end-to-end.

  test_stage2_low_rs_falls_to_fail
      Stage 2 structurally correct but RS rating = 0 → score too low → FAIL.

  test_score_weights_are_enforced_at_module_load
      SCORE_WEIGHTS assertion does not crash at import time.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from features.vcp import VCPMetrics
from rules.scorer import SCORE_WEIGHTS, SEPAResult, score_symbol
from rules.stage import StageResult, detect_stage
from rules.trend_template import TrendTemplateResult, check_trend_template

# ---------------------------------------------------------------------------
# Config — matches settings.yaml so integration behaviour is realistic
# ---------------------------------------------------------------------------

_CFG: dict = {
    "stage": {"flat_slope_threshold": 0.0005},
    "trend_template": {
        "pct_above_52w_low": 25.0,
        "pct_below_52w_high": 25.0,
        "min_rs_rating": 70,
    },
    "vcp": {
        "min_contractions": 2,
        "max_contractions": 5,
        "require_vol_contraction": True,
        "min_weeks": 3,
        "max_weeks": 52,
        "tightness_pct": 10.0,
        "max_depth_pct": 50.0,
    },
    "entry": {
        "breakout_buffer_pct": 0.001,
        "breakout_vol_threshold": 1.5,
    },
    "stop_loss": {
        "stop_buffer_pct": 0.005,
        "max_risk_pct": 15.0,
        "atr_multiplier": 2.0,
        "fixed_stop_pct": 0.07,
    },
    "risk_reward": {"min_rr_ratio": 2.0},
}

_TODAY = date(2025, 1, 15)
_SYMBOL = "TEST"


# ---------------------------------------------------------------------------
# Row factories — produce realistic pd.Series for each scenario
# ---------------------------------------------------------------------------

def _make_stage4_row() -> pd.Series:
    """
    Construct a row with Stage 4 MA arrangement:
      close(80) < sma_50(100) < sma_200(120), both slopes negative.

    Also includes all columns required by the trend template, stop loss,
    entry trigger, etc., so the full score_symbol() pipeline can run
    without KeyErrors.
    """
    return pd.Series({
        # Stage detection
        "close":        80.0,
        "sma_50":       100.0,
        "sma_150":      105.0,
        "sma_200":      120.0,
        "ma_slope_50":  -0.03,
        "ma_slope_200": -0.02,
        # 52-week range — satisfies TT conditions 6 & 7 to isolate stage gate
        "high_52w":     170.0,
        "low_52w":      50.0,
        # RS rating
        "rs_rating":    85,
        # Entry trigger
        "pivot_high":   200.0,   # far above close → no breakout
        "vol_ratio":    1.0,
        # Volume
        "acc_dist_score": 5.0,
        # Stop loss
        "atr_14":       3.0,
    })


def _make_stage2_ideal_row() -> pd.Series:
    """
    Construct a row representing a pristine Stage 2 / breakout setup:
      - All 5 Stage 2 conditions clearly satisfied.
      - Price breaking above pivot_high with 3× volume.
      - RS rating = 90.
    """
    return pd.Series({
        "close":          150.0,
        "sma_50":         130.0,
        "sma_150":        120.0,
        "sma_200":        110.0,
        "ma_slope_50":    0.08,
        "ma_slope_200":   0.04,
        "high_52w":       155.0,   # close within 25% of 52w high ✓
        "low_52w":        90.0,    # close > 90 * 1.25 = 112.5 ✓
        "rs_rating":      90,
        "pivot_high":     148.0,   # close(150) > 148 * 1.001 = 148.148 → breakout
        "vol_ratio":      3.5,     # well above 1.5× threshold
        "acc_dist_score": 12.0,
        "atr_14":         2.5,
    })


def _ideal_vcp() -> VCPMetrics:
    """Ideal 3-contraction VCP with strong volume dry-up."""
    return VCPMetrics(
        contraction_count=3,
        max_depth_pct=22.0,
        final_depth_pct=7.0,
        vol_contraction_ratio=0.35,
        base_length_weeks=10,
        base_low=85.0,
        is_valid_vcp=True,
        tightness_score=3.5,
    )


def _make_symbol_info(symbol: str, sector: str) -> pd.DataFrame:
    return pd.DataFrame({"symbol": [symbol], "sector": [sector]})


# ---------------------------------------------------------------------------
# Integration test 1 — Stage 4 blocked even if TT says all 8 conditions pass
# ---------------------------------------------------------------------------

class TestStage4BlockedDespiteTtPass:
    """
    Stage 4 stock scores FAIL even if all 8 TT conditions are manually set
    to pass.  This proves the stage gate inside score_symbol() is independent
    of the TrendTemplateResult.
    """

    def test_stage4_blocked_despite_tt_pass(self):
        row = _make_stage4_row()

        # Use detect_stage() for real — it must return Stage 4.
        stage = detect_stage(row, _CFG)
        assert stage.stage == 4, (
            f"Precondition failed: expected Stage 4, got {stage.stage}. "
            f"Row: close={row['close']}, sma_50={row['sma_50']}, "
            f"sma_200={row['sma_200']}, slope_200={row['ma_slope_200']}"
        )

        # Inject a TrendTemplateResult that claims ALL 8 conditions pass.
        # This isolates the stage gate — we're not testing TT here.
        perfect_tt = TrendTemplateResult(
            passes=True,
            conditions_met=8,
            condition_1=True, condition_2=True, condition_3=True,
            condition_4=True, condition_5=True, condition_6=True,
            condition_7=True, condition_8=True,
            details={},
        )

        vcp = _ideal_vcp()
        symbol_info = _make_symbol_info(_SYMBOL, "Technology")

        result = score_symbol(
            symbol=_SYMBOL,
            run_date=_TODAY,
            row=row,
            stage_result=stage,
            tt_result=perfect_tt,
            vcp_metrics=vcp,
            sector_ranks={"Technology": 1},   # would give +5 bonus if stage==2
            symbol_info=symbol_info,
            config=_CFG,
        )

        assert result.stage == 4, f"SEPAResult.stage should be 4, got {result.stage}"
        assert result.score == 0, (
            f"Stage 4 must score 0; got {result.score}"
        )
        assert result.setup_quality == "FAIL", (
            f"Stage 4 must be FAIL; got {result.setup_quality!r}"
        )

    def test_stage4_real_tt_also_fails(self):
        """
        Running the actual check_trend_template() on a Stage 4 row should
        also fail multiple conditions (price < SMA_200, etc.) — consistent
        with the stage gate producing score=0.
        """
        row = _make_stage4_row()
        stage = detect_stage(row, _CFG)
        tt = check_trend_template(row, _CFG)

        assert stage.stage == 4
        assert tt.passes is False, (
            "A genuine Stage 4 row cannot pass all 8 TT conditions"
        )

        result = score_symbol(
            symbol=_SYMBOL,
            run_date=_TODAY,
            row=row,
            stage_result=stage,
            tt_result=tt,
            vcp_metrics=_ideal_vcp(),
            sector_ranks={},
            symbol_info=_make_symbol_info(_SYMBOL, "Financials"),
            config=_CFG,
        )
        assert result.score == 0
        assert result.setup_quality == "FAIL"


# ---------------------------------------------------------------------------
# Integration test 2 — Ideal Stage 2 setup reaches A+ quality end-to-end
# ---------------------------------------------------------------------------

class TestStage2APlus:
    """A well-formed Stage 2 / VCP / breakout row reaches A+ quality."""

    def test_a_plus_quality_e2e(self):
        row = _make_stage2_ideal_row()

        stage = detect_stage(row, _CFG)
        assert stage.stage == 2, f"Precondition: expected Stage 2, got {stage.stage}"

        tt = check_trend_template(row, _CFG)

        result = score_symbol(
            symbol=_SYMBOL,
            run_date=_TODAY,
            row=row,
            stage_result=stage,
            tt_result=tt,
            vcp_metrics=_ideal_vcp(),
            sector_ranks={"Technology": 2},  # top-5 → +5 bonus
            symbol_info=_make_symbol_info(_SYMBOL, "Technology"),
            config=_CFG,
        )

        assert result.stage == 2
        assert result.score >= 85, f"Expected A+ score ≥ 85, got {result.score}"
        assert result.setup_quality == "A+"
        assert result.breakout_triggered is True
        assert result.entry_price is not None
        assert result.stop_loss is not None

    def test_stage2_score_is_positive(self):
        row = _make_stage2_ideal_row()
        stage = detect_stage(row, _CFG)
        tt = check_trend_template(row, _CFG)

        result = score_symbol(
            symbol=_SYMBOL,
            run_date=_TODAY,
            row=row,
            stage_result=stage,
            tt_result=tt,
            vcp_metrics=_ideal_vcp(),
            sector_ranks={},
            symbol_info=_make_symbol_info(_SYMBOL, "Technology"),
            config=_CFG,
        )
        assert result.score > 0


# ---------------------------------------------------------------------------
# Integration test 3 — Stage 2 with rs_rating=0 → too low to grade well
# ---------------------------------------------------------------------------

class TestLowRsStage2:
    """Stage 2 stock with rs_rating=0 produces a score that may reach FAIL or C."""

    def test_low_rs_suppresses_score(self):
        """rs_rating=0 → rs component is 0; weighted score is significantly lower."""
        row_high_rs = _make_stage2_ideal_row()
        row_low_rs  = _make_stage2_ideal_row().copy()
        row_low_rs["rs_rating"] = 0

        stage = detect_stage(row_high_rs, _CFG)
        tt = check_trend_template(row_high_rs, _CFG)
        vcp = _ideal_vcp()
        symbol_info = _make_symbol_info(_SYMBOL, "Technology")

        result_high = score_symbol(
            symbol=_SYMBOL, run_date=_TODAY, row=row_high_rs,
            stage_result=stage, tt_result=tt, vcp_metrics=vcp,
            sector_ranks={}, symbol_info=symbol_info, config=_CFG,
        )
        result_low = score_symbol(
            symbol=_SYMBOL, run_date=_TODAY, row=row_low_rs,
            stage_result=stage, tt_result=tt, vcp_metrics=vcp,
            sector_ranks={}, symbol_info=symbol_info, config=_CFG,
        )

        # rs weight=0.30, diff=90 points → 27-point reduction
        assert result_low.score < result_high.score, (
            f"Low RS ({result_low.score}) should score less than high RS ({result_high.score})"
        )

    def test_zero_rs_quality_is_not_a_plus(self):
        row = _make_stage2_ideal_row().copy()
        row["rs_rating"] = 0
        stage = detect_stage(row, _CFG)
        tt = check_trend_template(row, _CFG)

        result = score_symbol(
            symbol=_SYMBOL, run_date=_TODAY, row=row,
            stage_result=stage, tt_result=tt,
            vcp_metrics=_ideal_vcp(),
            sector_ranks={},
            symbol_info=_make_symbol_info(_SYMBOL, "Technology"),
            config=_CFG,
        )
        assert result.setup_quality != "A+", (
            f"rs_rating=0 should not produce A+ (got {result.setup_quality!r})"
        )


# ---------------------------------------------------------------------------
# Integration test 4 — SCORE_WEIGHTS assertion survives import
# ---------------------------------------------------------------------------

class TestModuleIntegrity:
    def test_score_weights_sum_to_one(self):
        """The module-level assertion must not fire — weights sum to 1.0."""
        total = sum(SCORE_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}"

    def test_sepa_result_importable(self):
        """SEPAResult must be importable at module level (pipeline.py requirement)."""
        from rules.scorer import SEPAResult as _SEPAResult  # noqa: F401
        assert _SEPAResult is not None
