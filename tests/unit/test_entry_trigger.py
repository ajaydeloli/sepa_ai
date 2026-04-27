"""
tests/unit/test_entry_trigger.py
---------------------------------
Unit tests for rules/entry_trigger.py — check_entry_trigger().

Row objects are built as plain pd.Series; no DataFrame I/O required.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from rules.entry_trigger import EntryTrigger, check_entry_trigger

# ---------------------------------------------------------------------------
# Shared config.
# ---------------------------------------------------------------------------

_CFG: dict = {
    "entry": {
        "breakout_buffer_pct": 0.001,   # 0.1 %
        "breakout_vol_threshold": 1.5,
    }
}


def _row(close: float, pivot_high: float | float, vol_ratio: float = 1.0) -> pd.Series:
    return pd.Series({"close": close, "pivot_high": pivot_high, "vol_ratio": vol_ratio})


# ---------------------------------------------------------------------------
# Test 1 — clean breakout with volume confirmation.
# ---------------------------------------------------------------------------

def test_entry_trigger_breakout_with_vol():
    """close=102 > pivot_high=100 * 1.001=100.1 and vol_ratio=2.0 >= 1.5."""
    row = _row(close=102.0, pivot_high=100.0, vol_ratio=2.0)
    result = check_entry_trigger(row, _CFG)

    assert result.triggered is True
    assert result.volume_confirmed is True
    assert result.pivot_high == pytest.approx(100.0)
    # entry_price should be the breakout level
    assert result.entry_price == pytest.approx(100.0 * 1.001)
    assert "breakout" in result.reason.lower()


# ---------------------------------------------------------------------------
# Test 2 — price below pivot high → no trigger.
# ---------------------------------------------------------------------------

def test_entry_trigger_no_breakout():
    """close=99 < breakout_level=100.1 → triggered==False."""
    row = _row(close=99.0, pivot_high=100.0, vol_ratio=2.0)
    result = check_entry_trigger(row, _CFG)

    assert result.triggered is False
    assert result.entry_price is None


# ---------------------------------------------------------------------------
# Test 3 — breakout price satisfied but volume below threshold.
# ---------------------------------------------------------------------------

def test_entry_trigger_breakout_no_vol():
    """close=101, vol_ratio=1.2 < 1.5 → triggered=True, volume_confirmed=False."""
    row = _row(close=101.0, pivot_high=100.0, vol_ratio=1.2)
    result = check_entry_trigger(row, _CFG)

    assert result.triggered is True
    assert result.volume_confirmed is False


# ---------------------------------------------------------------------------
# Test 4 — pivot_high is NaN → no trigger, no exception.
# ---------------------------------------------------------------------------

def test_entry_trigger_nan_pivot_high():
    """pivot_high=NaN → triggered=False, no exception raised."""
    row = _row(close=110.0, pivot_high=float("nan"), vol_ratio=3.0)
    result = check_entry_trigger(row, _CFG)

    assert result.triggered is False
    assert result.entry_price is None
    assert result.pivot_high is None
    assert "no pivot high" in result.reason.lower()
