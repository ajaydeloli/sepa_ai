"""
backtest/regime.py
------------------
Market regime labelling for the SEPA AI backtesting engine.

Regime classification follows the NSE_REGIME_CALENDAR (Appendix E of
PROJECT_DESIGN.md) for all historically-defined periods.  Dates that
fall beyond the calendar end use a 20-period SMA-200 slope fallback
derived from a benchmark DataFrame.

Public API
----------
get_regime(trade_date, benchmark_df) -> RegimeType
label_trades(trades, benchmark_df)   -> list[dict]
get_regime_stats(trades)             -> dict[str, dict]
"""

from __future__ import annotations

from datetime import date
from typing import Literal

import pandas as pd

from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

RegimeType = Literal["Bull", "Bear", "Sideways", "Unknown"]


# ---------------------------------------------------------------------------
# Regime calendar (Appendix E — NSE Market Regime Calendar)
# ---------------------------------------------------------------------------

NSE_REGIME_CALENDAR: list[dict] = [
    {
        "start": date(2014, 5, 1),
        "end": date(2018, 1, 31),
        "regime": "Bull",
        "rationale": "Modi wave + GST + recovery",
    },
    {
        "start": date(2018, 2, 1),
        "end": date(2019, 3, 31),
        "regime": "Sideways",
        "rationale": "IL&FS crisis, NBFC stress, mid-cap collapse",
    },
    {
        "start": date(2019, 4, 1),
        "end": date(2020, 1, 31),
        "regime": "Bull",
        "rationale": "Pre-COVID recovery",
    },
    {
        "start": date(2020, 2, 1),
        "end": date(2020, 3, 31),
        "regime": "Bear",
        "rationale": "COVID crash",
    },
    {
        "start": date(2020, 4, 1),
        "end": date(2021, 12, 31),
        "regime": "Bull",
        "rationale": "V-shaped recovery, liquidity rally",
    },
    {
        "start": date(2022, 1, 1),
        "end": date(2022, 12, 31),
        "regime": "Sideways",
        "rationale": "Fed rate hikes, FII selling",
    },
    {
        "start": date(2023, 1, 1),
        "end": date(2024, 9, 30),
        "regime": "Bull",
        "rationale": "Earnings recovery, domestic flows",
    },
    {
        "start": date(2024, 10, 1),
        "end": date(2025, 3, 31),
        "regime": "Sideways",
        "rationale": "Global uncertainty",
    },
    # After 2025-03-31: slope fallback is used (see get_regime)
]

# Pre-computed calendar end date for fast comparison
_CALENDAR_END: date = max(entry["end"] for entry in NSE_REGIME_CALENDAR)


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------


def get_regime(
    trade_date: date,
    benchmark_df: pd.DataFrame | None = None,
) -> RegimeType:
    """Return the market regime label for *trade_date*.

    Priority
    --------
    1. NSE_REGIME_CALENDAR — exact calendar lookup (O(n), n≈8).
    2. Slope fallback — when *trade_date* is after the calendar end and
       *benchmark_df* is provided with a ``sma_200`` column:

           slope = sma_200.pct_change(20).iloc[-1]  (using rows up to trade_date)
           slope > +0.0005  →  "Bull"
           slope < -0.0005  →  "Bear"
           else             →  "Sideways"

    3. "Unknown" — date outside calendar and no benchmark_df supplied.

    Parameters
    ----------
    trade_date:
        The date for which to look up the regime.
    benchmark_df:
        Optional DataFrame with a DatetimeIndex and a ``sma_200`` column.
        Only used when *trade_date* falls beyond the calendar end.

    Returns
    -------
    RegimeType
        One of ``"Bull"``, ``"Bear"``, ``"Sideways"``, or ``"Unknown"``.
    """
    # --- Priority 1: calendar lookup ---
    for entry in NSE_REGIME_CALENDAR:
        if entry["start"] <= trade_date <= entry["end"]:
            regime: RegimeType = entry["regime"]
            log.debug("regime calendar hit: %s → %s", trade_date, regime)
            return regime


    # --- Priority 2 / 3: outside calendar ---
    log.debug(
        "regime calendar miss for %s (calendar ends %s); trying slope fallback",
        trade_date,
        _CALENDAR_END,
    )

    if benchmark_df is None:
        log.debug("no benchmark_df provided → Unknown")
        return "Unknown"

    # Validate the required column exists
    if "sma_200" not in benchmark_df.columns:
        log.warning(
            "benchmark_df missing 'sma_200' column; cannot compute slope → Unknown"
        )
        return "Unknown"

    # Slice benchmark up to (and including) trade_date
    trade_ts = pd.Timestamp(trade_date)
    sma_series: pd.Series = benchmark_df.loc[:trade_ts, "sma_200"]

    if len(sma_series) < 21:
        # Need at least 21 rows so pct_change(20) has one valid value
        log.warning(
            "insufficient benchmark rows (%d) to compute 20-period slope → Unknown",
            len(sma_series),
        )
        return "Unknown"

    slope: float = float(sma_series.pct_change(20).iloc[-1])
    log.debug("sma_200 slope for %s: %.6f", trade_date, slope)

    if slope > 0.0005:
        return "Bull"
    if slope < -0.0005:
        return "Bear"
    return "Sideways"


