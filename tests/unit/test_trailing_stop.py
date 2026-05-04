"""
tests/unit/test_trailing_stop.py
---------------------------------
Unit tests for apply_trailing_stop logic and its integration with
check_exits.

Tests
-----
1. Trailing stop moves up as price rises (peak_close advances).
2. Trailing stop is floored at VCP stop_loss when 7 % below peak is lower.
3. Trailing stop does NOT decrease on a price pullback (ratchet property).
4. check_exits fires "trailing_stop" when price drops below the stop.
5. check_exits does NOT fire while price remains above the trailing stop.
"""

from __future__ import annotations

from datetime import date

import pytest

from paper_trading.portfolio import Portfolio, Position
from paper_trading.simulator import apply_trailing_stop, check_exits

# ---------------------------------------------------------------------------
# Shared config used by all tests in this module
# ---------------------------------------------------------------------------

_CONFIG = {
    "paper_trading": {
        "initial_capital": 100_000.0,
        "max_positions": 10,
        "risk_per_trade_pct": 2.0,
        "slippage_pct": 0.15,
        "brokerage_pct": 0.0,       # zero brokerage so pnl checks stay simple
        "min_score_to_trade": 70,
        "max_hold_days": 20,
    },
    "backtest": {
        "trailing_stop_pct": 0.07,  # 7 %
    },
}

_RUN_DATE = date(2024, 4, 1)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_position(
    entry_price: float = 1_000.0,
    stop_loss: float = 900.0,
    peak_close: float = 1_000.0,
    trailing_stop: float = 0.0,
    days_held: int = 0,
    target_price: float = 1_400.0,
) -> Position:
    return Position(
        symbol="TEST",
        entry_date=date(2024, 1, 15),
        entry_price=entry_price,
        quantity=10,
        stop_loss=stop_loss,
        target_price=target_price,
        sepa_score=80,
        setup_quality="A",
        peak_close=peak_close,
        trailing_stop=trailing_stop,
        days_held=days_held,
    )


def _make_portfolio(pos: Position) -> Portfolio:
    p = Portfolio(initial_capital=100_000.0, config=_CONFIG)
    p.positions[pos.symbol] = pos
    p.cash = 90_000.0   # entry cost already deducted
    return p


# ---------------------------------------------------------------------------
# Test 1 — Trailing stop moves up as price rises
# ---------------------------------------------------------------------------


def test_trailing_stop_moves_up_with_rising_price():
    """Each new higher peak causes apply_trailing_stop to return a higher value."""
    pos = _make_position(peak_close=1_000.0, trailing_stop=0.0)

    stop_at_1000 = apply_trailing_stop(pos, 1_000.0, _CONFIG)
    assert stop_at_1000 == pytest.approx(1_000.0 * 0.93)  # 930.0

    # Price rises to 1_200 — update peak and call again
    pos.peak_close = 1_200.0
    pos.trailing_stop = stop_at_1000
    stop_at_1200 = apply_trailing_stop(pos, 1_200.0, _CONFIG)

    assert stop_at_1200 == pytest.approx(1_200.0 * 0.93)  # 1_116.0
    assert stop_at_1200 > stop_at_1000, "Trailing stop must increase when price rises"


# ---------------------------------------------------------------------------
# Test 2 — Trailing stop never drops below VCP floor (position.stop_loss)
# ---------------------------------------------------------------------------


def test_trailing_stop_floored_at_vcp_stop_loss():
    """When 7 % below peak_close < stop_loss, return stop_loss as the floor."""
    # 7 % below 930 = 864.9, which is below stop_loss 900 → floor kicks in
    pos = _make_position(stop_loss=900.0, peak_close=930.0, trailing_stop=0.0)

    stop = apply_trailing_stop(pos, 930.0, _CONFIG)

    assert stop >= 900.0, "Trailing stop must never drop below VCP stop_loss"
    assert stop == pytest.approx(900.0)


# ---------------------------------------------------------------------------
# Test 3 — Trailing stop does not decrease on a price pullback
# ---------------------------------------------------------------------------


def test_trailing_stop_ratchet_never_decreases():
    """Once the trailing stop has reached level X it must not fall below X."""
    # peak was 1_200 → stop was set to 1_116 (1_200 × 0.93)
    pos = _make_position(
        stop_loss=900.0,
        peak_close=1_200.0,
        trailing_stop=1_116.0,
    )

    # Price pulls back to 1_050 — peak_close stays at 1_200 (not updated here)
    new_stop = apply_trailing_stop(pos, 1_050.0, _CONFIG)

    assert new_stop >= 1_116.0, (
        "Trailing stop must not decrease on a pullback "
        f"(got {new_stop:.2f} < 1116.0)"
    )
    assert new_stop == pytest.approx(1_116.0)


# ---------------------------------------------------------------------------
# Test 4 — Exit triggered when price drops below trailing stop
# ---------------------------------------------------------------------------


def test_check_exits_trailing_stop_triggers_exit():
    """Price falling to or below trailing_stop must close the position."""
    pos = _make_position(
        stop_loss=900.0,
        peak_close=1_200.0,
        trailing_stop=1_116.0,  # 1_200 × 0.93
    )
    portfolio = _make_portfolio(pos)

    # Price at 1_100 — below trailing stop 1_116
    closed = check_exits(portfolio, {"TEST": 1_100.0}, _RUN_DATE)

    assert len(closed) == 1, "Expected exactly one ClosedTrade"
    assert closed[0].symbol == "TEST"
    assert closed[0].exit_reason == "trailing_stop"
    assert closed[0].exit_price == pytest.approx(1_100.0)
    assert "TEST" not in portfolio.positions


# ---------------------------------------------------------------------------
# Test 5 — Exit NOT triggered while price stays above trailing stop
# ---------------------------------------------------------------------------


def test_check_exits_no_exit_while_price_above_trailing_stop():
    """Position must remain open when price is comfortably above the stop."""
    pos = _make_position(
        stop_loss=900.0,
        peak_close=1_000.0,
        trailing_stop=930.0,   # 1_000 × 0.93
        days_held=1,
    )
    portfolio = _make_portfolio(pos)

    # Price at 1_080 — safely above trailing stop 930
    closed = check_exits(portfolio, {"TEST": 1_080.0}, _RUN_DATE)

    assert len(closed) == 0, "No exit expected while price is above trailing stop"
    assert "TEST" in portfolio.positions
    # Trailing stop should have ratcheted up (new peak = 1_080)
    assert portfolio.positions["TEST"].peak_close == pytest.approx(1_080.0)
    assert portfolio.positions["TEST"].trailing_stop >= 930.0
