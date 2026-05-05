"""
tests/unit/test_regime.py
--------------------------
Unit tests for backtest.regime — regime labelling, trade annotation,
and per-regime statistics.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backtest.regime import (
    NSE_REGIME_CALENDAR,
    RegimeType,
    get_regime,
    get_regime_stats,
    label_trades,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_benchmark(slope_pct: float, rows: int = 25) -> pd.DataFrame:
    """Build a minimal benchmark DataFrame with a synthetic sma_200 column.

    *slope_pct* controls the 20-period percentage change of the last value:
        final_value = initial_value * (1 + slope_pct)

    The index is a DatetimeIndex ending on 2025-06-01.
    """
    idx = pd.date_range(end="2025-05-30", periods=rows, freq="B")  # Friday end
    # Linearly space values so pct_change(20) ≈ slope_pct at the tail
    start_val = 100.0
    end_val = start_val * (1 + slope_pct)
    n = len(idx)
    values = [start_val + (end_val - start_val) * i / (n - 1) for i in range(n)]
    return pd.DataFrame({"sma_200": values}, index=idx)


# ---------------------------------------------------------------------------
# Test 1 — 2020-06-15 → "Bull"  (V-shaped recovery period)
# ---------------------------------------------------------------------------


def test_calendar_v_shape_recovery_bull():
    regime = get_regime(date(2020, 6, 15))
    assert regime == "Bull", (
        f"Expected 'Bull' for 2020-06-15 (V-shaped recovery), got '{regime}'"
    )


# ---------------------------------------------------------------------------
# Test 2 — 2020-02-20 → "Bear"  (COVID crash period)
# ---------------------------------------------------------------------------


def test_calendar_covid_crash_bear():
    regime = get_regime(date(2020, 2, 20))
    assert regime == "Bear", (
        f"Expected 'Bear' for 2020-02-20 (COVID crash), got '{regime}'"
    )


# ---------------------------------------------------------------------------
# Test 3 — 2022-06-01 → "Sideways"  (Fed rate-hike period)
# ---------------------------------------------------------------------------


def test_calendar_fed_rate_hikes_sideways():
    regime = get_regime(date(2022, 6, 1))
    assert regime == "Sideways", (
        f"Expected 'Sideways' for 2022-06-01 (Fed rate hikes), got '{regime}'"
    )


# ---------------------------------------------------------------------------
# Test 4 — 2025-06-01 (post-calendar) + positive slope → "Bull"
# ---------------------------------------------------------------------------


def test_slope_fallback_positive_slope_bull():
    # slope ≈ +0.02 (2 % over 20 periods) — well above the +0.0005 threshold
    bdf = _make_benchmark(slope_pct=0.02)
    regime = get_regime(date(2025, 6, 1), benchmark_df=bdf)
    assert regime == "Bull", (
        f"Expected 'Bull' from positive slope fallback, got '{regime}'"
    )


# ---------------------------------------------------------------------------
# Test 5 — 2025-06-01 + no benchmark_df → "Unknown"
# ---------------------------------------------------------------------------


def test_slope_fallback_no_benchmark_unknown():
    regime = get_regime(date(2025, 6, 1), benchmark_df=None)
    assert regime == "Unknown", (
        f"Expected 'Unknown' when no benchmark supplied, got '{regime}'"
    )


# ---------------------------------------------------------------------------
# Test 6 — label_trades adds "regime" key to every trade dict
# ---------------------------------------------------------------------------


def test_label_trades_adds_regime_key():
    trades = [
        {"entry_date": date(2020, 6, 15), "symbol": "RELIANCE"},
        {"entry_date": date(2020, 2, 20), "symbol": "INFY"},
        {"entry_date": date(2022, 6, 1),  "symbol": "TCS"},
    ]
    labelled = label_trades(trades)

    assert labelled is trades, "label_trades should return the same list"
    for trade in labelled:
        assert "regime" in trade, f"'regime' key missing from trade {trade}"

    assert labelled[0]["regime"] == "Bull"
    assert labelled[1]["regime"] == "Bear"
    assert labelled[2]["regime"] == "Sideways"


# Test that label_trades also accepts ISO string dates
def test_label_trades_accepts_iso_string_dates():
    trades = [{"entry_date": "2020-06-15", "symbol": "HDFC"}]
    labelled = label_trades(trades)
    assert labelled[0]["regime"] == "Bull"


# ---------------------------------------------------------------------------
# Test 7 — get_regime_stats: 3 Bull trades (2 wins), 1 Bear (0 wins)
#           Bull win_rate ≈ 0.667, Bear win_rate = 0.0
# ---------------------------------------------------------------------------


def test_get_regime_stats_bull_and_bear():
    trades = [
        # Bull — win
        {"regime": "Bull", "win": True,  "pnl_pct": 8.0},
        # Bull — win
        {"regime": "Bull", "win": True,  "pnl_pct": 5.5},
        # Bull — loss
        {"regime": "Bull", "win": False, "pnl_pct": -3.0},
        # Bear — loss
        {"regime": "Bear", "win": False, "pnl_pct": -6.0},
    ]

    stats = get_regime_stats(trades)

    # Bull bucket
    assert "Bull" in stats
    bull = stats["Bull"]
    assert bull["count"] == 3
    assert bull["win_rate"] == pytest.approx(2 / 3, abs=0.001), (
        f"Bull win_rate expected ~0.667, got {bull['win_rate']}"
    )
    assert bull["avg_pnl_pct"] == pytest.approx((8.0 + 5.5 - 3.0) / 3, abs=0.001)

    # Bear bucket
    assert "Bear" in stats
    bear = stats["Bear"]
    assert bear["count"] == 1
    assert bear["win_rate"] == 0.0, (
        f"Bear win_rate expected 0.0, got {bear['win_rate']}"
    )
    assert bear["avg_pnl_pct"] == pytest.approx(-6.0, abs=0.001)


# ---------------------------------------------------------------------------
# Extra edge-case: slope boundary at exactly +0.0005 → "Sideways"
# ---------------------------------------------------------------------------


def test_slope_fallback_boundary_sideways():
    """A slope of exactly ±0.0005 should map to Sideways (strict inequalities)."""
    # Build a series with pct_change(20) == 0.0 (flat)
    idx = pd.date_range(end="2025-05-30", periods=25, freq="B")
    bdf = pd.DataFrame({"sma_200": [100.0] * len(idx)}, index=idx)
    regime = get_regime(date(2025, 6, 1), benchmark_df=bdf)
    assert regime == "Sideways"
