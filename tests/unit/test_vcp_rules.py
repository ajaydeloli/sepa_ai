"""
tests/unit/test_vcp_rules.py
----------------------------
Unit tests for rules/vcp_rules.py — qualify_vcp().

All tests build VCPMetrics directly; no DataFrame or I/O required.
"""

from __future__ import annotations

import pytest

from features.vcp import VCPMetrics
from rules.vcp_rules import qualify_vcp

# ---------------------------------------------------------------------------
# Shared config matching settings.yaml defaults.
# ---------------------------------------------------------------------------

_CFG: dict = {
    "vcp": {
        "min_contractions": 2,
        "max_contractions": 5,
        "require_vol_contraction": True,
        "min_weeks": 3,
        "max_weeks": 52,
        "tightness_pct": 10.0,
        "max_depth_pct": 50.0,
    }
}


def _make_metrics(**overrides) -> VCPMetrics:
    """Return a fully-valid VCPMetrics; individual fields can be overridden."""
    defaults = dict(
        contraction_count=3,
        max_depth_pct=20.0,      # deepest leg
        final_depth_pct=8.0,     # shallower than max_depth_pct → declining ✓
        vol_contraction_ratio=0.6,
        base_length_weeks=8,
        base_low=80.0,
        is_valid_vcp=True,
        tightness_score=5.0,
    )
    defaults.update(overrides)
    return VCPMetrics(**defaults)


# ---------------------------------------------------------------------------
# Test 1 — golden path: valid VCP qualifies.
# ---------------------------------------------------------------------------

def test_qualify_vcp_golden_path():
    """3 contractions, declining depth, vol dry-up → qualified==True."""
    metrics = _make_metrics()
    qualified, details = qualify_vcp(metrics, _CFG)

    assert qualified is True
    assert all(details.values()), f"Expected all detail rules to pass: {details}"


# ---------------------------------------------------------------------------
# Test 2 — too few contractions.
# ---------------------------------------------------------------------------

def test_qualify_vcp_too_few_contractions():
    """1 contraction < min_contractions=2 → qualified==False."""
    metrics = _make_metrics(contraction_count=1, is_valid_vcp=True)
    qualified, details = qualify_vcp(metrics, _CFG)

    assert qualified is False
    assert details["contraction_count_min"] is False


# ---------------------------------------------------------------------------
# Test 3 — depth not declining (final_depth_pct >= max_depth_pct).
# ---------------------------------------------------------------------------

def test_qualify_vcp_depth_not_declining():
    """final_depth_pct > max_depth_pct → declining_depth rule fails."""
    # final_depth=25.0 > max_depth=20.0 means depth is INCREASING, not declining.
    metrics = _make_metrics(
        max_depth_pct=20.0,
        final_depth_pct=25.0,
        is_valid_vcp=True,
    )
    qualified, details = qualify_vcp(metrics, _CFG)

    assert qualified is False
    assert details["declining_depth"] is False


# ---------------------------------------------------------------------------
# Test 4 — tightness_score too high.
# ---------------------------------------------------------------------------

def test_qualify_vcp_tightness_too_high():
    """tightness_score=12 > tightness_pct=10 → tightness_score rule fails."""
    metrics = _make_metrics(tightness_score=12.0, is_valid_vcp=True)
    qualified, details = qualify_vcp(metrics, _CFG)

    assert qualified is False
    assert details["tightness_score"] is False


# ---------------------------------------------------------------------------
# Test 5 — is_valid_vcp=False → immediate early return.
# ---------------------------------------------------------------------------

def test_qualify_vcp_invalid_vcp_early_exit():
    """qualify_vcp with metrics.is_valid_vcp=False returns (False, ...) immediately."""
    # Even though these individual metrics would otherwise pass, the detector
    # has already flagged this as invalid.
    metrics = _make_metrics(is_valid_vcp=False)
    qualified, details = qualify_vcp(metrics, _CFG)

    assert qualified is False
    # All detail flags must be False (early-exit path).
    assert all(v is False for v in details.values()), (
        f"Expected all detail flags False on early exit, got: {details}"
    )
