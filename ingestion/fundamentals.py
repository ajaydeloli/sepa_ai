"""
ingestion/fundamentals.py
--------------------------
Screener.in scraper with 7-day per-symbol cache.

Design
------
* Two URL patterns are tried in order:
    1. https://www.screener.in/company/{symbol}/consolidated/
    2. https://www.screener.in/company/{symbol}/           (standalone fallback)
* Parsed fields cover every input required by rules/fundamental_template.py.
* Every field extraction is wrapped in a try/except — a changed site layout
  degrades gracefully to a missing key rather than crashing the pipeline.
* HTTP calls are never made in CI/unit tests; callers mock ``requests.get``.
* The 0.5 s inter-fetch sleep is skipped when the env-var
  ``SEPA_SKIP_SLEEP`` is set to any non-empty value.

Cache layout
------------
    data/fundamentals/{SYMBOL}.json
    {
        "symbol": "DIXON",
        "fetched_at": "2024-01-15T10:30:00+05:30",
        "pe_ratio": 32.5,
        ...
    }
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / "data" / "fundamentals"
_CACHE_TTL_DAYS = 7

_BASE_URL = "https://www.screener.in/company"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
_REQUEST_TIMEOUT = 10  # seconds


# ---------------------------------------------------------------------------
# Sleep helper (skippable in tests via SEPA_SKIP_SLEEP env-var)
# ---------------------------------------------------------------------------

def _rate_limit_sleep() -> None:
    """Sleep 0.5 s between consecutive Screener.in requests.

    Skipped when ``SEPA_SKIP_SLEEP`` is set to any non-empty string — this
    allows unit-test suites to run at full speed without monkey-patching
    ``time.sleep``.
    """
    if not os.environ.get("SEPA_SKIP_SLEEP"):
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(symbol: str) -> Path:
    return _CACHE_DIR / f"{symbol.upper()}.json"


def _load_cache(symbol: str) -> dict | None:
    """Return cached dict if it exists and is not expired, else None."""
    path = _cache_path(symbol)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        # Normalise to UTC for comparison
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(tz=timezone.utc) - fetched_at).total_seconds() / 86_400
        if age_days <= _CACHE_TTL_DAYS:
            return data
    except Exception as exc:  # noqa: BLE001
        log.warning("fundamentals: cache read error for %s — %s", symbol, exc)
    return None


def _save_cache(symbol: str, data: dict) -> None:
    """Persist *data* to the cache file for *symbol*."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(symbol)
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
    except Exception as exc:  # noqa: BLE001
        log.warning("fundamentals: cache write error for %s — %s", symbol, exc)


# ---------------------------------------------------------------------------
# HTML parsing helpers — each is fully defensive
# ---------------------------------------------------------------------------

def _parse_ratio(soup: BeautifulSoup, label: str) -> float | None:
    """Extract a numeric value from the ``#top-ratios`` section by label text."""
    try:
        section = soup.find(id="top-ratios")
        if not section:
            return None
        for li in section.find_all("li"):
            name_el = li.find(class_="name") or li.find("span")
            if name_el and label.lower() in name_el.get_text(strip=True).lower():
                val_el = li.find(class_="number") or li.find("span", class_=lambda c: c and "number" in c)
                if not val_el:
                    # try the last span
                    spans = li.find_all("span")
                    val_el = spans[-1] if spans else None
                if val_el:
                    raw = val_el.get_text(strip=True).replace(",", "").replace("%", "")
                    return float(raw)
    except Exception:  # noqa: BLE001
        pass
    return None


def _parse_eps_quarterly(soup: BeautifulSoup) -> list[float]:
    """Return the last 4 quarterly EPS values (oldest → newest) from Quarterly Results."""
    try:
        section = soup.find(id="quarterly-results")
        if not section:
            return []
        table = section.find("table", class_="data-table")
        if not table:
            return []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            label = cells[0].get_text(strip=True).lower()
            if "eps" in label or "earning per share" in label:
                values: list[float] = []
                for cell in cells[1:]:
                    raw = cell.get_text(strip=True).replace(",", "")
                    try:
                        values.append(float(raw))
                    except ValueError:
                        pass
                # Return the 4 most recent (last 4 columns)
                return values[-4:] if len(values) >= 4 else values
    except Exception:  # noqa: BLE001
        pass
    return []


