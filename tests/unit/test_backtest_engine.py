"""
tests/unit/test_backtest_engine.py
-----------------------------------
Unit tests for backtest/engine.py — simulate_trade function.

Tests
-----
1. Price rises then falls to trailing stop → exit_reason="trailing_stop"
2. Trailing stop NEVER drops below VCP floor (critical regression test)
3. Price hits target → exit_reason="target"
4. max_hold_days exceeded → exit_reason="max_hold"
5. trailing_stop_pct=None → uses fixed stop only
6. Trailing stop moves up 3 times as price rises (ratchet property)
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backtest.engine import BacktestTrade, simulate_trade

# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

_CONFIG = {
    "backtest": {
        "trailing_stop_pct": 0.07,
        "target_pct": 0.10,
        "max_hold_days": 20,
    },
    "paper_trading": {
        "initial_capital": 100_000,
        "risk_per_trade_pct": 2.0,
    },
    "data": {"features_dir": "data/features"},
}

_ENTRY_DATE = date(2024, 1, 2)


# ---------------------------------------------------------------------------
# OHLCV helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(closes: list[float], start: date = _ENTRY_DATE) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with a DatetimeIndex from *closes*."""
    n = len(closes)
    idx = pd.date_range(start=start, periods=n, freq="B")
    return pd.DataFrame(
        {
            "open":   closes,
            "high":   [c * 1.01 for c in closes],
            "low":    [c * 0.99 for c in closes],
            "close":  closes,
            "volume": [1_000_000] * n,
        },
        index=idx,
    )


def _make_trending_then_falling(peak: float = 120.0, valley: float = 80.0) -> pd.DataFrame:
    """Rise from 100 → peak, then fall to valley."""
    rising  = [100 + (peak - 100) * i / 4 for i in range(5)]   # 5 steps up
    falling = [peak - (peak - valley) * i / 6 for i in range(7)] # 7 steps down
    return _make_ohlcv(rising + falling)


# ---------------------------------------------------------------------------
# Test 1 — Price rises then falls to trailing stop
# ---------------------------------------------------------------------------

def test_trailing_stop_triggers_after_peak():
    """Price rises to 115 then pulls back to 105 → trailing stop fires."""
    # closes: [100, 105, 110, 115, 110, 105]
    # Day 0 (i=0): entry @100 — no exit check
    # After peak=115: trailing = 115 * 0.93 = 106.95
    # Day 5: close=105 < 106.95 → exit
    closes = [100, 105, 110, 115, 110, 105]
    ohlcv  = _make_ohlcv(closes)

    trade = simulate_trade(
        entry_date=_ENTRY_DATE,
        entry_price=100.0,
        stop_loss_price=85.0,
        ohlcv_df=ohlcv,
        config=_CONFIG,
        trailing_stop_pct=0.07,
    )

    assert trade.exit_reason == "trailing_stop", (
        f"Expected 'trailing_stop', got '{trade.exit_reason}'"
    )
    assert trade.peak_price == pytest.approx(115.0)
    # Trailing stop at peak 115: 115 * 0.93 = 106.95
    assert trade.trailing_stop_used == pytest.approx(115.0 * 0.93)
    assert trade.exit_price == pytest.approx(105.0)


# ---------------------------------------------------------------------------
# Test 2 — Trailing stop NEVER drops below VCP floor (critical regression)
# ---------------------------------------------------------------------------

def test_trailing_stop_never_drops_below_vcp_floor():
    """Critical regression test — trailing stop must always be >= stop_loss_price."""
    ohlcv = _make_trending_then_falling(peak=120.0, valley=80.0)

    trade = simulate_trade(
        entry_date=_ENTRY_DATE,
        entry_price=100.0,
        stop_loss_price=85.0,
        ohlcv_df=ohlcv,
        config=_CONFIG,
        trailing_stop_pct=0.07,
    )

    # When price is at 120, trailing = 120 * 0.93 = 111.6 (well above floor 85)
    # When price falls below 111.6, trailing stop triggers
    assert trade.trailing_stop_used >= 85.0, (
        f"Trailing stop ({trade.trailing_stop_used:.4f}) dropped below VCP floor (85.0)!"
    )
    assert trade.exit_reason == "trailing_stop"
    assert trade.peak_price == pytest.approx(120.0)


