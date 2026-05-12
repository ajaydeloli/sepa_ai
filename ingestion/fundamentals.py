"""
ingestion/fundamentals.py
--------------------------
Source-agnostic router for fundamental data fetching.

Reads ``config["fundamentals"]["source"]`` and delegates every call to the
matching backend module:

    "screener"  →  ingestion.fundamentals_screener   (HTML scraper, default)
    "yfinance"  →  ingestion.fundamentals_yfinance   (structured API)

All three public functions have identical signatures so the rest of the
codebase (pipeline/runner.py, screener/pipeline.py, api/routers/…) imports
only from this module and never needs to know which backend is active.

Switching sources
-----------------
Edit config/settings.yaml:

    fundamentals:
      source: "yfinance"   # or "screener"

The cache entries written by each backend include a ``"source"`` key.
Switching backends automatically bypasses stale entries from the old source
(the new backend's _load_cache() ignores entries with a different source tag)
so you will always get a fresh fetch after a switch.
"""
from __future__ import annotations

from utils.logger import get_logger

log = get_logger(__name__)

_VALID_SOURCES = ("screener", "yfinance")
_DEFAULT_SOURCE = "screener"


def _get_backend(config: dict | None):
    """Return the backend module for the configured source."""
    source = (config or {}).get("fundamentals", {}).get("source", _DEFAULT_SOURCE)
    if source not in _VALID_SOURCES:
        log.warning(
            "fundamentals: unknown source %r — falling back to %r. "
            "Valid options: %s",
            source, _DEFAULT_SOURCE, ", ".join(_VALID_SOURCES),
        )
        source = _DEFAULT_SOURCE

    if source == "yfinance":
        from ingestion import fundamentals_yfinance as backend
    else:
        from ingestion import fundamentals_screener as backend

    log.debug("fundamentals: using backend %r", source)
    return backend


# ---------------------------------------------------------------------------
# Public API — thin wrappers that delegate to the active backend
# ---------------------------------------------------------------------------

def fetch_fundamentals(
    symbol: str,
    force_refresh: bool = False,
    config: dict | None = None,
) -> dict | None:
    """Fetch (and cache) fundamental data for *symbol*.

    Parameters
    ----------
    symbol:
        NSE base ticker, e.g. ``"DIXON"``.
    force_refresh:
        When True, bypass the TTL cache and fetch fresh data.
    config:
        Project config dict.  Controls ``source``, ``cache_days``, etc.

    Returns
    -------
    dict | None
        Fundamentals dict compatible with rules/fundamental_template.py,
        or None if the backend cannot retrieve data.  Never raises.
    """
    return _get_backend(config).fetch_fundamentals(
        symbol=symbol,
        force_refresh=force_refresh,
        config=config,
    )


def get_fundamentals_age_days(symbol: str) -> float | None:
    """Return the age in days of the cached entry for *symbol*, or None."""
    # Age check doesn't depend on source — both backends share the same
    # cache directory and the same JSON schema, so either module works here.
    from ingestion import fundamentals_screener as _any
    return _any.get_fundamentals_age_days(symbol)


def clear_fundamentals_cache(symbol: str | None = None) -> None:
    """Delete the on-disk cache for one symbol, or all symbols."""
    from ingestion import fundamentals_screener as _any
    _any.clear_fundamentals_cache(symbol)