def _parse_shareholding(soup: BeautifulSoup) -> dict[str, Any]:
    """
    Parse promoter holding % and FII holding (latest + last 3 quarters)
    from the Shareholding Pattern section.

    Returns dict with keys: promoter_holding, fii_holding_pct, fii_last3.
    """
    result: dict[str, Any] = {
        "promoter_holding": None,
        "fii_holding_pct": None,
        "fii_last3": [],
    }
    try:
        # Find the section by heading
        section = None
        for heading in soup.find_all(["h2", "h3", "section"]):
            if "shareholding" in heading.get_text(strip=True).lower():
                section = heading.find_next("table", class_="data-table")
                break
        if not section:
            # Fallback: any table after a shareholding heading
            for tag in soup.find_all(string=lambda t: t and "shareholding" in t.lower()):
                section = tag.find_parent().find_next("table")
                if section:
                    break
        if not section:
            return result

        for row in section.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            label = cells[0].get_text(strip=True).lower()
            values_raw = [
                c.get_text(strip=True).replace(",", "").replace("%", "")
                for c in cells[1:]
            ]
            floats = []
            for v in values_raw:
                try:
                    floats.append(float(v))
                except ValueError:
                    pass
            if "promoter" in label and floats:
                result["promoter_holding"] = floats[-1]
            elif "fii" in label or "foreign" in label:
                if floats:
                    result["fii_holding_pct"] = floats[-1]
                    result["fii_last3"] = floats[-3:] if len(floats) >= 3 else floats
    except Exception:  # noqa: BLE001
        pass
    return result


def _parse_annual_growth(soup: BeautifulSoup) -> dict[str, float | None]:
    """
    Compute YoY sales growth and profit growth from the annual P&L table.

    Returns dict with: sales_growth_yoy, profit_growth,
                       latest_revenue, latest_profit.
    """
    result: dict[str, Any] = {
        "sales_growth_yoy": None,
        "profit_growth": None,
        "latest_revenue": None,
        "latest_profit": None,
    }
    try:
        section = soup.find(id="profit-loss")
        if not section:
            return result
        table = section.find("table", class_="data-table")
        if not table:
            return result

        def _row_floats(row_label: str) -> list[float]:
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if not cells:
                    continue
                if row_label.lower() in cells[0].get_text(strip=True).lower():
                    out = []
                    for c in cells[1:]:
                        raw = c.get_text(strip=True).replace(",", "")
                        try:
                            out.append(float(raw))
                        except ValueError:
                            pass
                    return out
            return []

        sales = _row_floats("sales") or _row_floats("revenue")
        profit = _row_floats("net profit") or _row_floats("profit after tax")

        if len(sales) >= 2 and sales[-2] != 0:
            result["sales_growth_yoy"] = round((sales[-1] - sales[-2]) / abs(sales[-2]) * 100, 2)
            result["latest_revenue"] = sales[-1]
        if len(profit) >= 2 and profit[-2] != 0:
            result["profit_growth"] = round((profit[-1] - profit[-2]) / abs(profit[-2]) * 100, 2)
            result["latest_profit"] = profit[-1]
    except Exception:  # noqa: BLE001
        pass
    return result


# ---------------------------------------------------------------------------
# Derived computation helpers
# ---------------------------------------------------------------------------

def _compute_eps_metrics(eps_values: list[float]) -> tuple[list[float], bool | None]:
    """
    Given a list of EPS values (oldest → newest), compute:
    - growth_rates : list of QoQ % growth rates
    - accelerating : True if most recent rate > the one before it
    """
    if len(eps_values) < 2:
        return [], None
    rates: list[float] = []
    for i in range(1, len(eps_values)):
        prev = eps_values[i - 1]
        if prev == 0:
            rates.append(0.0)
        else:
            rates.append(round((eps_values[i] - prev) / abs(prev) * 100, 2))
    accelerating: bool | None = None
    if len(rates) >= 2:
        accelerating = bool(rates[-1] > rates[-2])
    return rates, accelerating


def _compute_fii_trend(fii_last3: list[float]) -> str:
    """Return 'rising', 'falling', or 'flat' based on the last 3 FII holding values."""
    if len(fii_last3) < 2:
        return "flat"
    diffs = [fii_last3[i] - fii_last3[i - 1] for i in range(1, len(fii_last3))]
    if all(d > 0 for d in diffs):
        return "rising"
    if all(d < 0 for d in diffs):
        return "falling"
    return "flat"


# ---------------------------------------------------------------------------
# HTTP fetch helper
# ---------------------------------------------------------------------------

