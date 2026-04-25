"""
utils/trading_calendar.py
--------------------------
NSE trading-day helpers built on ``pandas_market_calendars``.

All public functions work with plain ``datetime.date`` objects so callers
don't have to think about pandas timestamps or timezones.

Caching
-------
``trading_days()`` is decorated with ``@lru_cache(maxsize=10)`` keyed on
the ISO-format string pair ``(start, end)``.  The cache is warm for the
lifetime of the process; a full pipeline run typically calls the function
with at most 2-3 distinct ranges, so 10 slots is generous.

Calendar name
-------------
``pandas_market_calendars`` ships an "NSE" calendar that includes all
gazetted Indian public holidays as well as NSE-specific market holidays.
It reflects Bombay Stock Exchange closures as well, which for NSE
purposes are identical.
"""

from __future__ import annotations

import functools
from datetime import date, timedelta

import pandas as pd
import pandas_market_calendars as mcal

# ---------------------------------------------------------------------------
# Module-level calendar instance (cheap to create, so we keep one around)
# ---------------------------------------------------------------------------

_NSE = mcal.get_calendar("NSE")


# ---------------------------------------------------------------------------
# Core helper — cached
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=10)
def trading_days(start: str, end: str) -> pd.DatetimeIndex:
    """Return all NSE trading days between *start* and *end*, inclusive.

    Parameters
    ----------
    start, end:
        ISO-8601 date strings (``"YYYY-MM-DD"``).  String arguments are
        required (rather than ``date`` objects) so the result is hashable
        and can be cached by ``lru_cache``.

    Returns
    -------
    pd.DatetimeIndex
        Sorted index of trading-day timestamps (tz-naive, midnight UTC).

    Examples
    --------
    >>> td = trading_days("2024-01-22", "2024-01-26")
    >>> len(td)   # 5 trading days; 26-Jan is Republic Day — holiday
    4
    """
    schedule = _NSE.schedule(start_date=start, end_date=end)
    # Guard: mcal.date_range() raises AttributeError on an empty schedule
    # (holidays, weekends, or ranges with no trading days return empty DataFrames).
    if schedule.empty:
        return pd.DatetimeIndex([])
    return mcal.date_range(schedule, frequency="1D")


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------


def is_trading_day(dt: date) -> bool:
    """Return ``True`` if *dt* is an NSE trading day.

    Parameters
    ----------
    dt:
        Calendar date to check.

    Examples
    --------
    >>> from datetime import date
    >>> is_trading_day(date(2024, 1, 26))   # Republic Day
    False
    >>> is_trading_day(date(2024, 1, 22))   # Normal Monday
    True
    """
    iso = dt.isoformat()
    days = trading_days(iso, iso)
    return len(days) > 0


def next_trading_day(dt: date) -> date:
    """Return the first NSE trading day *strictly after* *dt*.

    Scans forward up to 14 calendar days (covers any long holiday bridge
    including Christmas + New Year combos) and raises ``RuntimeError``
    if no trading day is found within that window.

    Parameters
    ----------
    dt:
        Reference date.

    Returns
    -------
    datetime.date
        Next trading day after *dt*.
    """
    candidate = dt + timedelta(days=1)
    for _ in range(14):
        if is_trading_day(candidate):
            return candidate
        candidate += timedelta(days=1)
    raise RuntimeError(
        f"Could not find the next NSE trading day within 14 days of {dt}"
    )


def prev_trading_day(dt: date) -> date:
    """Return the first NSE trading day *strictly before* *dt*.

    Scans backward up to 14 calendar days and raises ``RuntimeError``
    if no trading day is found within that window.

    Parameters
    ----------
    dt:
        Reference date.

    Returns
    -------
    datetime.date
        Previous trading day before *dt*.

    Examples
    --------
    >>> from datetime import date
    >>> prev_trading_day(date(2024, 1, 28))  # Sunday → Friday 26-Jan is holiday
    datetime.date(2024, 1, 25)
    """
    candidate = dt - timedelta(days=1)
    for _ in range(14):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    raise RuntimeError(
        f"Could not find the previous NSE trading day within 14 days of {dt}"
    )


def trading_days_count(start: date, end: date) -> int:
    """Return the number of NSE trading days between *start* and *end*, inclusive.

    Parameters
    ----------
    start, end:
        Boundary dates.

    Returns
    -------
    int
        Count of trading days in the range [start, end].

    Examples
    --------
    >>> from datetime import date
    >>> trading_days_count(date(2024, 1, 22), date(2024, 1, 26))
    4  # 26-Jan is Republic Day
    """
    days = trading_days(start.isoformat(), end.isoformat())
    return len(days)
