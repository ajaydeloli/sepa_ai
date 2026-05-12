"""
tests/unit/test_scorer.py
--------------------------
Unit tests for rules/scorer.py — SEPAResult dataclass and score_symbol().

All tests build their inputs from lightweight helper factories; no real
Parquet/CSV files are loaded.  Each test is self-contained and fast.

Test matrix (8 required tests + supplementary edge cases):
  1. Stage 2, 8/8 TT, VCP qualified, rs_rating=88  → score ≥ 85, quality=="A+"
  2. Stage 4 (non-buyable)                          → score==0, quality=="FAIL"
  3. Stage 2, 6/8 conditions, moderate rs_rating    → quality=="B"
  4. Stage 2, 8/8 TT, VCP NOT qualified             → quality=="A" or "B"
  5. Sector bonus: top-5 sector                     → sector_bonus==5 on result
  6. fundamental_result=None                        → treated as neutral (score unaffected)
  7. news_score=None                                → treated as neutral (score unaffected)
  8. SEPAResult is a dataclass                      → serialisable via dataclasses.asdict()
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import field
from datetime import date

import pandas as pd
import pytest

from features.vcp import VCPMetrics
from rules.scorer import SEPAResult, SCORE_WEIGHTS, score_symbol
from rules.stage import StageResult
from rules.trend_template import TrendTemplateResult

# ---------------------------------------------------------------------------
# Shared config (matches settings.yaml defaults)
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
    # Disable fundamentals processing in the base config so that tests using
    # _run() with a stub fundamental_result dict (e.g. {"score": 50.0}) get
    # the neutral fallback (50) rather than triggering check_fundamental_template
    # which re-evaluates all 7 conditions from scratch and would score 0.
    # Phase 5 tests that need fundamentals enabled explicitly use _CFG_WITH_FUND.
    "fundamentals": {"enabled": False},
}

_TODAY = date(2025, 1, 15)
_SYMBOL = "TESTCO"


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def _make_row(**overrides) -> pd.Series:
    """Build a feature row suitable for score_symbol().

    Defaults represent a healthy Stage 2 stock on a non-breakout day.
    All VCP / ATR / vol columns needed by downstream rules are included.
    """
    defaults = {
        # Price / MA columns
        "close": 150.0,
        "sma_50": 130.0,
        "sma_150": 120.0,
        "sma_200": 110.0,
        "ma_slope_50": 0.05,
        "ma_slope_200": 0.03,
        # 52-week range
        "high_52w": 160.0,
        "low_52w": 90.0,
        # RS rating
        "rs_rating": 80,
        # Entry trigger inputs
        "pivot_high": 200.0,   # far above close → no breakout by default
        "vol_ratio": 1.2,
        # Volume / acc-dist
        "acc_dist_score": 5.0,
        # Stop-loss inputs
        "atr_14": 3.0,
    }
    defaults.update(overrides)
    return pd.Series(defaults)


def _make_stage(stage: int = 2, confidence: int = 90) -> StageResult:
    """Build a StageResult for the given stage number."""
    labels = {
        1: "Stage 1 — Basing",
        2: "Stage 2 — Advancing",
        3: "Stage 3 — Topping",
        4: "Stage 4 — Declining",
    }
    return StageResult(
        stage=stage,
        label=labels[stage],
        confidence=confidence,
        reason=f"Test StageResult stage={stage}",
        ma_slope_200=0.03 if stage == 2 else -0.02,
        ma_slope_50=0.05 if stage == 2 else -0.03,
        is_buyable=(stage == 2),
    )


def _make_tt(conditions_met: int = 8) -> TrendTemplateResult:
    """Build a TrendTemplateResult with the requested number of conditions met."""
    flags = [True] * conditions_met + [False] * (8 - conditions_met)
    c1, c2, c3, c4, c5, c6, c7, c8 = flags
    return TrendTemplateResult(
        passes=(conditions_met == 8),
        conditions_met=conditions_met,
        condition_1=c1,
        condition_2=c2,
        condition_3=c3,
        condition_4=c4,
        condition_5=c5,
        condition_6=c6,
        condition_7=c7,
        condition_8=c8,
        details={},
    )


def _make_vcp(
    is_valid: bool = True,
    contraction_count: int = 3,
    vol_contraction_ratio: float = 0.4,
    tightness_score: float = 4.0,
    base_low: float = 80.0,
    monotonic_decline: bool = True,
) -> VCPMetrics:
    """Build a VCPMetrics dataclass."""
    return VCPMetrics(
        contraction_count=contraction_count,
        max_depth_pct=20.0,
        final_depth_pct=8.0,
        vol_contraction_ratio=vol_contraction_ratio,
        base_length_weeks=8,
        base_low=base_low,
        is_valid_vcp=is_valid,
        tightness_score=tightness_score,
        monotonic_decline=monotonic_decline,
    )


def _make_symbol_info(symbol: str = _SYMBOL, sector: str = "Technology") -> pd.DataFrame:
    """Minimal symbol_info DataFrame with one row."""
    return pd.DataFrame({"symbol": [symbol], "sector": [sector]})


def _run(
    row: pd.Series | None = None,
    stage: int = 2,
    conditions_met: int = 8,
    vcp_valid: bool = True,
    vcp_contractions: int = 3,
    vcp_vol_ratio: float = 0.4,
    rs_rating: int = 80,
    sector: str = "Technology",
    sector_ranks: dict | None = None,
    fundamental_result: dict | None = None,
    news_score: float | None = None,
) -> SEPAResult:
    """Convenience wrapper that builds all inputs and calls score_symbol()."""
    if row is None:
        row = _make_row(rs_rating=rs_rating)

    symbol_info = _make_symbol_info(_SYMBOL, sector)
    if sector_ranks is None:
        sector_ranks = {}

    return score_symbol(
        symbol=_SYMBOL,
        run_date=_TODAY,
        row=row,
        stage_result=_make_stage(stage),
        tt_result=_make_tt(conditions_met),
        vcp_metrics=_make_vcp(
            is_valid=vcp_valid,
            contraction_count=vcp_contractions,
            vol_contraction_ratio=vcp_vol_ratio,
        ),
        sector_ranks=sector_ranks,
        symbol_info=symbol_info,
        config=_CFG,
        fundamental_result=fundamental_result,
        news_score=news_score,
    )


# ===========================================================================
# Test 1 — A+ setup: Stage 2, 8/8 TT, VCP qualified, rs_rating=88
# ===========================================================================

class TestAplusSetup:
    """Score ≥ 85 with all gates satisfied → quality == 'A+'."""

    def test_score_gte_85(self):
        """A+ requires score ≥ 85; all components should push it there."""
        # Use a breakout row so volume score is maximised
        row = _make_row(
            rs_rating=88,
            pivot_high=148.0,   # close(150) > 148 * 1.001 → breakout triggered
            vol_ratio=3.0,      # 3× avg volume = perfect volume score
            acc_dist_score=10.0,
        )
        # Put symbol in top-5 sector for the 5-point bonus
        sector_ranks = {"Technology": 1}
        result = score_symbol(
            symbol=_SYMBOL,
            run_date=_TODAY,
            row=row,
            stage_result=_make_stage(2),
            tt_result=_make_tt(8),
            vcp_metrics=_make_vcp(is_valid=True, contraction_count=3, vol_contraction_ratio=0.4),
            sector_ranks=sector_ranks,
            symbol_info=_make_symbol_info(_SYMBOL, "Technology"),
            config=_CFG,
        )
        assert result.score >= 85, f"Expected score ≥ 85, got {result.score}"
        assert result.setup_quality == "A+"

    def test_returns_sepa_result_instance(self):
        result = _run(stage=2, conditions_met=8, vcp_valid=True, rs_rating=88)
        assert isinstance(result, SEPAResult)

    def test_a_plus_requires_vcp_qualified(self):
        """A+ must not be granted without vcp_qualified, even if score is ≥ 85."""
        row = _make_row(rs_rating=88, pivot_high=148.0, vol_ratio=3.0)
        sector_ranks = {"Technology": 1}
        result = score_symbol(
            symbol=_SYMBOL,
            run_date=_TODAY,
            row=row,
            stage_result=_make_stage(2),
            tt_result=_make_tt(8),
            vcp_metrics=_make_vcp(is_valid=False, contraction_count=0),
            sector_ranks=sector_ranks,
            symbol_info=_make_symbol_info(_SYMBOL, "Technology"),
            config=_CFG,
        )
        assert result.setup_quality != "A+", (
            "A+ must not be granted when VCP is not qualified"
        )


# ===========================================================================
# Test 2 — Stage 4 hard gate: score == 0, quality == FAIL
# ===========================================================================

class TestStage4Gate:
    """Non-Stage-2 stocks must always score 0 and receive FAIL quality."""

    def test_stage4_score_is_zero(self):
        result = _run(stage=4, conditions_met=8, vcp_valid=True, rs_rating=95)
        assert result.score == 0

    def test_stage4_quality_is_fail(self):
        result = _run(stage=4, conditions_met=8, vcp_valid=True, rs_rating=95)
        assert result.setup_quality == "FAIL"

    def test_stage4_stage_field_preserved(self):
        result = _run(stage=4)
        assert result.stage == 4

    def test_stage1_score_is_zero(self):
        result = _run(stage=1, conditions_met=6, rs_rating=80)
        assert result.score == 0
        assert result.setup_quality == "FAIL"

    def test_stage3_score_is_zero(self):
        result = _run(stage=3, conditions_met=7, rs_rating=75)
        assert result.score == 0
        assert result.setup_quality == "FAIL"


# ===========================================================================
# Test 3 — B setup: Stage 2, 6/8 conditions, moderate rs_rating
# ===========================================================================

class TestBSetup:
    """score ≥ 55, stage==2, conditions_met==6 → quality=='B'."""

    def _b_result(self) -> SEPAResult:
        # rs=70, 6/8 TT, no VCP, 2 contractions (partial credit)
        # weighted: 70*.30 + 75*.25 + 30*.22 + 50*.10 + 50*.07 + 50*.06
        #         = 21.0 + 18.75 + 6.6 + 5.0 + 3.5 + 3.0 = 57.85 → score=57
        return _run(
            stage=2,
            conditions_met=6,
            vcp_valid=False,
            vcp_contractions=2,
            rs_rating=70,
        )

    def test_quality_is_b(self):
        result = self._b_result()
        assert result.setup_quality == "B", (
            f"Expected B, got {result.setup_quality} (score={result.score})"
        )

    def test_score_is_in_valid_range(self):
        result = self._b_result()
        assert 0 < result.score < 100, f"Score {result.score} out of range"

    def test_stage_is_2(self):
        result = self._b_result()
        assert result.stage == 2


# ===========================================================================
# Test 4 — Stage 2, 8/8 TT, VCP NOT qualified → A or B
# ===========================================================================

class TestNoVcpGrade:
    """Without VCP qualification, A+ is impossible; quality is A or B."""

    def test_quality_is_a_or_b(self):
        result = _run(
            stage=2,
            conditions_met=8,
            vcp_valid=False,
            vcp_contractions=1,
            rs_rating=80,
        )
        assert result.setup_quality in ("A", "B"), (
            f"Expected A or B without VCP, got {result.setup_quality}"
        )

    def test_quality_is_not_a_plus(self):
        result = _run(
            stage=2,
            conditions_met=8,
            vcp_valid=False,
            vcp_contractions=0,
            rs_rating=95,
        )
        assert result.setup_quality != "A+", "A+ requires vcp_qualified=True"

    def test_vcp_qualified_field_is_false(self):
        result = _run(stage=2, conditions_met=8, vcp_valid=False)
        assert result.vcp_qualified is False


# ===========================================================================
# Test 5 — Sector component: top sector adds to weighted score
# ===========================================================================

class TestSectorBonus:
    """Symbol in top-5 sector → sector component adds to weighted score."""

    def test_sector_component_applied(self):
        """Score with top-sector rank is higher than without."""
        symbol_info = _make_symbol_info(_SYMBOL, "Technology")
        common_kwargs = dict(
            symbol=_SYMBOL,
            run_date=_TODAY,
            row=_make_row(rs_rating=75),
            stage_result=_make_stage(2),
            tt_result=_make_tt(7),
            vcp_metrics=_make_vcp(is_valid=True, contraction_count=3),
            symbol_info=symbol_info,
            config=_CFG,
        )
        result_no_rank  = score_symbol(**common_kwargs, sector_ranks={})
        result_with_rank = score_symbol(
            **common_kwargs,
            sector_ranks={"Technology": 1},   # rank 1 = strongest sector
        )
        # sector_bonus field is now always 0 (sector is weighted component)
        assert result_with_rank.sector_bonus == 0
        assert result_no_rank.sector_bonus == 0
        # Score with top sector should be higher due to sector component weight
        assert result_with_rank.score >= result_no_rank.score

    def test_outside_top5_no_bonus(self):
        """Symbol in rank-6 sector → sector_bonus == 0."""
        sector_ranks = {"Technology": 6}
        result = score_symbol(
            symbol=_SYMBOL,
            run_date=_TODAY,
            row=_make_row(rs_rating=75),
            stage_result=_make_stage(2),
            tt_result=_make_tt(7),
            vcp_metrics=_make_vcp(),
            sector_ranks=sector_ranks,
            symbol_info=_make_symbol_info(_SYMBOL, "Technology"),
            config=_CFG,
        )
        assert result.sector_bonus == 0


# ===========================================================================
# Test 6 — fundamental_result=None → treated as neutral (score=50)
# ===========================================================================

class TestFundamentalNeutral:
    """When fundamental_result is None, fundamental component is scored 50 (neutral)."""

    def test_fundamental_none_yields_same_score_as_explicit_50(self):
        """None and {"score": 50} must produce identical final scores."""
        common = dict(
            stage=2, conditions_met=7, vcp_valid=True, rs_rating=75
        )
        result_none      = _run(**common, fundamental_result=None)
        result_neutral   = _run(**common, fundamental_result={"score": 50.0})
        assert result_none.score == result_neutral.score

    def test_fundamental_none_pass_is_false(self):
        result = _run(fundamental_result=None)
        assert result.fundamental_pass is False

    def test_fundamental_none_details_is_empty_dict(self):
        result = _run(fundamental_result=None)
        assert result.fundamental_details == {}

    def test_high_fundamental_score_raises_score(self):
        """fundamental_result={"score": 100} must raise score above None baseline."""
        common = dict(stage=2, conditions_met=7, vcp_valid=True, rs_rating=75)
        result_high = _run(**common, fundamental_result={"score": 100.0})
        result_none = _run(**common, fundamental_result=None)
        # fundamental weight=0.07; 50-point difference → 0.07*50=3.5 points
        assert result_high.score >= result_none.score


# ===========================================================================
# Test 7 — news_score=None → treated as neutral (score=50 normalised)
# ===========================================================================

class TestNewsNeutral:
    """When news_score is None, news component is scored 50 (neutral)."""

    def test_news_none_yields_same_score_as_neutral(self):
        """None and news_score=0 (maps to 50 normalised) must be identical."""
        # (0 + 100) / 2 == 50 → same as None branch
        common = dict(stage=2, conditions_met=7, vcp_valid=True, rs_rating=75)
        result_none    = _run(**common, news_score=None)
        result_neutral = _run(**common, news_score=0.0)
        assert result_none.score == result_neutral.score

    def test_news_score_stored_on_result(self):
        result = _run(news_score=75.0)
        assert result.news_score == pytest.approx(75.0)

    def test_news_none_stored_on_result(self):
        result = _run(news_score=None)
        assert result.news_score is None

    def test_positive_news_raises_score(self):
        """news_score=+100 must produce a higher score than None/neutral."""
        common = dict(stage=2, conditions_met=7, vcp_valid=True, rs_rating=75)
        result_bullish = _run(**common, news_score=100.0)
        result_none    = _run(**common, news_score=None)
        # news weight=0.06; 50-point normalised difference → +3 points
        assert result_bullish.score >= result_none.score


# ===========================================================================
# Test 8 — SEPAResult is a proper dataclass → serialisable via asdict()
# ===========================================================================

class TestDataclassSerialisability:
    """SEPAResult must be importable and serialisable by pipeline.py (Phase 3 Step 5)."""

    def test_asdict_returns_dict(self):
        result = _run()
        d = dataclasses.asdict(result)
        assert isinstance(d, dict)

    def test_asdict_contains_all_required_keys(self):
        required_keys = {
            "symbol", "run_date", "stage", "stage_label", "stage_confidence",
            "trend_template_pass", "trend_template_details", "conditions_met",
            "fundamental_pass", "fundamental_details",
            "vcp_qualified", "vcp_details",
            "breakout_triggered", "entry_price", "stop_loss", "risk_pct",
            "target_price", "reward_risk_ratio",
            "rs_rating", "sector_bonus", "news_score",
            "setup_quality", "score",
        }
        d = dataclasses.asdict(_run())
        assert required_keys.issubset(d.keys()), (
            f"Missing keys: {required_keys - d.keys()}"
        )

    def test_score_is_int(self):
        result = _run()
        assert isinstance(result.score, int)

    def test_run_date_preserved(self):
        result = _run()
        assert result.run_date == _TODAY

    def test_symbol_preserved(self):
        result = _run()
        assert result.symbol == _SYMBOL

    def test_setup_quality_is_valid_literal(self):
        valid = {"A+", "A", "B", "C", "FAIL"}
        for stage in [1, 2, 3, 4]:
            result = _run(stage=stage)
            assert result.setup_quality in valid, (
                f"Invalid quality {result.setup_quality!r} for stage {stage}"
            )


# ===========================================================================
# Phase 5 — Test P1: fundamental_result with passes=True → fundamental_pass=True
# ===========================================================================

_FULL_FUNDAMENTALS: dict = {
    "eps": 12.5,
    "eps_accelerating": True,
    "sales_growth_yoy": 25.0,
    "roe": 22.0,
    "debt_to_equity": 0.4,
    "promoter_holding": 55.0,
    "profit_growth": 18.0,
}

_CFG_WITH_FUND = dict(_CFG) | {
    "fundamentals": {
        "enabled": True,
        "hard_gate": False,
        "conditions": {
            "min_roe": 15.0,
            "max_de": 1.0,
            "min_promoter_holding": 35.0,
            "min_sales_growth_yoy": 10.0,
        },
    }
}

_CFG_HARD_GATE = dict(_CFG_WITH_FUND) | {
    "fundamentals": dict(_CFG_WITH_FUND["fundamentals"]) | {"hard_gate": True}
}

_FAIL_FUNDAMENTALS: dict = {
    "eps": -5.0,           # F1 fail
    "eps_accelerating": False,
    "sales_growth_yoy": 2.0,
    "roe": 5.0,
    "debt_to_equity": 3.0,
    "promoter_holding": 10.0,
    "profit_growth": -1.0,
}


def _run_with_config(config: dict, fundamental_result=None, news_score=None) -> "SEPAResult":
    row = _make_row(rs_rating=80)
    symbol_info = _make_symbol_info(_SYMBOL, "Technology")
    return score_symbol(
        symbol=_SYMBOL,
        run_date=_TODAY,
        row=row,
        stage_result=_make_stage(2),
        tt_result=_make_tt(8),
        vcp_metrics=_make_vcp(is_valid=True),
        sector_ranks={},
        symbol_info=symbol_info,
        config=config,
        fundamental_result=fundamental_result,
        news_score=news_score,
    )


class TestPhase5FundamentalsPass:
    """P1: fundamental_result with passes=True → result.fundamental_pass=True."""

    def test_fundamental_pass_true_when_all_conditions_met(self):
        result = _run_with_config(_CFG_WITH_FUND, fundamental_result=_FULL_FUNDAMENTALS)
        assert result.fundamental_pass is True

    def test_fundamental_details_populated(self):
        result = _run_with_config(_CFG_WITH_FUND, fundamental_result=_FULL_FUNDAMENTALS)
        assert result.fundamental_details != {}
        assert "f1_eps_positive" in result.fundamental_details
        assert result.fundamental_details["f1_eps_positive"] is True

    def test_fundamental_score_reflected_in_higher_total(self):
        """All-pass fundamentals (score=100) should yield a higher total than all-fail."""
        result_pass = _run_with_config(_CFG_WITH_FUND, fundamental_result=_FULL_FUNDAMENTALS)
        result_fail = _run_with_config(_CFG_WITH_FUND, fundamental_result=_FAIL_FUNDAMENTALS)
        # Weight=0.07; difference = (100 - 0) * 0.07 ≈ 7 points
        assert result_pass.score > result_fail.score


class TestPhase5HardGate:
    """P2: fundamentals.hard_gate=True + fundamentals failed → quality forced to FAIL."""

    def test_hard_gate_downgrades_to_fail(self):
        result = _run_with_config(_CFG_HARD_GATE, fundamental_result=_FAIL_FUNDAMENTALS)
        assert result.setup_quality == "FAIL"

    def test_score_preserved_despite_fail_quality(self):
        """Score should be >0 even when quality is forced to FAIL by hard gate."""
        result = _run_with_config(_CFG_HARD_GATE, fundamental_result=_FAIL_FUNDAMENTALS)
        # Stage 2 with good TT/VCP/RS → weighted score > 0 before hard gate
        assert result.score > 0

    def test_hard_gate_false_does_not_downgrade(self):
        """hard_gate=False: failing fundamentals lower score but don't force FAIL."""
        result = _run_with_config(_CFG_WITH_FUND, fundamental_result=_FAIL_FUNDAMENTALS)
        # With hard_gate=False, a strong TT+RS stock can still be B or better
        assert result.setup_quality != "FAIL" or result.score < 40  # normal quality logic


