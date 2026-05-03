"""
tests/unit/test_explainer.py
-----------------------------
Unit tests for llm/explainer.py.

All LLM calls are mocked — no real API traffic occurs.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from llm.explainer import generate_batch_briefs, generate_trade_brief
from rules.scorer import SEPAResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXED_BRIEF = "DIXON shows a classic VCP with 3 contractions and drying volume."

_BASE_CONFIG = {
    "llm": {
        "enabled": True,
        "provider": "groq",
        "max_tokens": 350,
        "only_for_quality": ["A+", "A"],
    }
}


def _make_result(
    symbol: str = "TESTCO",
    quality: str = "A+",
    score: int = 88,
) -> SEPAResult:
    """Return a minimal valid SEPAResult for testing."""
    return SEPAResult(
        symbol=symbol,
        run_date=date(2025, 1, 15),
        stage=2,
        stage_label="Stage 2 — Advancing",
        stage_confidence=85,
        trend_template_pass=True,
        trend_template_details={},
        conditions_met=8,
        vcp_qualified=True,
        vcp_details={"contraction_count": 3},
        breakout_triggered=True,
        entry_price=1500.0,
        stop_loss=1420.0,
        risk_pct=5.33,
        target_price=1740.0,
        reward_risk_ratio=3.0,
        rs_rating=92,
        sector_bonus=5,
        setup_quality=quality,
        score=score,
    )


def _make_ohlcv() -> pd.DataFrame:
    """Return a minimal 5-row OHLCV DataFrame."""
    return pd.DataFrame(
        {
            "date": pd.date_range("2025-01-09", periods=5, freq="B"),
            "close": [1480.0, 1490.0, 1495.0, 1498.0, 1500.0],
            "volume": [100000] * 5,
            "vol_ratio": [1.1, 0.9, 0.8, 0.7, 1.5],
        }
    )


def _mock_client(return_value: str = _FIXED_BRIEF) -> MagicMock:
    """Return a mock LLMClient whose complete_with_fallback yields return_value."""
    client = MagicMock()
    client.complete_with_fallback.return_value = return_value
    return client


# ---------------------------------------------------------------------------
# Test 1 — quality below threshold → None returned immediately
# ---------------------------------------------------------------------------

def test_generate_trade_brief_skips_below_quality_threshold():
    """Quality C is not in only_for_quality → None, no LLM call."""
    result = _make_result(quality="C", score=45)
    client = _mock_client()

    brief = generate_trade_brief(result, _make_ohlcv(), _BASE_CONFIG, client=client)

    assert brief is None
    client.complete_with_fallback.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2 — A+ quality + mock LLM → returns non-empty string
# ---------------------------------------------------------------------------

def test_generate_trade_brief_returns_narrative_for_a_plus():
    """A+ quality with a working mock LLM should return the fixed brief."""
    result = _make_result(quality="A+")
    client = _mock_client(_FIXED_BRIEF)

    brief = generate_trade_brief(result, _make_ohlcv(), _BASE_CONFIG, client=client)

    assert brief is not None
    assert len(brief) > 0
    assert "DIXON" in brief  # content from _FIXED_BRIEF
    client.complete_with_fallback.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3 — LLM returns empty string → validation fails → None
# ---------------------------------------------------------------------------

def test_generate_trade_brief_returns_none_on_empty_llm_response():
    """Empty LLM response fails validation; function should return None."""
    result = _make_result(quality="A+")
    client = _mock_client("")  # empty string

    brief = generate_trade_brief(result, _make_ohlcv(), _BASE_CONFIG, client=client)

    assert brief is None


# ---------------------------------------------------------------------------
# Test 4 — LLM returns JSON/code block → validation fails → None
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_response",
    [
        '{"symbol": "TESTCO", "brief": "some text"}',          # raw JSON object
        "```json\n{\"symbol\": \"TESTCO\"}\n```",              # fenced JSON block
        "```\nsome code here\n```",                             # plain fenced block
        '[{"key": "value"}]',                                   # JSON array
    ],
)
def test_generate_trade_brief_returns_none_on_code_or_json_block(bad_response):
    """Code/JSON blocks in the LLM response must be rejected."""
    result = _make_result(quality="A+")
    client = _mock_client(bad_response)

    brief = generate_trade_brief(result, _make_ohlcv(), _BASE_CONFIG, client=client)

    assert brief is None, f"Expected None for response: {bad_response!r}"


# ---------------------------------------------------------------------------
# Test 5 — client=None + no configured LLM → None, no exception
# ---------------------------------------------------------------------------

def test_generate_trade_brief_returns_none_when_no_client_available():
    """When client=None and get_llm_client returns None, result is None (no crash)."""
    result = _make_result(quality="A+")

    with patch("llm.explainer.get_llm_client", return_value=None):
        brief = generate_trade_brief(result, _make_ohlcv(), _BASE_CONFIG, client=None)

    assert brief is None


# ---------------------------------------------------------------------------
# Test 6 — generate_batch_briefs: 3 A+ results → 3 entries in dict
# ---------------------------------------------------------------------------

def test_generate_batch_briefs_returns_entry_for_each_qualifying_result():
    """3 A+ results should yield a dict with exactly 3 entries."""
    symbols = ["ALPHA", "BETA", "GAMMA"]
    results = [_make_result(symbol=s, quality="A+") for s in symbols]
    ohlcv_data = {s: _make_ohlcv() for s in symbols}

    # Patch get_llm_client so batch doesn't try real providers, then patch
    # generate_trade_brief to return a fixed string for every call.
    with patch("llm.explainer.get_llm_client", return_value=_mock_client()):
        with patch(
            "llm.explainer.generate_trade_brief",
            return_value=_FIXED_BRIEF,
        ) as mock_brief:
            briefs = generate_batch_briefs(results, ohlcv_data, _BASE_CONFIG)

    assert len(briefs) == 3
    for symbol in symbols:
        assert symbol in briefs
        assert briefs[symbol] == _FIXED_BRIEF
    assert mock_brief.call_count == 3


# ---------------------------------------------------------------------------
# Test 7 — generate_batch_briefs: individual failure → skips, continues
# ---------------------------------------------------------------------------

def test_generate_batch_briefs_skips_failed_symbol_and_continues():
    """An exception for one symbol must not abort the rest of the batch."""
    symbols = ["ALPHA", "BETA", "GAMMA"]
    results = [_make_result(symbol=s, quality="A+") for s in symbols]
    ohlcv_data = {s: _make_ohlcv() for s in symbols}

    call_count = 0

    def _side_effect(result, ohlcv_tail, config, client):
        nonlocal call_count
        call_count += 1
        if result.symbol == "BETA":
            raise RuntimeError("Simulated LLM failure for BETA")
        return _FIXED_BRIEF

    with patch("llm.explainer.get_llm_client", return_value=_mock_client()):
        with patch("llm.explainer.generate_trade_brief", side_effect=_side_effect):
            briefs = generate_batch_briefs(results, ohlcv_data, _BASE_CONFIG)

    # BETA failed → only ALPHA and GAMMA should be in the result
    assert "BETA" not in briefs
    assert "ALPHA" in briefs
    assert "GAMMA" in briefs
    assert len(briefs) == 2
    # All 3 symbols were still attempted
    assert call_count == 3
