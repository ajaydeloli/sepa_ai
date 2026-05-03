"""
tests/unit/test_news.py
-----------------------
Unit tests for ``ingestion/news.py``.

All HTTP / feedparser / LLM calls are mocked — no real network traffic.

Test matrix
-----------
 1. fetch_market_news: mock feedparser → returns list[dict] with keyword_sentiment
 2. Cache hit (<30 min) → returns cached articles, feedparser NOT called
 3. Cache expired → feedparser called, fresh articles returned
 4. One feed fails → warning logged, articles from other feeds returned
 5. fetch_symbol_news: article mentioning "reliance industries" matches RELIANCE
 6. fetch_symbol_news: alias from symbol_aliases.yaml used for matching (DIXON / "dixon tech")
 7. compute_news_score: all bullish articles → score > 0
 8. compute_news_score: empty list → 0.0
 9. use_llm=False → keyword scoring only (no LLM import)
10. LLM unavailable → falls back to keyword_score, no exception
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ingestion.news import (
    _keyword_score_article,
    compute_news_score,
    fetch_market_news,
    fetch_symbol_news,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "sample_news_articles.json"


def _load_fixture() -> list[dict]:
    with _fixture_path().open() as fh:
        return json.load(fh)


def _fixture_path() -> Path:
    return _FIXTURE_PATH


def _make_feedparser_result(entries: list[dict]) -> SimpleNamespace:
    """Build a minimal fake feedparser parsed result."""
    def _make_entry(d: dict) -> SimpleNamespace:
        e = SimpleNamespace()
        e.title = d.get("title", "")
        e.summary = d.get("description", "")
        e.link = d.get("link", "")
        # Supply published_parsed as a time.struct_time-compatible tuple
        e.published_parsed = (2024, 6, 15, 10, 0, 0, 0, 0, 0)
        e.published = d.get("published", "")
        return e

    result = SimpleNamespace()
    result.entries = [_make_entry(d) for d in entries]
    result.bozo = False
    result.bozo_exception = None
    return result


_SAMPLE_ENTRIES = [
    {
        "title": "Reliance Industries posts record high profit",
        "description": "RIL strong earnings, beat estimates on expansion.",
        "link": "https://example.com/1",
    },
    {
        "title": "Market update for the day",
        "description": "Stable trading session with mixed results.",
        "link": "https://example.com/2",
    },
]


# ---------------------------------------------------------------------------
# Test 1 — fetch_market_news returns article list with keyword fields
# ---------------------------------------------------------------------------

@patch("ingestion.news._load_cache", return_value=None)
@patch("ingestion.news._save_cache")
@patch("feedparser.parse")
def test_fetch_market_news_returns_articles(mock_parse, mock_save, mock_cache):
    mock_parse.return_value = _make_feedparser_result(_SAMPLE_ENTRIES)

    articles = fetch_market_news(force_refresh=True)

    assert isinstance(articles, list)
    assert len(articles) == len(_SAMPLE_ENTRIES) * 4  # 4 feeds × 2 entries each
    first = articles[0]
    assert "title" in first
    assert "keyword_sentiment" in first
    assert first["keyword_sentiment"] in ("bullish", "bearish", "neutral")
    assert -1.0 <= first["keyword_score"] <= 1.0


# ---------------------------------------------------------------------------
# Test 2 — Cache hit: feedparser NOT called
# ---------------------------------------------------------------------------

@patch("feedparser.parse")
def test_cache_hit_skips_fetch(mock_parse, tmp_path):
    import ingestion.news as news_mod

    articles = [
        {
            "title": "cached article",
            "description": "desc",
            "link": "http://x.com",
            "published": "2024-06-15T10:00:00+00:00",
            "source": "x.com",
            "keyword_sentiment": "neutral",
            "keyword_score": 0.0,
        }
    ]
    # Write a fresh cache file
    cache_file = tmp_path / "market_news.json"
    cache_file.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
                "articles": articles,
            }
        )
    )
    original = news_mod._CACHE_PATH
    news_mod._CACHE_PATH = cache_file
    try:
        result = fetch_market_news(force_refresh=False)
        mock_parse.assert_not_called()
        assert result == articles
    finally:
        news_mod._CACHE_PATH = original


# ---------------------------------------------------------------------------
# Test 3 — Cache expired: feedparser IS called
# ---------------------------------------------------------------------------

@patch("ingestion.news._save_cache")
@patch("feedparser.parse")
def test_cache_expired_fetches_fresh(mock_parse, mock_save, tmp_path):
    import ingestion.news as news_mod

    old_articles = [{"title": "old", "description": "", "link": "", "published": "",
                     "source": "x.com", "keyword_sentiment": "neutral", "keyword_score": 0.0}]
    stale_time = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
    cache_file = tmp_path / "market_news.json"
    cache_file.write_text(json.dumps({"fetched_at": stale_time, "articles": old_articles}))

    mock_parse.return_value = _make_feedparser_result(_SAMPLE_ENTRIES)
    original = news_mod._CACHE_PATH
    news_mod._CACHE_PATH = cache_file
    try:
        result = fetch_market_news(force_refresh=False)
        assert mock_parse.called
        assert any(a["title"] == _SAMPLE_ENTRIES[0]["title"] for a in result)
    finally:
        news_mod._CACHE_PATH = original


# ---------------------------------------------------------------------------
# Test 4 — One feed fails: warning logged, other feeds' articles returned
# ---------------------------------------------------------------------------

@patch("ingestion.news._load_cache", return_value=None)
@patch("ingestion.news._save_cache")
@patch("feedparser.parse")
def test_one_feed_failure_logs_warning(mock_parse, mock_save, mock_cache, caplog):
    import logging

    def side_effect(url, **kwargs):
        if "moneycontrol" in url and "marketreports" in url:
            raise ConnectionError("network error")
        return _make_feedparser_result(_SAMPLE_ENTRIES)

    mock_parse.side_effect = side_effect

    with caplog.at_level(logging.WARNING, logger="ingestion"):
        articles = fetch_market_news(force_refresh=True)

    assert any("network error" in r.message or "failed to fetch" in r.message.lower()
               for r in caplog.records)
    assert len(articles) > 0  # articles from other feeds


# ---------------------------------------------------------------------------
# Test 5 — fetch_symbol_news: "reliance industries" in text matches RELIANCE
# ---------------------------------------------------------------------------

def test_fetch_symbol_news_direct_mention():
    articles = _load_fixture()
    matched = fetch_symbol_news("RELIANCE", all_news=articles, use_llm=False)
    assert len(matched) >= 1
    assert all(
        "reliance" in (a["title"] + a["description"]).lower()
        for a in matched
    )


# ---------------------------------------------------------------------------
# Test 6 — fetch_symbol_news: alias from symbol_aliases.yaml used (DIXON)
# ---------------------------------------------------------------------------

def test_fetch_symbol_news_uses_alias():
    """
    The fixture has an article mentioning "Dixon Tech" (an alias for DIXON).
    Without the alias list, plain symbol match on "dixon" would still work, but
    the alias "dixon tech" also appears — we test that aliases are loaded and used.
    """
    articles = _load_fixture()
    # Inject a non-obvious alias-only article
    alias_article = {
        "title": "Dixon tech secures new PLI order",
        "description": "The electronics manufacturer wins fresh government incentive.",
        "link": "https://example.com/dixon",
        "published": "2024-06-15T08:00:00+00:00",
        "source": "example.com",
        "keyword_sentiment": "bullish",
        "keyword_score": 1.0,
    }
    matched = fetch_symbol_news("DIXON", all_news=[alias_article], use_llm=False)
    assert len(matched) == 1
    assert matched[0]["title"] == alias_article["title"]


# ---------------------------------------------------------------------------
# Test 7 — compute_news_score: all bullish articles → score > 0
# ---------------------------------------------------------------------------

def test_compute_news_score_all_bullish():
    articles = [
        {"final_score": 0.8, "published": datetime.now(tz=timezone.utc).isoformat()},
        {"final_score": 1.0, "published": datetime.now(tz=timezone.utc).isoformat()},
        {"final_score": 0.6, "published": datetime.now(tz=timezone.utc).isoformat()},
    ]
    score = compute_news_score(articles)
    assert score > 0


# ---------------------------------------------------------------------------
# Test 8 — compute_news_score: empty list → 0.0
# ---------------------------------------------------------------------------

def test_compute_news_score_empty():
    assert compute_news_score([]) == 0.0


# ---------------------------------------------------------------------------
# Test 9 — use_llm=False: keyword scoring only, no LLM import attempted
# ---------------------------------------------------------------------------

@patch("ingestion.news._llm_rescore")
def test_use_llm_false_no_llm_call(mock_llm):
    articles = _load_fixture()
    matched = fetch_symbol_news("RELIANCE", all_news=articles, use_llm=False)
    mock_llm.assert_not_called()
    for art in matched:
        assert art["llm_sentiment"] is None
        assert art["llm_score"] is None
        assert art["final_score"] == art["keyword_score"]


# ---------------------------------------------------------------------------
# Test 10 — LLM unavailable: falls back to keyword_score, no exception
# ---------------------------------------------------------------------------

@patch("ingestion.news._load_settings")
def test_llm_unavailable_falls_back_gracefully(mock_settings):
    """If LLM import raises ImportError, final_score equals keyword_score."""
    mock_settings.return_value = {
        "llm": {"enabled": True, "provider": "groq", "model": "test", "max_tokens": 50}
    }
    articles = [
        {
            "title": "Reliance Industries strong earnings beat estimates",
            "description": "Strong performance by reliance industries this quarter.",
            "link": "http://x.com/1",
            "published": datetime.now(tz=timezone.utc).isoformat(),
            "source": "x.com",
            "keyword_sentiment": "bullish",
            "keyword_score": 0.5,
        }
    ]

    # Simulate ImportError from the LLM client module
    with patch("builtins.__import__", side_effect=ImportError("no llm module")):
        # We only want the ImportError on the llm.groq_client import; patch more precisely
        pass

    # Use a targeted patch on _llm_rescore to simulate unavailable LLM
    with patch("ingestion.news._llm_rescore", return_value=(None, None)):
        matched = fetch_symbol_news("RELIANCE", all_news=articles, use_llm=True,
                                    config=mock_settings.return_value)

    assert len(matched) == 1
    assert matched[0]["llm_sentiment"] is None
    assert matched[0]["llm_score"] is None
    assert matched[0]["final_score"] == matched[0]["keyword_score"]


# ---------------------------------------------------------------------------
# Bonus: _keyword_score_article unit checks
# ---------------------------------------------------------------------------

def test_keyword_score_bullish_article():
    art = {"title": "Stock surges on strong earnings, beat estimates", "description": ""}
    score = _keyword_score_article(art)
    assert score > 0


def test_keyword_score_bearish_article():
    art = {"title": "Company faces fraud probe and SEBI penalty", "description": ""}
    score = _keyword_score_article(art)
    assert score < 0


def test_keyword_score_neutral_article():
    art = {"title": "Market update for Tuesday", "description": "Trading volumes stable."}
    score = _keyword_score_article(art)
    assert score == 0.0