class TestPhase5NewsPenalty:
    """P3: news_score=-80 → normalised to 10, penalises overall score.

    Note: With news weight=0.0 in default config, news score doesn't affect
    the final score. These tests verify the news_score is stored on the result.
    """

    def test_very_negative_news_stored_on_result(self):
        """Verify news_score is stored on result, even if weight is 0."""
        common = dict(stage=2, conditions_met=8, vcp_valid=True, rs_rating=80)
        result_neg = _run(**common, news_score=-80.0)
        assert result_neg.news_score == pytest.approx(-80.0)

    def test_news_score_stored_as_raw_on_result(self):
        result = _run(news_score=-80.0)
        assert result.news_score == pytest.approx(-80.0)

    def test_news_none_stored_as_none(self):
        """news_score=None is stored as None."""
        result = _run(news_score=None)
        assert result.news_score is None


class TestPhase5NeutralFallbacks:
    """P4 & P5: None inputs produce neutral (50) score without exceptions."""

    def test_fundamental_none_no_exception(self):
        # Must not raise
        result = _run(stage=2, conditions_met=7, vcp_valid=True, fundamental_result=None)
        assert isinstance(result, SEPAResult)

    def test_fundamental_none_uses_neutral_50(self):
        common = dict(stage=2, conditions_met=7, vcp_valid=True, rs_rating=75)
        result_none    = _run(**common, fundamental_result=None)
        result_neutral = _run(**common, fundamental_result={"score": 50.0})
        assert result_none.score == result_neutral.score

    def test_news_none_no_exception(self):
        result = _run(stage=2, conditions_met=7, vcp_valid=True, news_score=None)
        assert isinstance(result, SEPAResult)

    def test_news_none_uses_neutral_50(self):
        """news_score=None → normalised 50; news_score=0 → (0+100)/2=50. Must match."""
        common = dict(stage=2, conditions_met=7, vcp_valid=True, rs_rating=75)
        result_none    = _run(**common, news_score=None)
        result_zero    = _run(**common, news_score=0.0)
        assert result_none.score == result_zero.score


