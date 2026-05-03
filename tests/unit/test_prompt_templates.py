"""
tests/unit/test_prompt_templates.py
------------------------------------
Unit tests for the Jinja2 prompt templates in llm/prompt_templates/.

Tests validate that:
  1. trade_brief.j2 renders without error with a minimal SEPAResult-like dict
  2. The rendered trade_brief prompt contains the no-recommendation instruction
  3. trade_brief with fundamental_pass=None produces no fundamental section
  4. trade_brief with news_score provided mentions the score in output
  5. watchlist_summary.j2 renders without error with 3 top_candidates
  6. Both templates render to non-empty strings (> 100 chars)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "llm" / "prompt_templates"


@pytest.fixture(scope="module")
def jinja_env() -> Environment:
    """Jinja2 Environment pointing at the real template directory."""
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


@pytest.fixture()
def minimal_trade_brief_ctx() -> dict:
    """Minimal variable dict that satisfies all non-optional trade_brief.j2 slots."""
    return {
        "symbol": "TESTCO",
        "run_date": date(2026, 4, 25),
        "setup_quality": "A",
        "score": 78,
        "stage_label": "Stage 2 — Advancing",
        "stage_confidence": 85,
        "conditions_met": 8,
        "trend_template_pass": True,
        "vcp_qualified": True,
        "vcp_details": {
            "contraction_count": 3,
            "vol_contraction_ratio": 0.45,
            "base_length_weeks": 8,
        },
        "rs_rating": 82,
        "breakout_triggered": False,
        "entry_price": None,
        "stop_loss": None,
        "risk_pct": None,
        "reward_risk_ratio": None,
        "fundamental_pass": None,     # Phase 5 not wired — None by default
        "fundamental_details": {},
        "news_score": None,           # Phase 5 not wired — None by default
        "sector_bonus": 5,
    }


@pytest.fixture()
def minimal_watchlist_ctx() -> dict:
    """Minimal variable dict for watchlist_summary.j2."""
    return {
        "run_date": date(2026, 4, 25),
        "a_plus_count": 3,
        "a_count": 7,
        "b_count": 12,
        "market_mood": "Constructive",
        "top_candidates": [
            {"symbol": "DIXON", "score": 91, "quality": "A+", "stage_label": "Stage 2 — Advancing"},
            {"symbol": "POLYCAB", "score": 84, "quality": "A", "stage_label": "Stage 2 — Advancing"},
            {"symbol": "TIINDIA", "score": 76, "quality": "A", "stage_label": "Stage 2 — Advancing"},
        ],
        "sector_leaders": ["Capital Goods", "Auto Ancillary", "Electronics"],
    }


# ---------------------------------------------------------------------------
# Tests — trade_brief.j2
# ---------------------------------------------------------------------------

class TestTradeBriefTemplate:

    def test_renders_without_error_minimal(self, jinja_env, minimal_trade_brief_ctx):
        """Test 1: trade_brief.j2 renders without error with minimal SEPAResult dict."""
        template = jinja_env.get_template("trade_brief.j2")
        output = template.render(**minimal_trade_brief_ctx)
        assert output is not None

    def test_contains_no_recommendation_instruction(self, jinja_env, minimal_trade_brief_ctx):
        """Test 2: Rendered prompt contains the explicit no-recommendation instruction."""
        template = jinja_env.get_template("trade_brief.j2")
        output = template.render(**minimal_trade_brief_ctx)
        assert "Do NOT make a buy/sell recommendation" in output

    def test_no_fundamental_section_when_none(self, jinja_env, minimal_trade_brief_ctx):
        """Test 3: fundamental_pass=None → no fundamental section in output."""
        ctx = dict(minimal_trade_brief_ctx, fundamental_pass=None, fundamental_details={})
        template = jinja_env.get_template("trade_brief.j2")
        output = template.render(**ctx)
        assert "Fundamentals:" not in output

    def test_fundamental_section_present_when_provided(self, jinja_env, minimal_trade_brief_ctx):
        """Bonus: fundamental_pass=True → fundamental section IS in output."""
        ctx = dict(
            minimal_trade_brief_ctx,
            fundamental_pass=True,
            fundamental_details={"roe": 28.3, "eps_accelerating": True},
        )
        template = jinja_env.get_template("trade_brief.j2")
        output = template.render(**ctx)
        assert "Fundamentals:" in output

    def test_news_score_mentioned_when_provided(self, jinja_env, minimal_trade_brief_ctx):
        """Test 4: news_score provided → news score value mentioned in output."""
        ctx = dict(minimal_trade_brief_ctx, news_score=42.5)
        template = jinja_env.get_template("trade_brief.j2")
        output = template.render(**ctx)
        assert "42.5" in output

    def test_no_news_section_when_none(self, jinja_env, minimal_trade_brief_ctx):
        """Bonus: news_score=None → no News Sentiment section in output."""
        ctx = dict(minimal_trade_brief_ctx, news_score=None)
        template = jinja_env.get_template("trade_brief.j2")
        output = template.render(**ctx)
        assert "News Sentiment:" not in output

    def test_renders_to_non_empty_string(self, jinja_env, minimal_trade_brief_ctx):
        """Test 6 (trade_brief part): Output is a non-empty string longer than 100 chars."""
        template = jinja_env.get_template("trade_brief.j2")
        output = template.render(**minimal_trade_brief_ctx)
        assert isinstance(output, str)
        assert len(output) > 100

    def test_symbol_and_score_present(self, jinja_env, minimal_trade_brief_ctx):
        """Symbol name and score appear in rendered output."""
        template = jinja_env.get_template("trade_brief.j2")
        output = template.render(**minimal_trade_brief_ctx)
        assert "TESTCO" in output
        assert "78" in output

    def test_entry_price_shown_when_provided(self, jinja_env, minimal_trade_brief_ctx):
        """entry_price renders when not None."""
        ctx = dict(minimal_trade_brief_ctx, entry_price=1450.75, stop_loss=1380.0, risk_pct=4.83)
        template = jinja_env.get_template("trade_brief.j2")
        output = template.render(**ctx)
        assert "1450.75" in output
        assert "1380.0" in output

    def test_entry_price_absent_when_none(self, jinja_env, minimal_trade_brief_ctx):
        """entry_price block is not rendered when None."""
        ctx = dict(minimal_trade_brief_ctx, entry_price=None)
        template = jinja_env.get_template("trade_brief.j2")
        output = template.render(**ctx)
        assert "Entry Price" not in output


# ---------------------------------------------------------------------------
# Tests — watchlist_summary.j2
# ---------------------------------------------------------------------------

class TestWatchlistSummaryTemplate:

    def test_renders_without_error(self, jinja_env, minimal_watchlist_ctx):
        """Test 5: watchlist_summary.j2 renders without error with 3 top_candidates."""
        template = jinja_env.get_template("watchlist_summary.j2")
        output = template.render(**minimal_watchlist_ctx)
        assert output is not None

    def test_renders_to_non_empty_string(self, jinja_env, minimal_watchlist_ctx):
        """Test 6 (watchlist part): Output is a non-empty string longer than 100 chars."""
        template = jinja_env.get_template("watchlist_summary.j2")
        output = template.render(**minimal_watchlist_ctx)
        assert isinstance(output, str)
        assert len(output) > 100

    def test_all_candidates_appear_in_output(self, jinja_env, minimal_watchlist_ctx):
        """All 3 top_candidate symbols appear in the rendered prompt."""
        template = jinja_env.get_template("watchlist_summary.j2")
        output = template.render(**minimal_watchlist_ctx)
        for candidate in minimal_watchlist_ctx["top_candidates"]:
            assert candidate["symbol"] in output

    def test_breadth_counts_in_output(self, jinja_env, minimal_watchlist_ctx):
        """A+, A, B counts appear in rendered output."""
        template = jinja_env.get_template("watchlist_summary.j2")
        output = template.render(**minimal_watchlist_ctx)
        assert str(minimal_watchlist_ctx["a_plus_count"]) in output
        assert str(minimal_watchlist_ctx["a_count"]) in output
        assert str(minimal_watchlist_ctx["b_count"]) in output

    def test_sector_leaders_present(self, jinja_env, minimal_watchlist_ctx):
        """Sector leaders appear in rendered output when provided."""
        template = jinja_env.get_template("watchlist_summary.j2")
        output = template.render(**minimal_watchlist_ctx)
        for sector in minimal_watchlist_ctx["sector_leaders"]:
            assert sector in output

    def test_no_sector_section_when_empty(self, jinja_env, minimal_watchlist_ctx):
        """No sector block rendered when sector_leaders is an empty list."""
        ctx = dict(minimal_watchlist_ctx, sector_leaders=[])
        template = jinja_env.get_template("watchlist_summary.j2")
        output = template.render(**ctx)
        assert "Leading Sectors" not in output

    def test_no_recommendation_instruction_present(self, jinja_env, minimal_watchlist_ctx):
        """The no-recommendation instruction block is present in rendered output."""
        template = jinja_env.get_template("watchlist_summary.j2")
        output = template.render(**minimal_watchlist_ctx)
        assert "No buy/sell recommendations" in output

    def test_market_mood_present(self, jinja_env, minimal_watchlist_ctx):
        """Market mood value appears in rendered output."""
        template = jinja_env.get_template("watchlist_summary.j2")
        output = template.render(**minimal_watchlist_ctx)
        assert minimal_watchlist_ctx["market_mood"] in output

    def test_run_date_present(self, jinja_env, minimal_watchlist_ctx):
        """run_date appears in rendered output."""
        template = jinja_env.get_template("watchlist_summary.j2")
        output = template.render(**minimal_watchlist_ctx)
        assert "2026-04-25" in output