def _fetch_html(symbol: str) -> str | None:
    """
    Try consolidated URL first, fall back to standalone.
    Returns raw HTML string, or None on any HTTP/network failure.
    """
    urls = [
        f"{_BASE_URL}/{symbol}/consolidated/",
        f"{_BASE_URL}/{symbol}/",
    ]
    for url in urls:
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
            if resp.status_code == 200:
                log.debug("fundamentals: fetched %s via %s", symbol, url)
                return resp.text
            if resp.status_code == 404:
                log.debug("fundamentals: 404 at %s, trying next URL", url)
                continue
            log.warning(
                "fundamentals: HTTP %s for %s at %s", resp.status_code, symbol, url
            )
        except requests.exceptions.Timeout:
            log.warning("fundamentals: timeout fetching %s at %s", symbol, url)
        except requests.exceptions.RequestException as exc:
            log.warning("fundamentals: request error for %s at %s — %s", symbol, url, exc)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_fundamentals(symbol: str, force_refresh: bool = False) -> dict | None:
    """Fetch and cache fundamental data from Screener.in.

    Parameters
    ----------
    symbol:
        NSE base ticker, e.g. ``"DIXON"``.
    force_refresh:
        If True, bypass the 7-day TTL cache and fetch fresh data.

    Returns
    -------
    dict | None
        Parsed fundamentals dict (always includes ``symbol`` and
        ``fetched_at`` ISO timestamp), or ``None`` if the fetch or
        parse fails.  Never raises.
    """
    symbol = symbol.upper()

    # ── Cache check ──────────────────────────────────────────────────────
    if not force_refresh:
        cached = _load_cache(symbol)
        if cached is not None:
            log.debug("fundamentals: cache hit for %s", symbol)
            return cached

    # ── HTTP fetch ────────────────────────────────────────────────────────
    html = _fetch_html(symbol)
    _rate_limit_sleep()

    if html is None:
        log.warning("fundamentals: could not fetch HTML for %s — returning None", symbol)
        return None

    # ── Parse ─────────────────────────────────────────────────────────────
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:  # noqa: BLE001
        log.warning("fundamentals: BeautifulSoup parse error for %s — %s", symbol, exc)
        return None

    data: dict[str, Any] = {"symbol": symbol}

    # Top-ratio section
    data["pe_ratio"]       = _parse_ratio(soup, "Stock P/E")
    data["pb_ratio"]       = _parse_ratio(soup, "Price to Book")
    data["roe"]            = _parse_ratio(soup, "Return on Equity")
    data["roce"]           = _parse_ratio(soup, "ROCE")
    data["debt_to_equity"] = _parse_ratio(soup, "Debt to equity")
    data["eps"]            = _parse_ratio(soup, "Earning Per Share")

    # Shareholding
    sh = _parse_shareholding(soup)
    data["promoter_holding"] = sh["promoter_holding"]
    data["fii_holding_pct"]  = sh["fii_holding_pct"]
    fii_last3                = sh["fii_last3"]
    data["fii_trend"]        = _compute_fii_trend(fii_last3)

    # Quarterly EPS
    eps_vals = _parse_eps_quarterly(soup)
    data["eps_values"] = eps_vals
    rates, accel = _compute_eps_metrics(eps_vals)
    data["eps_growth_rates"]  = rates
    data["eps_accelerating"]  = accel

    # Annual P&L
    annual = _parse_annual_growth(soup)
    data["sales_growth_yoy"] = annual["sales_growth_yoy"]
    data["profit_growth"]    = annual["profit_growth"]
    data["latest_revenue"]   = annual["latest_revenue"]
    data["latest_profit"]    = annual["latest_profit"]

    # Timestamp
    data["fetched_at"] = datetime.now(tz=timezone.utc).isoformat()

    _save_cache(symbol, data)
    log.info("fundamentals: fetched and cached data for %s", symbol)
    return data


def get_fundamentals_age_days(symbol: str) -> float | None:
    """Return the age of the cached fundamentals file in days, or None if not cached.

    Used by ``pipeline/runner.py`` to surface data-freshness in run reports.

    Parameters
    ----------
    symbol:
        NSE base ticker.

    Returns
    -------
    float | None
        Age in fractional days, or ``None`` if no cache file exists.
    """
    path = _cache_path(symbol.upper())
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return (datetime.now(tz=timezone.utc) - fetched_at).total_seconds() / 86_400
    except Exception as exc:  # noqa: BLE001
        log.warning("fundamentals: age check error for %s — %s", symbol, exc)
        return None


def clear_fundamentals_cache(symbol: str | None = None) -> None:
    """Delete the on-disk cache for a single symbol, or for every symbol.

    Parameters
    ----------
    symbol:
        NSE base ticker to clear.  Pass ``None`` (default) to clear the
        entire ``data/fundamentals/`` directory.
    """
    if symbol is not None:
        path = _cache_path(symbol.upper())
        if path.exists():
            path.unlink()
            log.info("fundamentals: cleared cache for %s", symbol.upper())
    else:
        if _CACHE_DIR.exists():
            for json_file in _CACHE_DIR.glob("*.json"):
                json_file.unlink()
            log.info("fundamentals: cleared all cached files in %s", _CACHE_DIR)