# ---------------------------------------------------------------------------
# Trade labelling helper
# ---------------------------------------------------------------------------


def label_trades(
    trades: list[dict],
    benchmark_df: pd.DataFrame | None = None,
) -> list[dict]:
    """Annotate each trade dict with its market regime.

    Mutates each dict in-place by adding (or overwriting) the ``"regime"``
    key, then returns the same list for chaining convenience.

    Parameters
    ----------
    trades:
        List of trade dicts.  Each dict must contain an ``"entry_date"``
        key whose value is a :class:`datetime.date` (or ISO-8601 string).
    benchmark_df:
        Forwarded verbatim to :func:`get_regime` for the slope fallback.

    Returns
    -------
    list[dict]
        The same list, with ``"regime"`` populated on every element.
    """
    for trade in trades:
        raw_date = trade["entry_date"]
        # Accept both date objects and ISO strings
        if isinstance(raw_date, str):
            entry_date = date.fromisoformat(raw_date)
        else:
            entry_date = raw_date
        trade["regime"] = get_regime(entry_date, benchmark_df)
    return trades


# ---------------------------------------------------------------------------
# Regime statistics
# ---------------------------------------------------------------------------


def get_regime_stats(trades: list[dict]) -> dict[str, dict]:
    """Compute per-regime performance statistics.

    Parameters
    ----------
    trades:
        List of trade dicts.  Each dict must contain:

        * ``"regime"``   — string label (populated by :func:`label_trades`)
        * ``"win"``      — bool/int truthy if the trade was profitable
        * ``"pnl_pct"``  — float, percentage P&L (e.g. 5.2 means +5.2 %)

    Returns
    -------
    dict[str, dict]
        Keyed by regime label.  Each value is::

            {
                "count":       int,
                "win_rate":    float,   # 0.0–1.0, rounded to 3 dp
                "avg_pnl_pct": float,   # rounded to 4 dp
            }

        Only regimes that actually appear in *trades* are included.

    Examples
    --------
    >>> stats = get_regime_stats(labelled_trades)
    >>> stats["Bull"]["win_rate"]
    0.667
    """
    buckets: dict[str, list[dict]] = {}
    for trade in trades:
        regime = trade.get("regime", "Unknown")
        buckets.setdefault(regime, []).append(trade)

    result: dict[str, dict] = {}
    for regime, bucket in buckets.items():
        count = len(bucket)
        wins = sum(1 for t in bucket if t.get("win"))
        pnl_sum = sum(float(t.get("pnl_pct", 0.0)) for t in bucket)
        result[regime] = {
            "count": count,
            "win_rate": round(wins / count, 3) if count else 0.0,
            "avg_pnl_pct": round(pnl_sum / count, 4) if count else 0.0,
        }

    log.debug("regime stats computed for %d regime(s): %s", len(result), list(result))
    return result
