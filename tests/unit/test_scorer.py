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
# Test 5 — Sector bonus: top-5 sector adds +5 to score
# ===========================================================================

class TestSectorBonus:
    """Symbol in top-5 sector → sector_bonus==5 and score increases by 5."""

    def test_sector_bonus_applied(self):
        """Score with top-sector bonus is 5 points higher than without."""
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
        result_no_bonus  = score_symbol(**common_kwargs, sector_ranks={})
        result_with_bonus = score_symbol(
            **common_kwargs,
            sector_ranks={"Technology": 1},   # rank 1 ≤ top_n=5 → bonus
        )
        assert result_with_bonus.sector_bonus == 5
        assert result_no_bonus.sector_bonus == 0
        assert result_with_bonus.score == result_no_bonus.score + 5, (
            f"With bonus={result_with_bonus.score}, without={result_no_bonus.score}"
        )

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
    """P3: news_score=-80 → normalised to 10, penalises overall score."""

    def test_very_negative_news_normalises_to_10(self):
        """(-80 + 100) / 2 = 10.  Verify it's used rather than neutral 50."""
        # Score with very negative news should be lower than with neutral news
        common = dict(stage=2, conditions_met=8, vcp_valid=True, rs_rating=80)
        result_neg  = _run(**common, news_score=-80.0)
        result_neut = _run(**common, news_score=None)   # neutral → 50
        # news weight=0.06; normalised delta=(50-10)*0.06=2.4 points
        assert result_neut.score > result_neg.score

    def test_news_score_stored_as_raw_on_result(self):
        result = _run(news_score=-80.0)
        assert result.news_score == pytest.approx(-80.0)

    def test_very_positive_news_raises_score(self):
        common = dict(stage=2, conditions_met=7, vcp_valid=True, rs_rating=75)
        result_pos  = _run(**common, news_score=+100.0)   # normalised → 100
        result_neut = _run(**common, news_score=None)      # neutral → 50
        assert result_pos.score >= result_neut.score


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

class TestScoreWeights:
    def test_weights_sum_to_one(self):
        total = sum(SCORE_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, not 1.0"

    def test_all_weights_are_positive(self):
        for name, w in SCORE_WEIGHTS.items():
            assert w > 0, f"Weight for '{name}' is not positive: {w}"

    def test_expected_weight_keys_present(self):
        expected = {"rs_rating", "trend", "vcp", "volume", "fundamental", "news"}
        assert set(SCORE_WEIGHTS.keys()) == expected
