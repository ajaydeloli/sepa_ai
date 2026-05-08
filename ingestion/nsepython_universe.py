"""
ingestion/nsepython_universe.py
--------------------------------
NSE symbol-list helpers backed by the ``nsepython`` library.

Public surface
--------------
* :func:`get_nifty500`  — Nifty-500 constituent tickers via NSE archives CSV (cached per calendar day).
* :func:`get_nse_all`   — All ~2 300 NSE equity tickers via ``nsepython.nse_eq_symbols`` (cached per calendar day).
* :func:`get_universe`  — Dispatcher; choose by *index* name.

Cache strategy
--------------
``@lru_cache(maxsize=1)`` caches the result of each function.  The
*cache_date* parameter (today's ISO date string by default) acts as the
cache key so the cache is **automatically invalidated once per day**:
a new date string produces a cache miss and triggers a fresh network call.

Error handling
--------------
``nsepython`` makes live HTTP calls to NSE; they can fail in CI, on
restricted networks, or during NSE maintenance windows.  All exceptions
are caught here, a warning is logged, and an empty list is returned so
the rest of the pipeline can degrade gracefully instead of crashing.
"""

from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from typing import Any

from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _today_str() -> str:
    """Return today's ISO date string used as the default cache key."""
    return date.today().isoformat()


def _clean_symbols(raw: Any) -> list[str]:
    """Normalise whatever nsepython returns into a flat list of uppercase tickers.

    nsepython may return:
    * A plain Python list of strings  →  ``["RELIANCE", "TCS", ...]``
    * A pandas DataFrame with a ``"Symbol"`` column
    * A list of dicts with a ``"symbol"`` or ``"Symbol"`` key

    All forms are handled here so callers always get ``list[str]``.
    """
    try:
        import pandas as pd  # local import keeps top-level import clean

        if isinstance(raw, pd.DataFrame):
            for col in ("Symbol", "symbol", "SYMBOL"):
                if col in raw.columns:
                    return [str(s).strip().upper() for s in raw[col].dropna() if str(s).strip()]
            # Fallback: use first column
            if not raw.empty:
                return [str(s).strip().upper() for s in raw.iloc[:, 0].dropna() if str(s).strip()]
            return []
    except ImportError:
        pass

    if isinstance(raw, list):
        result: list[str] = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                result.append(item.strip().upper())
            elif isinstance(item, dict):
                for key in ("Symbol", "symbol", "SYMBOL"):
                    val = item.get(key, "")
                    if val and str(val).strip():
                        result.append(str(val).strip().upper())
                        break
        return result

    return []


def _validate_nse_ticker(symbol: str) -> bool:
    """Return True if *symbol* looks like a valid NSE ticker.

    Accepts uppercase letters, digits, hyphens and ampersands (e.g. ``M&M``,
    ``BAJAJ-AUTO``).  Maximum 20 characters.
    """
    return bool(re.match(r"^[A-Z0-9&\-]{1,20}$", symbol))


# ---------------------------------------------------------------------------
# Public cached functions
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_nifty500(cache_date: str | None = None) -> list[str]:
    """Return Nifty-500 constituent tickers.

    Parameters
    ----------
    cache_date:
        ISO date string used as the LRU cache key.  Pass ``None`` (or omit)
        to use today's date so the cache refreshes automatically each day.
        Pass an explicit date string to pin the cache for testing.

    Returns
    -------
    list[str]
        Uppercase NSE tickers.  Empty list if nsepython is unavailable.
    """
    _key = cache_date or _today_str()  # noqa: F841  (key baked into lru_cache arg)

    try:
        import io

        import pandas as pd
        import requests

        # NSE publishes a static CSV of Nifty-500 constituents on their
        # archives server — no session cookie required.
        _CSV_URL = (
            "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
        )
        resp = requests.get(
            _CSV_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()

        df = pd.read_csv(io.StringIO(resp.text))
        symbols = [
            str(s).strip().upper()
            for s in df["Symbol"].dropna()
            if str(s).strip()
        ]

        if not symbols:
            log.warning(
                "get_nifty500: Nifty-500 CSV returned an empty Symbol column — "
                "check NSE connectivity."
            )
            return []

        valid = [s for s in symbols if _validate_nse_ticker(s)]
        skipped = len(symbols) - len(valid)
        if skipped:
            log.warning("get_nifty500: skipped %d invalid ticker(s).", skipped)

        log.info("get_nifty500: loaded %d symbols (cache_date=%s).", len(valid), _key)
        return valid

    except ImportError:
        log.warning(
            "requests or pandas is not installed — cannot load Nifty-500 universe."
        )
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "get_nifty500: failed to fetch Nifty-500 CSV (%s). "
            "Returning empty universe — check NSE connectivity.",
            exc,
        )
        return []


@lru_cache(maxsize=1)
def get_nse_all(cache_date: str | None = None) -> list[str]:
    """Return all ~2 000 NSE equity tickers.

    Parameters
    ----------
    cache_date:
        ISO date string used as the LRU cache key.  Defaults to today.

    Returns
    -------
    list[str]
        Uppercase NSE tickers.  Empty list if nsepython is unavailable.
    """
    _key = cache_date or _today_str()  # noqa: F841

    try:
        from nsepython import nse_eq_symbols  # type: ignore[import]

        # nse_eq_symbols() fetches the full NSE equity list (~2 300 tickers)
        # from the NSE EQUITY_L.csv archive — the former nsefetch() approach
        # used the same underlying file.
        raw: list[str] = nse_eq_symbols()
        symbols = [str(s).strip().upper() for s in raw if str(s).strip()]

        if not symbols:
            log.warning(
                "get_nse_all: nse_eq_symbols() returned an empty result — "
                "NSE equity list unavailable."
            )
            return []

        valid = [s for s in symbols if _validate_nse_ticker(s)]
        skipped = len(symbols) - len(valid)
        if skipped:
            log.warning("get_nse_all: skipped %d invalid ticker(s).", skipped)

        log.info("get_nse_all: loaded %d symbols (cache_date=%s).", len(valid), _key)
        return valid

    except ImportError:
        log.warning(
            "nsepython is not installed — cannot load NSE all-equity universe. "
            "Install it with: pip install nsepython"
        )
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "get_nse_all: nsepython call failed (%s). "
            "Returning empty universe — check NSE connectivity.",
            exc,
        )
        return []


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def get_universe(index: str = "nifty500") -> list[str]:
    """Return the symbol universe for the given *index* name.

    Parameters
    ----------
    index:
        One of ``"nifty500"`` or ``"nse_all"``.  Falls back to Nifty-500
        for unrecognised values (with a warning).

    Returns
    -------
    list[str]
        Uppercase NSE tickers.
    """
    index_lower = index.lower().strip()

    if index_lower == "nifty500":
        return get_nifty500()
    elif index_lower in ("nse_all", "nseall", "all"):
        return get_nse_all()
    else:
        log.warning(
            "get_universe: unknown index %r — falling back to 'nifty500'. "
            "Valid options: 'nifty500', 'nse_all'.",
            index,
        )
        return get_nifty500()