def test_trailing_stop_floor_when_price_is_close_to_floor():
    """When 7% below peak is still below the hard floor, floor wins."""
    # peak_close = 90, 7% below = 83.7 < floor 85 → trailing = 85
    closes = [100, 88, 90, 88]   # i=0 entry, i=1 dip, i=2 peak=90, i=3 slight dip
    ohlcv  = _make_ohlcv(closes)

    trade = simulate_trade(
        entry_date=_ENTRY_DATE,
        entry_price=100.0,
        stop_loss_price=85.0,
        ohlcv_df=ohlcv,
        config=_CONFIG,
        trailing_stop_pct=0.07,
    )

    # trailing stop must never go below 85
    assert trade.trailing_stop_used >= 85.0


# ---------------------------------------------------------------------------
# Test 3 — Price hits target
# ---------------------------------------------------------------------------

def test_target_hit():
    """Price rises above entry * 1.10 → exit_reason='target'."""
    # entry=100, target=110.  Day 2 close=112 > 110 → target hit.
    closes = [100, 105, 112]
    ohlcv  = _make_ohlcv(closes)

    trade = simulate_trade(
        entry_date=_ENTRY_DATE,
        entry_price=100.0,
        stop_loss_price=85.0,
        ohlcv_df=ohlcv,
        config=_CONFIG,
        trailing_stop_pct=0.07,
    )

    assert trade.exit_reason == "target", f"Expected 'target', got '{trade.exit_reason}'"
    assert trade.exit_price == pytest.approx(112.0)
    assert trade.pnl_pct > 0.0


# ---------------------------------------------------------------------------
# Test 4 — max_hold_days exceeded
# ---------------------------------------------------------------------------

def test_max_hold_days_exceeded():
    """When no stop or target hit, exit after max_hold_days."""
    cfg = {
        **_CONFIG,
        "backtest": {**_CONFIG["backtest"], "max_hold_days": 5},
    }
    # 7 days of gently rising prices — no stop, no target in 5 days
    closes = [100, 101, 102, 103, 104, 105, 106]
    ohlcv  = _make_ohlcv(closes)

    trade = simulate_trade(
        entry_date=_ENTRY_DATE,
        entry_price=100.0,
        stop_loss_price=85.0,
        ohlcv_df=ohlcv,
        config=cfg,
        trailing_stop_pct=0.07,
    )

    assert trade.exit_reason == "max_hold", (
        f"Expected 'max_hold', got '{trade.exit_reason}'"
    )
    # Should exit on day i=5 (5th session after entry) at close=105
    assert trade.exit_price == pytest.approx(105.0)


# ---------------------------------------------------------------------------
# Test 5 — Fixed stop (trailing_stop_pct=None)
# ---------------------------------------------------------------------------

def test_fixed_stop_no_trailing():
    """With trailing_stop_pct=None, exit when close <= stop_loss_price."""
    # Day 0: entry @100, Day 3: close=84 < stop=85 → fixed stop
    closes = [100, 95, 90, 84]
    ohlcv  = _make_ohlcv(closes)

    trade = simulate_trade(
        entry_date=_ENTRY_DATE,
        entry_price=100.0,
        stop_loss_price=85.0,
        ohlcv_df=ohlcv,
        config=_CONFIG,
        trailing_stop_pct=None,
    )

    assert trade.exit_reason == "fixed_stop", (
        f"Expected 'fixed_stop', got '{trade.exit_reason}'"
    )
    assert trade.exit_price == pytest.approx(84.0)
    assert trade.stop_type == "fixed"
    # trailing_stop_used should equal stop_loss_price (no trailing applied)
    assert trade.trailing_stop_used == pytest.approx(85.0)


