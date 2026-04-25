"""
utils/date_utils.py
-------------------
Timezone-aware date helpers for the SEPA AI screening system.

All "today" calculations are pinned to **Asia/Kolkata (IST, UTC+5:30)**
because NSE operates in that timezone.  Using ``datetime.date.today()``
directly would give the server's local date, which may differ for cloud
deployments hosted outside India.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Union

import pytz

# Indian Standard Time zone object — reused across all calls
_IST = pytz.timezone("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def today_ist() -> date:
    """Return the current calendar date in the Asia/Kolkata timezone.

    Returns
    -------
    datetime.date
        Today's date in IST, regardless of the server's local timezone.

    Examples
    --------
    >>> from utils.date_utils import today_ist
    >>> today_ist()           # e.g. datetime.date(2024, 3, 15)
    """
    return datetime.now(tz=_IST).date()


def to_date(value: Union[str, date, datetime]) -> date:
    """Coerce *value* to a :class:`datetime.date`.

    Accepted input types
    --------------------
    * ``datetime.date``    — returned as-is.
    * ``datetime.datetime``— ``.date()`` is extracted.
    * ``str``              — parsed with the following format attempts in order:
      ``%Y-%m-%d``, ``%d-%m-%Y``, ``%d/%m/%Y``, ``%Y%m%d``.

    Raises
    ------
    ValueError
        If *value* is a string that doesn't match any known format.
    TypeError
        If *value* is not a str, date, or datetime.

    Examples
    --------
    >>> to_date("2024-01-26")
    datetime.date(2024, 1, 26)
    >>> to_date(datetime(2024, 1, 26, 9, 15))
    datetime.date(2024, 1, 26)
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        _FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y%m%d")
        for fmt in _FORMATS:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        raise ValueError(
            f"Cannot parse date string {value!r}. "
            f"Supported formats: {_FORMATS}"
        )
    raise TypeError(
        f"Expected str, date, or datetime; got {type(value).__name__!r}"
    )


def date_range(start: date, end: date) -> list[date]:
    """Return every calendar day between *start* and *end*, inclusive.

    Parameters
    ----------
    start, end:
        Boundary dates.  *start* must be ≤ *end*; an empty list is
        returned if they are equal (single-day range returns [start]).

    Returns
    -------
    list[datetime.date]
        Sorted list of all calendar days from *start* to *end*.

    Examples
    --------
    >>> from datetime import date
    >>> date_range(date(2024, 1, 1), date(2024, 1, 3))
    [datetime.date(2024, 1, 1), datetime.date(2024, 1, 2), datetime.date(2024, 1, 3)]
    """
    from datetime import timedelta

    if start > end:
        return []
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def format_date(dt: date, fmt: str = "%Y-%m-%d") -> str:
    """Format *dt* as a string using *fmt*.

    Parameters
    ----------
    dt:
        The date to format.
    fmt:
        ``strftime``-compatible format string.  Defaults to ISO-8601
        (``%Y-%m-%d``).

    Returns
    -------
    str
        Formatted date string.

    Examples
    --------
    >>> from datetime import date
    >>> format_date(date(2024, 1, 26))
    '2024-01-26'
    >>> format_date(date(2024, 1, 26), "%d %b %Y")
    '26 Jan 2024'
    """
    return dt.strftime(fmt)
