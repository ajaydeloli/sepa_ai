"""
tests/unit/test_stop_loss.py
-----------------------------
Unit tests for rules/stop_loss.py — compute_stop_loss().

Row objects are built as plain pd.Series; no DataFrame I/O required.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from rules.stop_loss import compute_stop_loss

# ---------------------------------------------------------------------------
# Shared config.
# ---------------------------------------------------------------------------

_CFG: dict = {
    "stop_loss": {
        "stop_buffer_pct": 0.005,
        "max_risk_pct":    15.0,
        "atr_multiplier":  2.0,
        "fixed_stop_pct":  0.07,
    }
}


def _row(close: float, atr_14: float = 3.0) -> pd.Series:
    return pd.Series({"close": close, "atr_14": atr_14})


# ---------------------------------------------------------------------------
# Test 1 — VCP base_low within risk budget → uses vcp_base_low method.
# ---------------------------------------------------------------------------

def test_stop_loss_vcp_method():
    """VCP base_low=86, close=100 → risk≈14.43% ≤ 15% → method='vcp_base_low'.

    Note: base_low=85 gives stop=84.575, risk=15.425% which *exceeds* max_risk_pct=15.0
    and correctly falls back to ATR. Use base_low=86 so risk stays inside the budget.
    """
    row = _row(close=100.0, atr_14=3.0)
    stop, risk_pct, method = compute_stop_loss(row, vcp_base_low=86.0, config=_CFG)

    assert method == "vcp_base_low"
    # stop = 86 * (1 - 0.005) = 85.57
    assert stop == pytest.approx(86.0 * 0.995, rel=1e-6)
    assert risk_pct is not None
    assert 0.0 < risk_pct <= 15.0


# ---------------------------------------------------------------------------
# Test 2 — VCP base_low gives risk > max_risk_pct → falls back to ATR.
# ---------------------------------------------------------------------------

def test_stop_loss_vcp_risk_too_wide_falls_back_to_atr():
    """VCP base_low=50 gives risk=50.25%>15% → fallback to ATR method."""
    row = _row(close=100.0, atr_14=3.0)
    stop, risk_pct, method = compute_stop_loss(row, vcp_base_low=50.0, config=_CFG)

    assert method == "atr"
    # stop = 100 - (3 * 2.0) = 94.0
    assert stop == pytest.approx(94.0, rel=1e-6)
    assert risk_pct == pytest.approx(6.0, rel=1e-3)


# ---------------------------------------------------------------------------
# Test 3 — vcp_base_low=None → falls back to ATR.
# ---------------------------------------------------------------------------

def test_stop_loss_no_vcp_base_low_falls_back_to_atr():
    """vcp_base_low=None → skip VCP method, go straight to ATR."""
    row = _row(close=100.0, atr_14=3.0)
    stop, risk_pct, method = compute_stop_loss(row, vcp_base_low=None, config=_CFG)

    assert method == "atr"
    assert stop == pytest.approx(94.0, rel=1e-6)


# ---------------------------------------------------------------------------
# Test 4 — close=0 → returns (None, None, "no_data").
# ---------------------------------------------------------------------------

def test_stop_loss_zero_close_returns_no_data():
    """close=0 → (None, None, 'no_data') — no exception raised."""
    row = _row(close=0.0, atr_14=3.0)
    stop, risk_pct, method = compute_stop_loss(row, vcp_base_low=85.0, config=_CFG)

    assert stop is None
    assert risk_pct is None
    assert method == "no_data"