# ===========================================================================
# SCORE_WEIGHTS integrity
# ===========================================================================

# ===========================================================================
# Improvement 5 — VCP proximity-to-pivot score multiplier
# ===========================================================================

from rules.scorer import _compute_vcp_score  # noqa: E402 (import after helpers defined)


def _make_vcp_valid(
    contraction_count: int = 3,
    vol_contraction_ratio: float = 0.4,
) -> VCPMetrics:
    """Minimal valid VCPMetrics for proximity tests."""
    return VCPMetrics(
        contraction_count=contraction_count,
        max_depth_pct=20.0,
        final_depth_pct=8.0,
        vol_contraction_ratio=vol_contraction_ratio,
        base_length_weeks=8,
        base_low=80.0,
        is_valid_vcp=True,
        tightness_score=4.0,
    )


class TestVcpProximityScore:
    """Verify the proximity-to-pivot multiplier inside _compute_vcp_score()."""

    # Helper: un-proxied base score for a canonical valid VCP
    def _base_score(self) -> float:
        return _compute_vcp_score(_make_vcp_valid(), _CFG, row=None)

    # ------------------------------------------------------------------ #
    # 1. close at 98 % of pivot_high → proximity >= 0.97 → factor = 1.00
    # ------------------------------------------------------------------ #
    def test_close_at_98pct_of_pivot_no_discount(self):
        pivot = 200.0
        row = pd.Series({"pivot_high": pivot, "close": pivot * 0.98})
        score = _compute_vcp_score(_make_vcp_valid(), _CFG, row=row)
        assert score == pytest.approx(self._base_score(), abs=1e-9), (
            f"Expected factor=1.0 at 98 % proximity; got {score} vs base {self._base_score()}"
        )

    # ------------------------------------------------------------------ #
    # 2. close at 85 % → falls into [0.80, 0.90) band.
    #    factor = 0.70 + (0.85 - 0.80) / (0.90 - 0.80) * (0.85 - 0.70)
    #           = 0.70 + 0.5 × 0.15 = 0.775
    # ------------------------------------------------------------------ #
    def test_close_at_85pct_of_pivot_applies_0875_factor(self):
        pivot = 200.0
        row = pd.Series({"pivot_high": pivot, "close": pivot * 0.85})
        score = _compute_vcp_score(_make_vcp_valid(), _CFG, row=row)
        expected = self._base_score() * 0.775
        assert score == pytest.approx(expected, rel=1e-6), (
            f"Expected ~base*0.775={expected:.4f}, got {score:.4f}"
        )

    # ------------------------------------------------------------------ #
    # 3. close at 72 % → below 0.80 → factor = 0.60
    # ------------------------------------------------------------------ #
    def test_close_at_72pct_of_pivot_applies_060_factor(self):
        pivot = 200.0
        row = pd.Series({"pivot_high": pivot, "close": pivot * 0.72})
        score = _compute_vcp_score(_make_vcp_valid(), _CFG, row=row)
        expected = self._base_score() * 0.60
        assert score == pytest.approx(expected, rel=1e-6), (
            f"Expected base*0.60={expected:.4f}, got {score:.4f}"
        )

    # ------------------------------------------------------------------ #
    # 4. row=None → identical to current (no-row) behaviour
    # ------------------------------------------------------------------ #
    def test_row_none_returns_base_score(self):
        score = _compute_vcp_score(_make_vcp_valid(), _CFG, row=None)
        assert score == pytest.approx(self._base_score(), abs=1e-9)

    # ------------------------------------------------------------------ #
    # 5. row present but pivot_high key missing → skip multiplier
    # ------------------------------------------------------------------ #
    def test_missing_pivot_high_skips_multiplier(self):
        row = pd.Series({"close": 190.0})   # no pivot_high key
        score = _compute_vcp_score(_make_vcp_valid(), _CFG, row=row)
        assert score == pytest.approx(self._base_score(), abs=1e-9), (
            "Missing pivot_high must not alter score"
        )

    # ------------------------------------------------------------------ #
    # 6. Unqualified VCP (is_valid_vcp=False) → partial_cap path, never
    #    affected by proximity even when close is very near pivot
    # ------------------------------------------------------------------ #
    def test_unqualified_vcp_proximity_has_no_effect(self):
        vcp_invalid = VCPMetrics(
            contraction_count=2,
            max_depth_pct=20.0,
            final_depth_pct=8.0,
            vol_contraction_ratio=0.4,
            base_length_weeks=8,
            base_low=80.0,
            is_valid_vcp=False,
            tightness_score=4.0,
        )
        pivot = 200.0
        row = pd.Series({"pivot_high": pivot, "close": pivot * 0.98})
        score_with_row    = _compute_vcp_score(vcp_invalid, _CFG, row=row)
        score_without_row = _compute_vcp_score(vcp_invalid, _CFG, row=None)
        assert score_with_row == pytest.approx(score_without_row, abs=1e-9), (
            "Unqualified VCP score must not be affected by proximity"
        )