def test_fixed_stop_no_trailing_does_not_exit_above_floor():
    """Fixed stop: price stays above stop_loss_price → no premature exit."""
    closes = [100, 99, 97, 90, 88, 86]   # all above 85
    ohlcv  = _make_ohlcv(closes)

    cfg = {**_CONFIG, "backtest": {**_CONFIG["backtest"], "max_hold_days": 4}}

    trade = simulate_trade(
        entry_date=_ENTRY_DATE,
        entry_price=100.0,
        stop_loss_price=85.0,
        ohlcv_df=ohlcv,
        config=cfg,
        trailing_stop_pct=None,
    )

    # Should NOT exit on fixed stop since close never drops to 85
    assert trade.exit_reason in ("max_hold", "target"), (
        f"Unexpected early fixed_stop exit at price {trade.exit_price}"
    )


# ---------------------------------------------------------------------------
# Test 6 — Trailing stop moves up exactly 3 times as price rises in 3 steps
# ---------------------------------------------------------------------------

def test_trailing_stop_ratchets_up_three_times():
    """Trailing stop must have ratcheted upward each time a new peak is set."""
    # Three distinct rising stages: 100 → 110 → 120 → 130, then hold flat
    # We check that trailing_stop_used at exit ≈ 130 * 0.93 = 120.9
    closes = [100, 110, 120, 130, 125, 122, 121]
    ohlcv  = _make_ohlcv(closes)

    # Expected ratchet points:
    #  After peak=110: ts = max(110*0.93, 85) = 102.3
    #  After peak=120: ts = max(120*0.93, 85) = 111.6
    #  After peak=130: ts = max(130*0.93, 85) = 120.9
    # Day close=121 > 120.9 — no exit yet
    # We need price to fall below 120.9 to trigger

    # Add a final bar that dips below the final trailing stop
    closes_with_exit = closes + [120]   # 120 < 120.9 → triggers trailing
    ohlcv2 = _make_ohlcv(closes_with_exit)

    trade = simulate_trade(
        entry_date=_ENTRY_DATE,
        entry_price=100.0,
        stop_loss_price=85.0,
        ohlcv_df=ohlcv2,
        config=_CONFIG,
        trailing_stop_pct=0.07,
    )

    # Peak must have reached 130
    assert trade.peak_price == pytest.approx(130.0), (
        f"Expected peak=130, got {trade.peak_price}"
    )
    # Trailing stop must be at the 3rd ratchet level: 130 * 0.93 = 120.9
    assert trade.trailing_stop_used == pytest.approx(130.0 * 0.93, rel=1e-3)
    assert trade.exit_reason == "trailing_stop"
    # The trailing stop must be greater than it was at the first two ratchet points
    assert trade.trailing_stop_used > 111.6   # > 2nd ratchet
    assert trade.trailing_stop_used > 102.3   # > 1st ratchet


# ---------------------------------------------------------------------------
# Test 7 — Sanity: pnl and r_multiple are consistent
# ---------------------------------------------------------------------------

def test_pnl_and_r_multiple_consistency():
    """pnl_pct and r_multiple should be mathematically consistent."""
    closes = [100, 105, 112]   # target hit at 112
    ohlcv  = _make_ohlcv(closes)

    trade = simulate_trade(
        entry_date=_ENTRY_DATE,
        entry_price=100.0,
        stop_loss_price=90.0,   # risk = 10 per share
        ohlcv_df=ohlcv,
        config=_CONFIG,
        trailing_stop_pct=0.07,
    )

    # pnl_pct = (112/100 - 1)*100 = 12.0
    assert trade.pnl_pct == pytest.approx(12.0, rel=1e-3)
    # r_multiple = (112 - 100) / (100 - 90) = 12 / 10 = 1.2
    assert trade.r_multiple == pytest.approx(1.2, rel=1e-3)
    # pnl = (112 - 100) * quantity
    assert trade.pnl == pytest.approx(12.0 * trade.quantity, rel=1e-3)


# ---------------------------------------------------------------------------
# Test 8 — Empty ohlcv_df graceful handling
# ---------------------------------------------------------------------------

def test_empty_ohlcv_df_returns_graceful_trade():
    """Empty OHLCV should return a zero-change trade without raising."""
    ohlcv = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    trade = simulate_trade(
        entry_date=_ENTRY_DATE,
        entry_price=100.0,
        stop_loss_price=85.0,
        ohlcv_df=ohlcv,
        config=_CONFIG,
        trailing_stop_pct=0.07,
    )

    assert trade.entry_price == pytest.approx(100.0)
    assert trade.exit_price  == pytest.approx(100.0)
    assert trade.pnl         == pytest.approx(0.0)
    assert trade.exit_reason == "max_hold"


