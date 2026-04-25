"""
tests/unit/test_trading_calendar.py
------------------------------------
Unit tests for utils/trading_calendar.py.

Strategy
--------
* Use *known* NSE holidays to verify ``is_trading_day`` returns False.
* Use weekends adjacent to known non-holidays to verify ``next_trading_day``
  and ``prev_trading_day`` navigate correctly.
* Keep all assertions deterministic (no calls to "today").

NSE holidays used in this file (verified against pandas_market_calendars v5.3.2)
----------------------------------------------------------------------------------
2024-01-26  Republic Day         — public holiday, market closed
2024-03-08  Mahashivratri        — NSE market holiday
2024-10-02  Gandhi Jayanti       — public holiday, market closed
2024-11-01  Diwali (Laxmi Puja) — NSE market holiday
"""

from __future__ import annotations

from datetime import date

import pytest

from utils.trading_calendar import (
    is_trading_day,
    next_trading_day,
    prev_trading_day,
    trading_days,
    trading_days_count,
)


# ===========================================================================
# is_trading_day — holidays
# ===========================================================================


class TestIsTradingDayHolidays:
    """NSE public / market holidays must return False."""

    def test_republic_day_2024(self):
        """2024-01-26 is Republic Day — NSE is closed."""
        assert is_trading_day(date(2024, 1, 26)) is False

    def test_gandhi_jayanti_2024(self):
        """2024-10-02 is Gandhi Jayanti — NSE is closed."""
        assert is_trading_day(date(2024, 10, 2)) is False

    def test_diwali_2024(self):
        """2024-11-01 is Diwali (Laxmi Puja) — NSE is closed."""
        assert is_trading_day(date(2024, 11, 1)) is False

    def test_mahashivratri_2024(self):
        """2024-03-08 is Mahashivratri — NSE is closed."""
        assert is_trading_day(date(2024, 3, 8)) is False


# ===========================================================================
# is_trading_day — weekends
# ===========================================================================


class TestIsTradingDayWeekends:
    """Saturdays and Sundays are never NSE trading days."""

    def test_saturday(self):
        assert is_trading_day(date(2024, 1, 20)) is False  # Saturday

    def test_sunday(self):
        assert is_trading_day(date(2024, 1, 21)) is False  # Sunday


# ===========================================================================
# is_trading_day — normal trading days
# ===========================================================================


class TestIsTradingDayNormal:
    """Ordinary weekdays that are not holidays should return True."""

    def test_normal_monday(self):
        # 2024-01-22 is a Monday with no holiday
        assert is_trading_day(date(2024, 1, 22)) is True

    def test_normal_friday(self):
        # 2024-01-19 is a Friday with no holiday
        assert is_trading_day(date(2024, 1, 19)) is True

    def test_day_after_republic_day(self):
        # 2024-01-29 is the Monday after Republic Day weekend — should be open
        assert is_trading_day(date(2024, 1, 29)) is True


# ===========================================================================
# next_trading_day — weekend skipping
# ===========================================================================


class TestNextTradingDay:
    """next_trading_day must skip weekends and holidays."""

    def test_friday_to_monday(self):
        """Next trading day after a normal Friday should be the following Monday."""
        # 2024-01-19 is Friday; 2024-01-22 is Monday (no holiday in between)
        result = next_trading_day(date(2024, 1, 19))
        assert result == date(2024, 1, 22)

    def test_saturday_skips_to_monday(self):
        """Next trading day after Saturday 2024-01-20 should be Monday 2024-01-22."""
        result = next_trading_day(date(2024, 1, 20))
        assert result == date(2024, 1, 22)

    def test_sunday_skips_to_monday(self):
        """Next trading day after Sunday 2024-01-21 should be Monday 2024-01-22."""
        result = next_trading_day(date(2024, 1, 21))
        assert result == date(2024, 1, 22)

    def test_skips_republic_day(self):
        """
        2024-01-25 is Thursday.
        2024-01-26 is Republic Day (holiday).
        2024-01-27 is Saturday.
        2024-01-28 is Sunday.
        Next trading day should be Monday 2024-01-29.
        """
        result = next_trading_day(date(2024, 1, 25))
        assert result == date(2024, 1, 29)


# ===========================================================================
# prev_trading_day — weekend skipping
# ===========================================================================


class TestPrevTradingDay:
    """prev_trading_day must skip weekends and holidays backwards."""

    def test_monday_to_friday(self):
        """Previous trading day before a normal Monday should be Friday."""
        # 2024-01-22 Monday → 2024-01-19 Friday
        result = prev_trading_day(date(2024, 1, 22))
        assert result == date(2024, 1, 19)

    def test_sunday_returns_friday(self):
        """
        Previous trading day before Sunday 2024-01-28 should be Friday 2024-01-26.
        BUT 2024-01-26 is Republic Day, so it should be Thursday 2024-01-25.
        """
        result = prev_trading_day(date(2024, 1, 28))
        assert result == date(2024, 1, 25)

    def test_saturday_returns_friday(self):
        """Previous trading day before Saturday 2024-03-02 should be Friday 2024-03-01."""
        # March 1, 2024 is a Friday with no NSE holiday
        result = prev_trading_day(date(2024, 3, 2))
        assert result == date(2024, 3, 1)

    def test_prev_skips_republic_day(self):
        """
        2024-01-29 Monday → prev should skip:
          2024-01-28 Sunday
          2024-01-27 Saturday
          2024-01-26 Republic Day
        Result: 2024-01-25 Thursday
        """
        result = prev_trading_day(date(2024, 1, 29))
        assert result == date(2024, 1, 25)


# ===========================================================================
# trading_days — count and content
# ===========================================================================


class TestTradingDays:
    """trading_days() should return a DatetimeIndex of NSE trading days."""

    def test_five_day_week_no_holiday(self):
        """A regular Mon–Fri week with no holiday should give exactly 5 days.

        Week of 2024-02-05 (Mon) to 2024-02-09 (Fri) — no NSE holiday.
        """
        days = trading_days("2024-02-05", "2024-02-09")
        assert len(days) == 5

    def test_republic_day_week(self):
        """Week containing Republic Day should give 4 days (Fri is holiday)."""
        # 2024-01-22 Mon to 2024-01-26 Fri; 26-Jan closed
        days = trading_days("2024-01-22", "2024-01-26")
        assert len(days) == 4

    def test_returns_datetime_index(self):
        import pandas as pd
        days = trading_days("2024-01-22", "2024-01-22")
        assert isinstance(days, pd.DatetimeIndex)


# ===========================================================================
# trading_days_count
# ===========================================================================


class TestTradingDaysCount:
    """trading_days_count should equal len(trading_days(...))."""

    def test_count_matches_index_length(self):
        start = date(2024, 1, 22)
        end = date(2024, 1, 26)
        count = trading_days_count(start, end)
        expected = len(trading_days("2024-01-22", "2024-01-26"))
        assert count == expected

    def test_single_trading_day(self):
        # 2024-03-04 is a Monday — should count as 1
        assert trading_days_count(date(2024, 3, 4), date(2024, 3, 4)) == 1

    def test_single_non_trading_day(self):
        # 2024-01-26 Republic Day — should count as 0
        assert trading_days_count(date(2024, 1, 26), date(2024, 1, 26)) == 0

    def test_single_saturday_non_trading(self):
        # 2024-02-10 is a Saturday — should count as 0
        assert trading_days_count(date(2024, 2, 10), date(2024, 2, 10)) == 0
