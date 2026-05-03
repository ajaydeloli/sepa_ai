"""
ingestion/news.py
-----------------
RSS feed fetcher + keyword scorer + LLM re-scorer for the SEPA AI news
sentiment layer (Section 10 of PROJECT_DESIGN.md).

Public API
----------
    fetch_market_news(force_refresh)           -> list[dict]
    fetch_symbol_news(symbol, ...)             -> list[dict]
    compute_news_score(articles)               -> float
    _keyword_score_article(article)            -> float   (semi-private helper)

Cache layout
------------
    data/news/market_news.json
    {
        "fetched_at": "<ISO timestamp>",
        "articles":   [ { ... }, ... ]
    }

Each article dict
-----------------
    {
        "title":             str,
        "description":       str,
        "link":              str,
        "published":         str  (ISO),
        "source":            str  (feed domain),
        "keyword_sentiment": "bullish" | "bearish" | "neutral",
        "keyword_score":     float   (-1.0 to +1.0),
        # added by fetch_symbol_news:
        "llm_sentiment":     str | None,
        "llm_score":         float | None,
        "final_score":       float,
    }

Anti-patterns avoided
---------------------
* LLM is NEVER called for keyword scoring.
* str.lower().find() is used for alias matching — no regex.
* use_llm=False path is fully functional with no LLM import.
* Cache is global (market_news.json) — no per-symbol files.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
import yaml

from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_PATH    = _PROJECT_ROOT / "data" / "news" / "market_news.json"
_ALIASES_PATH  = _PROJECT_ROOT / "config" / "symbol_aliases.yaml"
_SETTINGS_PATH = _PROJECT_ROOT / "config" / "settings.yaml"

_CACHE_TTL_MINUTES = 30
_FEED_TIMEOUT      = 10   # seconds for feedparser socket timeout

_DEFAULT_FEEDS: list[str] = [
    "https://www.moneycontrol.com/rss/marketreports.xml",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "https://www.business-standard.com/rss/markets-106.rss",
    "https://www.moneycontrol.com/rss/business.xml",
]

BULLISH_KEYWORDS: list[str] = [
    "surge", "rally", "upgrade", "order win", "buyback", "dividend",
    "record high", "expansion", "profit rise", "strong earnings",
    "outperform", "beat estimates", "acquisition", "deal win",
]
BEARISH_KEYWORDS: list[str] = [
    "probe", "fraud", "miss", "downgrade", "resignation", "sebi notice",
    "loss", "decline", "weak", "disappoints", "below estimates",
    "penalty", "lawsuit", "margin pressure",
]

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    try:
        with _SETTINGS_PATH.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("news: could not load settings.yaml — %s", exc)
        return {}


def _load_aliases() -> dict[str, list[str]]:
    """Return {SYMBOL: [alias, ...]} from config/symbol_aliases.yaml."""
    try:
        with _ALIASES_PATH.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("news: could not load symbol_aliases.yaml — %s", exc)
        return {}


def _get_rss_feeds(config: dict | None) -> list[str]:
    if config:
        feeds = config.get("news", {}).get("rss_feeds")
        if feeds:
            return feeds
    return _DEFAULT_FEEDS


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _load_cache() -> dict | None:
    """Return cached payload if it exists and is within TTL, else None."""
    if not _CACHE_PATH.exists():
        return None
    try:
        with _CACHE_PATH.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age_min = (datetime.now(tz=timezone.utc) - fetched_at).total_seconds() / 60
        if age_min <= _CACHE_TTL_MINUTES:
            return payload
    except Exception as exc:  # noqa: BLE001
        log.warning("news: cache read error — %s", exc)
    return None


def _save_cache(articles: list[dict]) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        "articles": articles,
    }
    try:
        with _CACHE_PATH.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
    except Exception as exc:  # noqa: BLE001
        log.warning("news: cache write error — %s", exc)


# ---------------------------------------------------------------------------
# Keyword scoring
# ---------------------------------------------------------------------------

def _keyword_score_article(article: dict) -> float:
    """Fast keyword scoring for a single article.

    Returns a float in [-1.0, +1.0].
    bullish_count - bearish_count, normalised to [-1, 1] by total count.
    If no keywords found, returns 0.0.
    """
    text = (article.get("title", "") + " " + article.get("description", "")).lower()
    bullish = sum(1 for kw in BULLISH_KEYWORDS if kw in text)
    bearish = sum(1 for kw in BEARISH_KEYWORDS if kw in text)
    total = bullish + bearish
    if total == 0:
        return 0.0
    raw = (bullish - bearish) / total
    # Clamp to [-1, 1] (already guaranteed, but explicit)
    return max(-1.0, min(1.0, raw))


def _sentiment_label(score: float) -> str:
    if score > 0:
        return "bullish"
    if score < 0:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Feed parsing
# ---------------------------------------------------------------------------

def _parse_entry(entry: Any, source: str) -> dict:
    """Convert a feedparser entry to our canonical article dict."""
    title       = getattr(entry, "title", "") or ""
    description = getattr(entry, "summary", "") or ""
    link        = getattr(entry, "link", "")  or ""

    # Parse published date to ISO
    published = ""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            published = datetime(
                *entry.published_parsed[:6], tzinfo=timezone.utc
            ).isoformat()
        except Exception:  # noqa: BLE001
            published = str(getattr(entry, "published", ""))
    else:
        published = str(getattr(entry, "published", ""))

    article = {
        "title":       title,
        "description": description,
        "link":        link,
        "published":   published,
        "source":      source,
    }
    score = _keyword_score_article(article)
    article["keyword_score"]     = round(score, 4)
    article["keyword_sentiment"] = _sentiment_label(score)
    return article


def _domain(url: str) -> str:
    """Extract the bare domain from an RSS feed URL."""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc or url
    except Exception:  # noqa: BLE001
        return url


# ---------------------------------------------------------------------------
# Public: fetch_market_news
# ---------------------------------------------------------------------------

def fetch_market_news(force_refresh: bool = False) -> list[dict]:
    """Fetch RSS articles from all configured feeds.

    Cache for 30 minutes in data/news/market_news.json.  On individual feed
    failure, logs a warning and continues.  Returns empty list if all fail.
    """
    if not force_refresh:
        cached = _load_cache()
        if cached is not None:
            log.debug("news: cache hit (%d articles)", len(cached["articles"]))
            return cached["articles"]

    config   = _load_settings()
    feeds    = _get_rss_feeds(config)
    articles: list[dict] = []

    for feed_url in feeds:
        source = _domain(feed_url)
        try:
            parsed = feedparser.parse(feed_url, request_headers={"User-Agent": "SEPA-AI/1.0"})
            if parsed.bozo and not parsed.entries:
                log.warning("news: feed parse error for %s — %s", feed_url, parsed.bozo_exception)
                continue
            for entry in parsed.entries:
                articles.append(_parse_entry(entry, source))
            log.debug("news: fetched %d entries from %s", len(parsed.entries), feed_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("news: failed to fetch feed %s — %s", feed_url, exc)

    if not articles:
        log.warning("news: all feeds failed — returning empty list")
        return []

    _save_cache(articles)
    log.info("news: fetched %d articles from %d feeds", len(articles), len(feeds))
    return articles


# ---------------------------------------------------------------------------
# LLM re-scorer (isolated — gracefully degrades if LLM unavailable)
# ---------------------------------------------------------------------------

def _llm_rescore(symbol: str, article: dict, config: dict) -> tuple[str | None, float | None]:
    """Call the configured LLM to re-score a single article for *symbol*.

    Returns (sentiment_str, score_float) or (None, None) on any failure.
    """
    try:
        llm_cfg  = config.get("llm", {})
        provider = llm_cfg.get("provider", "")
        if not provider or not llm_cfg.get("enabled", False):
            return None, None

        if provider == "groq":
            from llm.groq_client import get_client  # type: ignore[import]
            client = get_client()
        elif provider == "openai":
            from llm.openai_client import get_client  # type: ignore[import]
            client = get_client()
        else:
            log.debug("news: unknown LLM provider '%s' — skipping LLM re-score", provider)
            return None, None

        prompt = (
            f"Rate the sentiment of this financial news article for stock {symbol}.\n"
            f"Title: {article['title']}\n"
            f"Description: {article['description'][:300]}\n\n"
            'Respond with ONLY a JSON object: {"sentiment": "bullish|bearish|neutral", "score": float}\n'
            "where score is -1.0 (very bearish) to +1.0 (very bullish).\n"
            "Consider context — negative news about a competitor may be bullish for the subject."
        )

        model     = llm_cfg.get("model", "llama-3.3-70b-versatile")
        max_tokens = int(llm_cfg.get("max_tokens", 100))
        response  = client.chat(prompt=prompt, model=model, max_tokens=max_tokens)

        # Strip markdown fences if present
        raw = response.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        parsed = json.loads(raw)
        sentiment = str(parsed.get("sentiment", "neutral"))
        score     = float(parsed.get("score", 0.0))
        score     = max(-1.0, min(1.0, score))
        return sentiment, score

    except (ImportError, ModuleNotFoundError):
        log.debug("news: LLM module not available — falling back to keyword score")
        return None, None
    except Exception as exc:  # noqa: BLE001
        log.debug("news: LLM re-score error for %s — %s", symbol, exc)
        return None, None


# ---------------------------------------------------------------------------
# Public: fetch_symbol_news
# ---------------------------------------------------------------------------

def fetch_symbol_news(
    symbol: str,
    all_news: list[dict] | None = None,
    use_llm: bool = True,
    config: dict | None = None,
) -> list[dict]:
    """Filter market_news for articles mentioning *symbol*, then LLM re-score.

    Parameters
    ----------
    symbol:
        NSE ticker (e.g. "RELIANCE").
    all_news:
        Pre-fetched news list; if None, fetch_market_news() is called.
    use_llm:
        If True (default) and LLM is available, re-score matched articles.
    config:
        Project config dict; if None, loaded from settings.yaml.

    Returns
    -------
    list[dict]
        Matched articles enriched with "llm_sentiment", "llm_score",
        and "final_score" keys.
    """
    symbol_upper = symbol.upper()

    if all_news is None:
        all_news = fetch_market_news()

    if config is None:
        config = _load_settings()

    # Build search terms: the raw ticker + any aliases
    aliases_map = _load_aliases()
    raw_aliases: list[str] = aliases_map.get(symbol_upper, [])
    # Include the symbol itself (lowercase) as a search term
    search_terms: list[str] = [symbol_upper.lower()] + [a.lower() for a in raw_aliases]

    matched: list[dict] = []
    for article in all_news:
        haystack = (article.get("title", "") + " " + article.get("description", "")).lower()
        if any(term in haystack for term in search_terms):
            matched.append(dict(article))  # shallow copy so we don't mutate cache

    # LLM re-scoring
    llm_enabled = use_llm and config.get("llm", {}).get("enabled", False)
    for art in matched:
        llm_sentiment, llm_score = (None, None)
        if llm_enabled:
            llm_sentiment, llm_score = _llm_rescore(symbol_upper, art, config)
        art["llm_sentiment"] = llm_sentiment
        art["llm_score"]     = llm_score
        art["final_score"]   = llm_score if llm_score is not None else art["keyword_score"]

    log.debug(
        "news: %d articles matched for %s (LLM=%s)",
        len(matched), symbol_upper, llm_enabled,
    )
    return matched


# ---------------------------------------------------------------------------
# Public: compute_news_score
# ---------------------------------------------------------------------------

def compute_news_score(articles: list[dict]) -> float:
    """Aggregate article sentiments into a -100 to +100 score.

    Method: time-decayed weighted average of final_score × 100.
    Articles without a parseable published date are treated as age=0 days
    (i.e. highest weight).

    Returns 0.0 for an empty list.
    """
    if not articles:
        return 0.0

    now = datetime.now(tz=timezone.utc)
    _DECAY = 0.9  # per day

    total_weight = 0.0
    weighted_sum = 0.0

    for art in articles:
        score = art.get("final_score", art.get("keyword_score", 0.0))

        # Age in days
        age_days = 0.0
        published = art.get("published", "")
        if published:
            try:
                pub_dt = datetime.fromisoformat(published)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                age_days = max(0.0, (now - pub_dt).total_seconds() / 86_400)
            except Exception:  # noqa: BLE001
                age_days = 0.0

        weight = math.pow(_DECAY, age_days)
        weighted_sum  += weight * score
        total_weight  += weight

    if total_weight == 0.0:
        return 0.0

    raw = weighted_sum / total_weight   # in [-1, 1]
    return round(raw * 100, 2)          # scale to [-100, 100]
