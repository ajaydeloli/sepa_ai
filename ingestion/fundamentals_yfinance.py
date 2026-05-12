"""
ingestion/fundamentals_yfinance.py
------------------------------------
yfinance-backed fundamentals with 7-day per-symbol JSON cache.

All 7 conditions required by rules/fundamental_template.py are sourced
from yfinance structured APIs — no HTML parsing, no CSS-selector fragility.

Field mapping
-------------
  F1  EPS > 0              ← ticker.info["trailingEps"]
  F2  EPS accelerating     ← ticker.quarterly_financials row "Basic EPS" (QoQ)
  F3  Sales ≥ 10% YoY     ← ticker.info["revenueGrowth"] × 100
  F4  ROE ≥ 15%            ← ticker.info["returnOnEquity"] × 100
  F5  D/E ≤ 1.0            ← ticker.info["debtToEquity"] / 100
  F6  Promoter ≥ 35%       ← ticker.info["heldPercentInsiders"] × 100
  F7  Profit growth > 0    ← ticker.info["earningsGrowth"] × 100

Caveats
-------
* heldPercentInsiders covers all insider holdings — maps closely to Indian
  promoter holding but may differ slightly from the BSE shareholding-pattern
  figure used by Screener.in.
* revenueGrowth and earningsGrowth are trailing 12-month YoY.
* debtToEquity from yfinance is expressed as a percentage (e.g. 45.2 → 0.452
  ratio after dividing by 100).

Cache layout (same schema as fundamentals_screener.py)
------------------------------------------------------
    data/fundamentals/{SYMBOL}.json
    {
        "symbol": "DIXON",
        "fetched_at": "2024-01-15T10:30:00+05:30",
        "source": "yfinance",
        "eps": 12.5,
        ...
    }
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yfinance as yf

from utils.logger import get_logger

log = get_logger(__name__)

_PROJECT_ROOT   = Path(__file__).resolve().parent.parent
_CACHE_DIR      = _PROJECT_ROOT / "data" / "fundamentals"
_CACHE_TTL_DAYS = 7
_NS_SUFFIX      = ".NS"


# ---------------------------------------------------------------------------
# Cache helpers (same interface as fundamentals_screener.py)
# ---------------------------------------------------------------------------

def _cache_path(symbol: str) -> Path:
    return _CACHE_DIR / f"{symbol.upper()}.json"


def _load_cache(symbol: str, ttl_days: float = _CACHE_TTL_DAYS) -> dict | None:
    path = _cache_path(symbol)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        # Only serve back a cache entry that was written by the same source.
        # Prevents the yfinance backend from returning a stale Screener.in
        # entry (or vice-versa) after the operator switches sources.
        if data.get("source") != "yfinance":
            return None
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(tz=timezone.utc) - fetched_at).total_seconds() / 86_400
        if age_days <= ttl_days:
            return data
    except Exception as exc:
        log.warning("fundamentals_yfinance: cache read error for %s — %s", symbol, exc)
    return None


def _save_cache(symbol: str, data: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(symbol)
    try:
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, default=str)
    except Exception as exc:
        log.warning("fundamentals_yfinance: cache write error for %s — %s", symbol, exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(value: Any) -> float | None:
    """Return float or None — never raises."""
    try:
        v = float(value)
        return None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return None


def _eps_from_quarterly(ticker: yf.Ticker) -> tuple[float | None, bool | None]:
    """Derive latest EPS and QoQ acceleration from quarterly financials.

    Returns (latest_eps, accelerating).
    """
    try:
        qf = ticker.quarterly_financials
        for label in ("Basic EPS", "Diluted EPS", "EPS"):
            if label in qf.index:
                row    = qf.loc[label].dropna().sort_index()   # oldest → newest
                values = list(row.values)
                if len(values) >= 2:
                    latest_eps = _safe_float(values[-1])
                    rates = []
                    for i in range(1, len(values)):
                        prev = float(values[i - 1])
                        curr = float(values[i])
                        rates.append(0.0 if prev == 0 else (curr - prev) / abs(prev) * 100)
                    accel = bool(rates[-1] > rates[-2]) if len(rates) >= 2 else None
                    return latest_eps, accel
    except Exception as exc:
        log.debug("fundamentals_yfinance: quarterly EPS parse error — %s", exc)
    return None, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_fundamentals(
    symbol: str,
    force_refresh: bool = False,
    config: dict | None = None,
) -> dict | None:
    """Fetch and cache fundamental data via yfinance.

    Parameters
    ----------
    symbol:
        NSE base ticker, e.g. ``"DIXON"``.
    force_refresh:
        Bypass the cache TTL and always fetch fresh data.
    config:
        Project config dict.  TTL read from
        ``config["fundamentals"]["cache_days"]`` (default 7).

    Returns
    -------
    dict | None
        Fundamentals dict compatible with rules/fundamental_template.py,
        or None if yfinance returns no usable data.  Never raises.
    """
    symbol   = symbol.upper()
    ttl_days = float(
        (config or {}).get("fundamentals", {}).get("cache_days", _CACHE_TTL_DAYS)
    )

    if not force_refresh:
        cached = _load_cache(symbol, ttl_days=ttl_days)
        if cached is not None:
            log.debug("fundamentals_yfinance: cache hit for %s", symbol)
            return cached

    ticker_sym = symbol if symbol.endswith(_NS_SUFFIX) else symbol + _NS_SUFFIX
    try:
        ticker = yf.Ticker(ticker_sym)
        info: dict = ticker.info or {}
    except Exception as exc:
        log.warning("fundamentals_yfinance: fetch failed for %s — %s", symbol, exc)
        return None

    if not info or info.get("quoteType") is None:
        log.warning("fundamentals_yfinance: no data returned for %s", symbol)
        return None

    # F1 + F2 — EPS and QoQ acceleration
    quarterly_eps, eps_accel = _eps_from_quarterly(ticker)
    eps = quarterly_eps if quarterly_eps is not None else _safe_float(info.get("trailingEps"))

    # F3 — Sales growth YoY: fraction → %
    rev_raw         = _safe_float(info.get("revenueGrowth"))
    sales_growth_yoy = round(rev_raw * 100, 2) if rev_raw is not None else None

    # F4 — ROE: fraction → %
    roe_raw = _safe_float(info.get("returnOnEquity"))
    roe     = round(roe_raw * 100, 2) if roe_raw is not None else None

    # F5 — D/E: yfinance is in % (45.2 → ratio 0.452)
    de_raw         = _safe_float(info.get("debtToEquity"))
    debt_to_equity = round(de_raw / 100, 4) if de_raw is not None else None

    # F6 — Promoter / insider holding: fraction → %
    ins_raw          = _safe_float(info.get("heldPercentInsiders"))
    promoter_holding = round(ins_raw * 100, 2) if ins_raw is not None else None

    # F7 — Profit growth YoY: fraction → %
    eg_raw        = _safe_float(info.get("earningsGrowth"))
    profit_growth = round(eg_raw * 100, 2) if eg_raw is not None else None

    data: dict[str, Any] = {
        "symbol":           symbol,
        "fetched_at":       datetime.now(tz=timezone.utc).isoformat(),
        "source":           "yfinance",
        # Core fields consumed by check_fundamental_template()
        "eps":              eps,
        "eps_accelerating": eps_accel,
        "sales_growth_yoy": sales_growth_yoy,
        "roe":              roe,
        "debt_to_equity":   debt_to_equity,
        "promoter_holding": promoter_holding,
        "profit_growth":    profit_growth,
        # Extra context (displayed in raw-values table in the frontend)
        "pe_ratio":         _safe_float(info.get("trailingPE")),
        "pb_ratio":         _safe_float(info.get("priceToBook")),
        "market_cap":       info.get("marketCap"),
    }

    _save_cache(symbol, data)
    log.info(
        "fundamentals_yfinance: fetched for %s  "
        "eps=%s roe=%s de=%s promoter=%s sales_growth=%s profit_growth=%s",
        symbol,
        f"{eps:.2f}"              if eps              is not None else "—",
        f"{roe:.1f}%"            if roe              is not None else "—",
        f"{debt_to_equity:.2f}"  if debt_to_equity   is not None else "—",
        f"{promoter_holding:.1f}%" if promoter_holding is not None else "—",
        f"{sales_growth_yoy:.1f}%" if sales_growth_yoy is not None else "—",
        f"{profit_growth:.1f}%"  if profit_growth    is not None else "—",
    )
    return data


def get_fundamentals_age_days(symbol: str) -> float | None:
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
    except Exception as exc:
        log.warning("fundamentals_yfinance: age check error for %s — %s", symbol, exc)
        return None


def clear_fundamentals_cache(symbol: str | None = None) -> None:
    if symbol is not None:
        path = _cache_path(symbol.upper())
        if path.exists():
            path.unlink()
            log.info("fundamentals_yfinance: cleared cache for %s", symbol.upper())
    else:
        if _CACHE_DIR.exists():
            for f in _CACHE_DIR.glob("*.json"):
                f.unlink()
            log.info("fundamentals_yfinance: cleared all cached files")