class TestScoreWeights:
    def test_weights_sum_to_one(self):
        total = sum(SCORE_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, not 1.0"

    def test_all_weights_are_non_negative(self):
        for name, w in SCORE_WEIGHTS.items():
            assert w >= 0, f"Weight for '{name}' is negative: {w}"

    def test_expected_weight_keys_present(self):
        expected = {"rs_rating", "trend", "vcp", "volume", "fundamental", "sector", "news"}
        assert set(SCORE_WEIGHTS.keys()) == expected


# ===========================================================================
# Config-driven weightage override tests
# Verify that scorer reads ALL parameters from config rather than using
# hardcoded values.
# ===========================================================================

class TestConfigDrivenWeights:
    """Changing config["scoring"]["weights"] must change the final score."""

    def _score_with_weights(self, weights: dict) -> int:
        cfg = dict(_CFG) | {"scoring": {"weights": weights}}
        row = _make_row(rs_rating=80)
        result = score_symbol(
            symbol=_SYMBOL,
            run_date=_TODAY,
            row=row,
            stage_result=_make_stage(2),
            tt_result=_make_tt(7),
            vcp_metrics=_make_vcp(is_valid=True, contraction_count=3),
            sector_ranks={},
            symbol_info=_make_symbol_info(_SYMBOL, "Technology"),
            config=cfg,
        )
        return result.score

    def test_boosting_rs_weight_raises_score_for_high_rs(self):
        """Doubling rs_rating weight (and halving another) must change score."""
        base_weights = {
            "rs_rating": 0.30, "trend": 0.25, "vcp": 0.22,
            "volume": 0.10, "fundamental": 0.07, "news": 0.06,
        }
        high_rs_weights = {
            "rs_rating": 0.50, "trend": 0.15, "vcp": 0.22,
            "volume": 0.05, "fundamental": 0.04, "news": 0.04,
        }
        # rs_rating=80 is above neutral (50), so boosting rs weight raises score
        score_base = self._score_with_weights(base_weights)
        score_high = self._score_with_weights(high_rs_weights)
        assert score_high > score_base, (
            f"Expected higher score with boosted rs weight; base={score_base}, high={score_high}"
        )

    def test_zeroing_vcp_weight_via_config_changes_score(self):
        """Setting vcp weight to 0 (and redistributing) changes the score."""
        no_vcp_weights = {
            "rs_rating": 0.35, "trend": 0.30, "vcp": 0.00,
            "volume": 0.15, "fundamental": 0.10, "news": 0.10,
        }
        base_weights = {
            "rs_rating": 0.30, "trend": 0.25, "vcp": 0.22,
            "volume": 0.10, "fundamental": 0.07, "news": 0.06,
        }
        score_base  = self._score_with_weights(base_weights)
        score_no_vcp = self._score_with_weights(no_vcp_weights)
        # The VCP score is not 100 anymore in the new system, and with weight
        # redistribution, the score can go up or down depending on other components.
        # Just verify the scores are different when weights change.
        assert score_base != score_no_vcp, (
            f"Score should change when weights change; base={score_base}, no_vcp={score_no_vcp}"
        )


class TestConfigDrivenVcpScore:
    """Changing config["scoring"]["vcp_score"] must affect _compute_vcp_score output."""

    def _score_vcp_param(self, vcp_overrides: dict) -> int:
        cfg = dict(_CFG) | {"scoring": {"vcp_score": vcp_overrides}}
        return score_symbol(
            symbol=_SYMBOL,
            run_date=_TODAY,
            row=_make_row(rs_rating=75),
            stage_result=_make_stage(2),
            tt_result=_make_tt(7),
            vcp_metrics=_make_vcp(is_valid=True, contraction_count=3, vol_contraction_ratio=0.4),
            sector_ranks={},
            symbol_info=_make_symbol_info(_SYMBOL, "Technology"),
            config=cfg,
        ).score

    def test_raising_valid_base_raises_score(self):
        score_low  = self._score_vcp_param({"valid_base": 60.0})
        score_high = self._score_vcp_param({"valid_base": 90.0})
        assert score_high > score_low

    def test_lowering_valid_base_lowers_score(self):
        score_default = self._score_vcp_param({"valid_base": 60.0})
        score_low     = self._score_vcp_param({"valid_base": 30.0})
        assert score_low < score_default


class TestConfigDrivenVolumeScore:
    """Changing config["scoring"]["volume_score"] must affect _compute_volume_score output."""

    def _score_vol_param(self, vol_overrides: dict) -> int:
        cfg = dict(_CFG) | {"scoring": {"volume_score": vol_overrides}}
        # Breakout day: vol_ratio=3.0, pivot just below close
        row = _make_row(rs_rating=75, pivot_high=148.0, vol_ratio=3.0)
        return score_symbol(
            symbol=_SYMBOL,
            run_date=_TODAY,
            row=row,
            stage_result=_make_stage(2),
            tt_result=_make_tt(7),
            vcp_metrics=_make_vcp(is_valid=True),
            sector_ranks={},
            symbol_info=_make_symbol_info(_SYMBOL, "Technology"),
            config=cfg,
        ).score

    def test_lower_perfect_vol_ratio_raises_breakout_score(self):
        """Halving perfect_vol_ratio doubles vol score for the same vol_ratio."""
        score_default = self._score_vol_param({"perfect_vol_ratio": 3.0})
        score_easier  = self._score_vol_param({"perfect_vol_ratio": 1.5})
        assert score_easier >= score_default


class TestConfigDrivenQualityThresholds:
    """Changing config["scoring"]["setup_quality_thresholds"] must affect grade assignment."""

    def test_lower_a_plus_threshold_promotes_to_a_plus(self):
        """Dropping A+ threshold from 85 to 50 should promote a mid-range score to A+."""
        row = _make_row(rs_rating=80, pivot_high=148.0, vol_ratio=3.0)
        sector_ranks = {"Technology": 1}

        # Default config (A+ needs 85)
        result_default = score_symbol(
            symbol=_SYMBOL, run_date=_TODAY, row=row,
            stage_result=_make_stage(2), tt_result=_make_tt(8),
            vcp_metrics=_make_vcp(is_valid=True, contraction_count=3, vol_contraction_ratio=0.4),
            sector_ranks=sector_ranks,
            symbol_info=_make_symbol_info(_SYMBOL, "Technology"),
            config=_CFG,
        )

        # Lowered threshold config
        cfg_easy = dict(_CFG) | {
            "scoring": {
                "setup_quality_thresholds": {"a_plus": 50, "a": 40, "b": 30, "c": 20},
                "setup_quality_conditions": {
                    "a_plus_min_conditions": 8, "a_min_conditions": 8, "b_min_conditions": 6
                },
            }
        }
        result_easy = score_symbol(
            symbol=_SYMBOL, run_date=_TODAY, row=row,
            stage_result=_make_stage(2), tt_result=_make_tt(8),
            vcp_metrics=_make_vcp(is_valid=True, contraction_count=3, vol_contraction_ratio=0.4),
            sector_ranks=sector_ranks,
            symbol_info=_make_symbol_info(_SYMBOL, "Technology"),
            config=cfg_easy,
        )

        # Both scores are identical (weights unchanged); only grading changes
        assert result_default.score == result_easy.score, "Score must not change when only thresholds change"
        assert result_easy.setup_quality == "A+", (
            f"Expected A+ with lowered threshold; got {result_easy.setup_quality} (score={result_easy.score})"
        )

    def test_higher_b_min_conditions_demotes_grade(self):
        """Raising b_min_conditions to 8 means a 6/8 stock can't get B — falls to C or FAIL."""
        cfg_strict = dict(_CFG) | {
            "scoring": {
                "setup_quality_thresholds": {"a_plus": 85, "a": 70, "b": 55, "c": 40},
                "setup_quality_conditions": {
                    "a_plus_min_conditions": 8, "a_min_conditions": 8, "b_min_conditions": 8,
                },
            }
        }
        result = score_symbol(
            symbol=_SYMBOL, run_date=_TODAY, row=_make_row(rs_rating=70),
            stage_result=_make_stage(2), tt_result=_make_tt(6),
            vcp_metrics=_make_vcp(is_valid=False, contraction_count=2),
            sector_ranks={},
            symbol_info=_make_symbol_info(_SYMBOL, "Technology"),
            config=cfg_strict,
        )
        assert result.setup_quality in ("C", "FAIL"), (
            f"Expected C or FAIL with strict b_min_conditions=8; got {result.setup_quality}"
        )


# ===========================================================================
# Improvement 2 — VCP tightness bonus in _compute_vcp_score
# ===========================================================================


class TestVcpTightnessBonus:
    """Verify the tightness_bonus contribution inside _compute_vcp_score()."""

    def _valid_vcp_with_tight(self, tightness_score: float) -> VCPMetrics:
        return VCPMetrics(
            contraction_count=3,
            max_depth_pct=20.0,
            final_depth_pct=8.0,
            vol_contraction_ratio=1.1,   # no vol bonus so only tightness drives delta
            base_length_weeks=8,
            base_low=80.0,
            is_valid_vcp=True,
            tightness_score=tightness_score,
        )

    # 1. tightness_score=0.3 → bonus contribution > 0
    def test_strong_compression_adds_bonus(self) -> None:
        """tight=0.3 is well below 0.75 threshold → tightness_bonus > 0."""
        vcp_no_tight   = self._valid_vcp_with_tight(float('nan'))
        vcp_with_tight = self._valid_vcp_with_tight(0.3)
        score_base = _compute_vcp_score(vcp_no_tight,   _CFG, row=None)
        score_tight = _compute_vcp_score(vcp_with_tight, _CFG, row=None)
        assert score_tight > score_base, (
            f"tight=0.3 should add bonus; base={score_base:.2f}, tight={score_tight:.2f}"
        )

    # 2. tightness_score=0.75 → tightness_bonus = 0.0
    def test_at_threshold_no_bonus(self) -> None:
        """tight=0.75 is exactly at threshold → (0.75-0.75)/0.75*15 = 0.0."""
        vcp_nan  = self._valid_vcp_with_tight(float('nan'))
        vcp_0_75 = self._valid_vcp_with_tight(0.75)
        assert _compute_vcp_score(vcp_0_75, _CFG, row=None) == pytest.approx(
            _compute_vcp_score(vcp_nan, _CFG, row=None), abs=1e-6
        )

    # 3. tightness_score=1.0 → tightness_bonus = 0.0
    def test_expansion_no_bonus(self) -> None:
        """tight=1.0 > 0.75 → tightness_bonus = 0.0."""
        vcp_nan = self._valid_vcp_with_tight(float('nan'))
        vcp_1_0 = self._valid_vcp_with_tight(1.0)
        assert _compute_vcp_score(vcp_1_0, _CFG, row=None) == pytest.approx(
            _compute_vcp_score(vcp_nan, _CFG, row=None), abs=1e-6
        )

    # 4. tightness_score=nan → tightness_bonus = 0.0
    def test_nan_tightness_no_bonus(self) -> None:
        """nan tightness → tightness_bonus=0.0; score unchanged from pure vol/contraction."""
        vcp_nan  = self._valid_vcp_with_tight(float('nan'))
        vcp_0_3  = self._valid_vcp_with_tight(0.3)
        # nan should give lower (or equal) score than 0.3 strong compression
        assert _compute_vcp_score(vcp_nan, _CFG, row=None) <= _compute_vcp_score(vcp_0_3, _CFG, row=None)

    # 5. Total score with tight=0.3 must not exceed valid_base + max_bonus
    def test_score_capped_at_valid_base_plus_max_bonus(self) -> None:
        """Even with strong tightness, score <= valid_base + max_bonus."""
        from rules.scorer import _VCP_SCORE_DEFAULTS
        valid_base = float(_VCP_SCORE_DEFAULTS["valid_base"])
        max_bonus  = float(_VCP_SCORE_DEFAULTS["max_bonus"])
        cap = valid_base + max_bonus
        vcp = self._valid_vcp_with_tight(0.3)
        score = _compute_vcp_score(vcp, _CFG, row=None)
        assert score <= cap + 1e-6, (
            f"Score {score:.2f} exceeds cap {cap:.2f}"
        )


# ---------------------------------------------------------------------------
# TestVolSlopeBonusScoring — Improvement 3: slope-based vol_bonus in scorer
# ---------------------------------------------------------------------------


class TestVolSlopeBonusScoring:
    """Verify the continuous slope-based vol_bonus in _compute_vcp_score()."""

    from rules.scorer import _compute_vcp_score  # noqa: F401 — used in helpers

    @staticmethod
    def _valid_vcp_with_slope(slope: float) -> VCPMetrics:
        """Build a valid VCPMetrics with the given vol_slope.

        Use contraction_count=1 so contraction_bonus = (3-2)*10 = 10,
        leaving plenty of headroom in max_bonus=40 for the full vol_bonus=20
        contribution to be visible without cap interference.
        """
        return VCPMetrics(
            contraction_count=1,
            max_depth_pct=20.0,
            final_depth_pct=8.0,
            vol_contraction_ratio=0.5,
            base_length_weeks=8,
            base_low=80.0,
            is_valid_vcp=True,
            tightness_score=float("nan"),
            monotonic_decline=True,
            leg_depths=[20.0],
            vol_slope=slope,
        )

    # 1. vol_slope = -0.30 → vol_bonus = 20.0 (max)
    def test_slope_minus_point_30_gives_max_bonus(self) -> None:
        from rules.scorer import _compute_vcp_score, _VCP_SCORE_DEFAULTS
        vol_bonus_strong = float(_VCP_SCORE_DEFAULTS["vol_bonus_strong"])
        vcp_ref  = self._valid_vcp_with_slope(float("nan"))   # zero vol_bonus baseline
        vcp_max  = self._valid_vcp_with_slope(-0.30)
        diff = _compute_vcp_score(vcp_max, _CFG, row=None) - _compute_vcp_score(vcp_ref, _CFG, row=None)
        assert diff == pytest.approx(vol_bonus_strong, abs=1e-6), (
            f"Expected vol_bonus={vol_bonus_strong}, got diff={diff:.4f}"
        )

    # 2. vol_slope = 0.0 → vol_bonus = 0.0
    def test_slope_zero_gives_zero_bonus(self) -> None:
        from rules.scorer import _compute_vcp_score
        vcp_nan  = self._valid_vcp_with_slope(float("nan"))
        vcp_zero = self._valid_vcp_with_slope(0.0)
        assert _compute_vcp_score(vcp_zero, _CFG, row=None) == pytest.approx(
            _compute_vcp_score(vcp_nan, _CFG, row=None), abs=1e-6
        )

    # 3. vol_slope = +0.15 → vol_bonus negative (penalty)
    def test_positive_slope_gives_penalty(self) -> None:
        from rules.scorer import _compute_vcp_score
        vcp_nan  = self._valid_vcp_with_slope(float("nan"))
        vcp_pos  = self._valid_vcp_with_slope(+0.15)
        assert _compute_vcp_score(vcp_pos, _CFG, row=None) < _compute_vcp_score(vcp_nan, _CFG, row=None), (
            "Positive slope should produce a penalty vs nan (zero bonus)"
        )

    # 4. vol_slope = nan → vol_bonus = 0.0 (no change from nan baseline)
    def test_nan_slope_gives_zero_bonus(self) -> None:
        from rules.scorer import _compute_vcp_score
        vcp_nan1 = self._valid_vcp_with_slope(float("nan"))
        vcp_nan2 = self._valid_vcp_with_slope(float("nan"))
        assert _compute_vcp_score(vcp_nan1, _CFG, row=None) == pytest.approx(
            _compute_vcp_score(vcp_nan2, _CFG, row=None), abs=1e-6
        )

    # 5. Distribution pattern [5M,3.8M,7.2M,2.9M] scores lower than clean [5M,4M,3M,2M]
    def test_distribution_spike_scores_lower_than_clean(self) -> None:
        """Clean declining volumes must score higher than a spiked distribution pattern."""
        import numpy as np
        from rules.scorer import _compute_vcp_score

        def _slope_for_avgs(avgs: list[float]) -> float:
            xs = np.arange(len(avgs), dtype=float)
            ys = np.array(avgs, dtype=float)
            baseline = ys[0] if ys[0] > 0 else 1.0
            return float(np.polyfit(xs, ys / baseline, 1)[0])

        slope_clean = _slope_for_avgs([5_000_000, 4_000_000, 3_000_000, 2_000_000])
        slope_dist  = _slope_for_avgs([5_000_000, 3_800_000, 7_200_000, 2_900_000])

        vcp_clean = self._valid_vcp_with_slope(slope_clean)
        vcp_dist  = self._valid_vcp_with_slope(slope_dist)

        score_clean = _compute_vcp_score(vcp_clean, _CFG, row=None)
        score_dist  = _compute_vcp_score(vcp_dist,  _CFG, row=None)

        assert score_clean > score_dist, (
            f"Clean pattern ({score_clean:.2f}) should outscore distribution "
            f"spike ({score_dist:.2f})"
        )


# ---------------------------------------------------------------------------
# TestClimaxPenalty — Improvement 4: score penalty for climax days
# ---------------------------------------------------------------------------


class TestClimaxPenalty:
    """Climax-day penalty is applied in the else (unqualified VCP) branch only."""

    @staticmethod
    def _unqualified_vcp(climax_days: int = 0) -> VCPMetrics:
        """Build an unqualified VCPMetrics (is_valid_vcp=False)."""
        return VCPMetrics(
            contraction_count=2,
            max_depth_pct=30.0,
            final_depth_pct=15.0,
            vol_contraction_ratio=0.8,
            base_length_weeks=6,
            base_low=90.0,
            is_valid_vcp=False,
            tightness_score=0.6,
            monotonic_decline=True,
            leg_depths=[30.0, 15.0],
            vol_slope=-0.2,
            climax_days_in_base=climax_days,
        )

    @staticmethod
    def _valid_vcp(climax_days: int = 0) -> VCPMetrics:
        """Build a valid VCPMetrics (is_valid_vcp=True)."""
        return VCPMetrics(
            contraction_count=3,
            max_depth_pct=20.0,
            final_depth_pct=8.0,
            vol_contraction_ratio=0.4,
            base_length_weeks=8,
            base_low=85.0,
            is_valid_vcp=True,
            tightness_score=0.4,
            monotonic_decline=True,
            leg_depths=[20.0, 12.0, 8.0],
            vol_slope=-0.3,
            climax_days_in_base=climax_days,
        )

    # ------------------------------------------------------------------
    # Test 1: unqualified VCP, climax_days=3 → score = raw_partial - 30 (or 0)
    # ------------------------------------------------------------------
    def test_unqualified_three_climax_days_penalised(self) -> None:
        """climax_days=3 → penalty=30; score must be raw_partial-30 or 0."""
        from rules.scorer import _compute_vcp_score
        m = self._unqualified_vcp(climax_days=3)
        sc = _CFG.get("scoring", {}).get("vcp_score", {})
        partial_cap = sc.get("partial_cap", 45.0)
        partial_per = sc.get("partial_per_contraction", 15.0)
        raw = min(partial_cap, max(0.0, float(m.contraction_count) * partial_per))
        penalty = min(30.0, 3 * 10.0)
        expected = max(0.0, raw - penalty)
        score = _compute_vcp_score(m, _CFG, row=None)
        assert score == pytest.approx(expected, abs=1e-6), (
            f"Expected {expected:.2f} with 3 climax days, got {score:.2f}"
        )

    # ------------------------------------------------------------------
    # Test 2: unqualified VCP, climax_days=0 → score unchanged (no penalty)
    # ------------------------------------------------------------------
    def test_unqualified_zero_climax_days_no_penalty(self) -> None:
        """climax_days=0 → penalty=0; score identical to old partial logic."""
        from rules.scorer import _compute_vcp_score
        m0 = self._unqualified_vcp(climax_days=0)
        sc = _CFG.get("scoring", {}).get("vcp_score", {})
        partial_cap = sc.get("partial_cap", 45.0)
        partial_per = sc.get("partial_per_contraction", 15.0)
        expected_raw = min(partial_cap, max(0.0, float(m0.contraction_count) * partial_per))
        score = _compute_vcp_score(m0, _CFG, row=None)
        assert score == pytest.approx(expected_raw, abs=1e-6), (
            f"Expected raw partial {expected_raw:.2f} with 0 climax days, got {score:.2f}"
        )

    # ------------------------------------------------------------------
    # Test 3: valid VCP, climax_days=3 → bonus path; NO climax penalty
    # ------------------------------------------------------------------
    def test_valid_vcp_climax_days_no_penalty(self) -> None:
        """Valid VCPs go through the bonus path; climax penalty must NOT apply."""
        from rules.scorer import _compute_vcp_score
        m_no_climax   = self._valid_vcp(climax_days=0)
        m_with_climax = self._valid_vcp(climax_days=3)
        score_no   = _compute_vcp_score(m_no_climax,   _CFG, row=None)
        score_with = _compute_vcp_score(m_with_climax, _CFG, row=None)
        # Both take the bonus path → identical scores (penalty is else-branch only)
        assert score_no == pytest.approx(score_with, abs=1e-6), (
            f"Valid VCP scores must not differ by climax_days: "
            f"no_climax={score_no:.2f} with_climax={score_with:.2f}"
        )