# ---------------------------------------------------------------------------
# Tests for WindowGateStats and BacktestResult.gate_stats (Phase 8 remaining)
# ---------------------------------------------------------------------------

from backtest.engine import BacktestResult, WindowGateStats
from datetime import date as _date


def test_backtest_result_has_gate_stats_field():
    """BacktestResult must have a gate_stats field (list, default empty)."""
    result = BacktestResult(
        start_date=_date(2024, 1, 1),
        end_date=_date(2024, 1, 31),
        trades=[],
        universe_size=100,
        config_snapshot={},
    )
    assert hasattr(result, "gate_stats"), "BacktestResult missing gate_stats field"
    assert isinstance(result.gate_stats, list)
    assert len(result.gate_stats) == 0


def test_window_gate_stats_dataclass_fields():
    """WindowGateStats must expose all required fields with correct types."""
    gs = WindowGateStats(
        date=_date(2024, 1, 15),
        screened=120,
        passed_stage2=45,
        passed_tt=30,
        vcp_qualified=12,
        entered_positions=3,
    )
    assert gs.date            == _date(2024, 1, 15)
    assert gs.screened        == 120
    assert gs.passed_stage2   == 45
    assert gs.passed_tt       == 30
    assert gs.vcp_qualified   == 12
    assert gs.entered_positions == 3


def test_gate_stats_counts_are_logically_consistent():
    """Stage2 >= TT >= VCP >= entered should hold for any real run window."""
    # A symbol that passes VCP must have passed Stage2 + TT first,
    # so vcp_qualified <= passed_tt <= passed_stage2 <= screened.
    gs = WindowGateStats(
        date=_date(2024, 2, 1),
        screened=200,
        passed_stage2=80,
        passed_tt=50,
        vcp_qualified=20,
        entered_positions=5,
    )
    assert gs.screened       >= gs.passed_stage2
    assert gs.passed_stage2  >= gs.passed_tt
    assert gs.passed_tt      >= gs.vcp_qualified
    assert gs.vcp_qualified  >= gs.entered_positions


def test_backtest_result_gate_stats_can_hold_multiple_windows():
    """BacktestResult.gate_stats accepts a list of WindowGateStats entries."""
    windows = [
        WindowGateStats(_date(2024, 1, 2), 100, 40, 25, 10, 2),
        WindowGateStats(_date(2024, 1, 3), 102, 42, 26, 11, 3),
        WindowGateStats(_date(2024, 1, 4), 98, 38, 22, 9, 1),
    ]
    result = BacktestResult(
        start_date=_date(2024, 1, 2),
        end_date=_date(2024, 1, 4),
        trades=[],
        universe_size=102,
        config_snapshot={},
        gate_stats=windows,
    )
    assert len(result.gate_stats) == 3
    assert result.gate_stats[0].passed_stage2 == 40
    assert result.gate_stats[2].entered_positions == 1


def test_gate_stats_total_computations():
    """Verify aggregate totals used in report are arithmetically correct."""
    windows = [
        WindowGateStats(_date(2024, 1, 2), 100, 40, 20, 8, 2),
        WindowGateStats(_date(2024, 1, 3), 120, 50, 30, 10, 3),
    ]
    total_screened = sum(gs.screened      for gs in windows)
    total_stage2   = sum(gs.passed_stage2 for gs in windows)
    total_tt       = sum(gs.passed_tt     for gs in windows)
    total_vcp      = sum(gs.vcp_qualified for gs in windows)
    total_entered  = sum(gs.entered_positions for gs in windows)

    assert total_screened == 220
    assert total_stage2   == 90
    assert total_tt       == 50
    assert total_vcp      == 18
    assert total_entered  == 5

    # Pass-rate sanity
    assert total_stage2 / total_screened == pytest.approx(90 / 220, rel=1e-6)
    assert total_vcp    / total_screened == pytest.approx(18 / 220, rel=1e-6)
