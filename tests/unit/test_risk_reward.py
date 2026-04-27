"""
tests/unit/test_risk_reward.py
-------------------------------
Unit tests for rules/risk_reward.py — compute_risk_reward().

Pure numeric tests — no DataFrame or I/O required.
"""

from __future__ import annotations

import pytest

from rules.risk_reward import compute_risk_reward

# ---------------------------------------------------------------------------
# Shared config.
# ---------------------------------------------------------------------------

_CFG: dict = {
    "risk_reward": {
        "min_rr_ratio": 2.0,
    }
}


# ---------------------------------------------------------------------------
# Test 1 — standard 2R target (no resistance).
# ---------------------------------------------------------------------------

def test_risk_reward_standard_2r():
    """entry=100, stop=93 → risk=7, target=114 (2R), rr=2.0."""
    target, risk, rr = compute_risk_reward(
        entry_price=100.0, stop_price=93.0, config=_CFG
    )

    assert risk == pytest.approx(7.0)
    assert target == pytest.approx(114.0)     # 100 + 7 * 2
    assert rr == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Test 2 — resistance above entry → use resistance as target.
# ---------------------------------------------------------------------------

def test_risk_reward_uses_resistance_as_target():
    """entry=100, stop=93, resistance=120 → target=120, rr≈2.857."""
    target, risk, rr = compute_risk_reward(
        entry_price=100.0,
        stop_price=93.0,
        config=_CFG,
        resistance_price=120.0,
    )

    assert target == pytest.approx(120.0)
    assert risk == pytest.approx(7.0)
    assert rr == pytest.approx(20.0 / 7.0, rel=1e-4)  # ≈ 2.857


# ---------------------------------------------------------------------------
# Test 3 — entry <= stop → (0, 0, 0).
# ---------------------------------------------------------------------------

def test_risk_reward_invalid_entry_lte_stop():
    """entry <= stop → returns (0.0, 0.0, 0.0)."""
    # entry == stop
    result = compute_risk_reward(entry_price=93.0, stop_price=93.0, config=_CFG)
    assert result == (0.0, 0.0, 0.0)

    # entry < stop
    result = compute_risk_reward(entry_price=90.0, stop_price=93.0, config=_CFG)
    assert result == (0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Test 4 — resistance below entry is ignored → falls back to min_rr target.
# ---------------------------------------------------------------------------

def test_risk_reward_resistance_below_entry_ignored():
    """resistance=95 < entry=100 → resistance ignored, use 2R target."""
    target, risk, rr = compute_risk_reward(
        entry_price=100.0,
        stop_price=93.0,
        config=_CFG,
        resistance_price=95.0,  # below entry → should be ignored
    )

    assert target == pytest.approx(114.0)
    assert rr == pytest.approx(2.0)
