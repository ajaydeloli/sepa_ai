"""
tests/unit/test_paper_trading.py
---------------------------------
Unit tests for the paper_trading module.

Coverage
--------
1.  enter_trade: valid A+ result → Position created, cash reduced
2.  enter_trade: position count at max → returns None
3.  enter_trade: symbol already held → returns None
4.  pyramid_position: already pyramided → returns None
5.  pyramid_position: valid VCP Grade A + vol dry-up → pyramid_qty set
6.  check_exits: price hits stop_loss → ClosedTrade exit_reason=="stop_loss"
7.  check_exits: price hits target → ClosedTrade exit_reason=="target"
8.  Portfolio.get_summary returns correct total_return_pct
9.  Portfolio.to_json / from_json round-trip (no data loss)
10. Non-trading day → order queued, enter_trade returns None

All tests are self-contained — no real filesystem writes, no real clock
reads.  is_trading_day and _is_market_hours are patched where needed.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from paper_trading.portfolio import ClosedTrade, Portfolio, Position
from paper_trading.simulator import check_exits, enter_trade, pyramid_position
from rules.scorer import SEPAResult


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_CONFIG = {
    "paper_trading": {
        "initial_capital": 100_000.0,
        "max_positions": 3,
        "risk_per_trade_pct": 2.0,
        "slippage_pct": 0.15,   # 0.15 % → multiplier 0.0015
        "min_score_to_trade": 70,
    }
}


def _make_portfolio(cash: float = 100_000.0) -> Portfolio:
    p = Portfolio(initial_capital=cash, config=_CONFIG)
    p.cash = cash
    return p


def _make_result(
    symbol: str = "TCS",
    stage: int = 2,
    score: int = 85,
    setup_quality: str = "A+",
    stop_loss: float = 3_500.0,
    entry_price: float = 3_800.0,
    target_price: float = 4_600.0,
    vcp_qualified: bool = True,
    vcp_details: dict | None = None,
) -> SEPAResult:
    return SEPAResult(
        symbol=symbol,
        run_date=date(2024, 3, 15),
        stage=stage,
        stage_label="Stage 2 Uptrend",
        stage_confidence=85,
        trend_template_pass=True,
        trend_template_details={},
        conditions_met=8,
        vcp_qualified=vcp_qualified,
        vcp_details=vcp_details or {},
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_price=target_price,
        setup_quality=setup_quality,
        score=score,
    )


def _open_position(
    portfolio: Portfolio,
    symbol: str = "TCS",
    entry_price: float = 3_800.0,
    stop_loss: float = 3_500.0,
    target_price: float = 4_600.0,
    quantity: int = 5,
) -> Position:
    pos = Position(
        symbol=symbol,
        entry_date=date(2024, 3, 10),
        entry_price=entry_price,
        quantity=quantity,
        stop_loss=stop_loss,
        target_price=target_price,
        sepa_score=85,
        setup_quality="A+",
    )
    portfolio.positions[symbol] = pos
    portfolio.cash -= entry_price * quantity
    return pos


# ---------------------------------------------------------------------------
# Test 1 — enter_trade: valid A+ result → Position created, cash reduced
# ---------------------------------------------------------------------------


@patch("paper_trading.simulator._is_market_hours", return_value=True)
@patch("paper_trading.simulator.is_trading_day", return_value=True)
def test_enter_trade_valid_fills_immediately(mock_td, mock_mh):
    portfolio = _make_portfolio(100_000.0)
    result = _make_result()
    current_price = 3_800.0
    run_date = date(2024, 3, 15)

    position = enter_trade(result, portfolio, current_price, run_date)

    assert position is not None, "Expected a Position to be returned"
    assert position.symbol == "TCS"
    assert position.quantity >= 1
    # Cash must be reduced by exactly entry_price × quantity
    expected_cash = 100_000.0 - position.entry_price * position.quantity
    assert abs(portfolio.cash - expected_cash) < 0.01
    assert "TCS" in portfolio.positions
    # Slippage applied: fill > current_price
    assert position.entry_price > current_price


# ---------------------------------------------------------------------------
# Test 2 — enter_trade: position count at max → returns None
# ---------------------------------------------------------------------------


@patch("paper_trading.simulator._is_market_hours", return_value=True)
@patch("paper_trading.simulator.is_trading_day", return_value=True)
def test_enter_trade_max_positions_returns_none(mock_td, mock_mh):
    portfolio = _make_portfolio()
    # Fill up to max_positions (3 in our config)
    for sym in ("AAA", "BBB", "CCC"):
        _open_position(portfolio, symbol=sym, quantity=1)

    result = _make_result(symbol="NEW")
    position = enter_trade(result, portfolio, 3_800.0, date(2024, 3, 15))

    assert position is None
    assert "NEW" not in portfolio.positions


# ---------------------------------------------------------------------------
# Test 3 — enter_trade: symbol already held → returns None
# ---------------------------------------------------------------------------


@patch("paper_trading.simulator._is_market_hours", return_value=True)
@patch("paper_trading.simulator.is_trading_day", return_value=True)
def test_enter_trade_already_held_returns_none(mock_td, mock_mh):
    portfolio = _make_portfolio()
    _open_position(portfolio, symbol="TCS", quantity=2)
    original_cash = portfolio.cash

    result = _make_result(symbol="TCS")
    position = enter_trade(result, portfolio, 3_800.0, date(2024, 3, 15))

    assert position is None
    assert abs(portfolio.cash - original_cash) < 0.01  # cash unchanged


# ---------------------------------------------------------------------------
# Test 4 — pyramid_position: already pyramided → returns None
# ---------------------------------------------------------------------------


def test_pyramid_already_pyramided_returns_none():
    portfolio = _make_portfolio()
    pos = _open_position(portfolio, symbol="TCS")
    pos.pyramided = True  # mark as already pyramided

    result = _make_result(
        symbol="TCS",
        setup_quality="A",
        vcp_qualified=True,
        vcp_details={"vol_ratio": 0.3},
        entry_price=3_800.0,
    )

    ret = pyramid_position(result, portfolio, 3_810.0, date(2024, 3, 15))
    assert ret is None
    assert pos.pyramid_qty == 0


# ---------------------------------------------------------------------------
# Test 5 — pyramid_position: valid VCP Grade A + vol dry-up → pyramid_qty set
# ---------------------------------------------------------------------------


def test_pyramid_valid_a_grade_vol_dryup_sets_pyramid_qty():
    portfolio = _make_portfolio()
    pos = _open_position(portfolio, symbol="TCS", entry_price=3_800.0, quantity=10)
    original_cash = portfolio.cash

    result = _make_result(
        symbol="TCS",
        setup_quality="A",
        vcp_qualified=True,
        vcp_details={"vol_ratio": 0.25},   # < 0.4 → vol dry-up satisfied
        entry_price=3_800.0,               # pivot price
    )
    # current_price within 2 % above pivot (3_800 * 1.02 = 3_876)
    current_price = 3_830.0

    ret = pyramid_position(result, portfolio, current_price, date(2024, 3, 15))

    assert ret is not None, "Expected pyramid to succeed"
    assert pos.pyramided is True
    assert pos.pyramid_qty == 5   # max(1, int(10 * 0.5))
    assert portfolio.cash < original_cash  # cash reduced


# ---------------------------------------------------------------------------
# Test 6 — check_exits: price hits stop_loss → ClosedTrade exit_reason=="stop_loss"
# ---------------------------------------------------------------------------


def test_check_exits_stop_loss():
    portfolio = _make_portfolio()
    _open_position(
        portfolio,
        symbol="INFY",
        entry_price=1_500.0,
        stop_loss=1_400.0,
        target_price=1_800.0,
        quantity=5,
    )

    # Price falls to stop level
    closed = check_exits(portfolio, {"INFY": 1_390.0}, date(2024, 3, 20))

    assert len(closed) == 1
    trade = closed[0]
    assert isinstance(trade, ClosedTrade)
    assert trade.symbol == "INFY"
    assert trade.exit_reason == "stop_loss"
    assert trade.exit_price == pytest.approx(1_390.0)
    assert "INFY" not in portfolio.positions


# ---------------------------------------------------------------------------
# Test 7 — check_exits: price hits target → ClosedTrade exit_reason=="target"
# ---------------------------------------------------------------------------


def test_check_exits_target_hit():
    portfolio = _make_portfolio()
    _open_position(
        portfolio,
        symbol="RELIANCE",
        entry_price=2_800.0,
        stop_loss=2_600.0,
        target_price=3_200.0,
        quantity=3,
    )

    closed = check_exits(portfolio, {"RELIANCE": 3_250.0}, date(2024, 3, 20))

    assert len(closed) == 1
    trade = closed[0]
    assert trade.exit_reason == "target"
    assert trade.exit_price == pytest.approx(3_250.0)
    assert "RELIANCE" not in portfolio.positions
    assert trade.pnl > 0


# ---------------------------------------------------------------------------
# Test 8 — Portfolio.get_summary returns correct total_return_pct
# ---------------------------------------------------------------------------


def test_get_summary_total_return_pct():
    portfolio = _make_portfolio(cash=100_000.0)
    # Simulate a closed winning trade that added to cash
    portfolio.cash = 110_000.0   # +10 % return, no open positions

    summary = portfolio.get_summary({})

    assert summary["total_value"] == pytest.approx(110_000.0)
    assert summary["total_return_pct"] == pytest.approx(10.0, abs=0.01)
    assert summary["open_count"] == 0
    assert summary["closed_count"] == 0


# ---------------------------------------------------------------------------
# Test 9 — Portfolio.to_json / from_json round-trip
# ---------------------------------------------------------------------------


def test_portfolio_json_round_trip():
    portfolio = _make_portfolio(cash=80_000.0)
    _open_position(portfolio, symbol="HDFC", entry_price=1_600.0, quantity=4)

    # Simulate a closed trade
    from datetime import date as d
    portfolio.closed_trades.append(
        ClosedTrade(
            symbol="WIPRO",
            entry_date=d(2024, 1, 5),
            exit_date=d(2024, 2, 10),
            entry_price=400.0,
            exit_price=480.0,
            quantity=20,
            pnl=1_600.0,
            pnl_pct=20.0,
            exit_reason="target",
            r_multiple=2.67,
        )
    )

    data = portfolio.to_json()
    restored = Portfolio.from_json(data, _CONFIG)

    # Cash
    assert abs(restored.cash - portfolio.cash) < 0.01
    # Open positions
    assert set(restored.positions.keys()) == set(portfolio.positions.keys())
    orig_pos = portfolio.positions["HDFC"]
    rest_pos = restored.positions["HDFC"]
    assert rest_pos.entry_price == orig_pos.entry_price
    assert rest_pos.quantity == orig_pos.quantity
    assert rest_pos.entry_date == orig_pos.entry_date
    # Closed trades
    assert len(restored.closed_trades) == 1
    ct = restored.closed_trades[0]
    assert ct.symbol == "WIPRO"
    assert ct.pnl == pytest.approx(1_600.0)
    assert ct.exit_reason == "target"
    assert ct.entry_date == d(2024, 1, 5)


# ---------------------------------------------------------------------------
# Test 10 — Non-trading day → order queued, enter_trade returns None
# ---------------------------------------------------------------------------


@patch("paper_trading.simulator.queue_order")
@patch("paper_trading.simulator.is_trading_day", return_value=False)
def test_enter_trade_non_trading_day_queues_order(mock_td, mock_queue):
    portfolio = _make_portfolio()
    result = _make_result()
    run_date = date(2024, 1, 26)  # Republic Day — NSE holiday

    position = enter_trade(result, portfolio, 3_800.0, run_date)

    assert position is None
    mock_queue.assert_called_once()
    call_args = mock_queue.call_args
    assert call_args[0][0] == "TCS"   # symbol
    assert call_args[0][1] == "BUY"   # order_type
    assert "TCS" not in portfolio.positions
