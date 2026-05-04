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
    assert trade.exit_reason == "trailing_stop"
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


# ---------------------------------------------------------------------------
# Test 11 — check_exits: brokerage is deducted from pnl (and from cash)
# ---------------------------------------------------------------------------

_CONFIG_BROK = {
    "paper_trading": {
        "initial_capital": 100_000.0,
        "max_positions": 10,
        "risk_per_trade_pct": 2.0,
        "slippage_pct": 0.15,
        "brokerage_pct": 0.05,   # 0.05 % expressed in percent  → fraction 0.0005
        "min_score_to_trade": 70,
        "max_hold_days": 20,
    }
}


def test_check_exits_brokerage_deducted_from_pnl():
    """Net pnl = gross_pnl − (exit_price × qty × brokerage_fraction)."""
    portfolio = Portfolio(initial_capital=100_000.0, config=_CONFIG_BROK)

    from paper_trading.portfolio import Position as _Pos
    pos = _Pos(
        symbol="BROK",
        entry_date=date(2024, 3, 1),
        entry_price=1_000.0,
        quantity=10,
        stop_loss=800.0,
        target_price=1_500.0,
        sepa_score=80,
        setup_quality="A",
        peak_close=1_200.0,    # trailing stop = 1_200 × 0.93 = 1_116
        trailing_stop=1_116.0,
        days_held=3,
    )
    portfolio.positions["BROK"] = pos
    portfolio.cash = 90_000.0

    exit_price = 1_100.0   # below trailing stop 1_116 → "trailing_stop"
    closed = check_exits(portfolio, {"BROK": exit_price}, date(2024, 3, 20))

    assert len(closed) == 1
    trade = closed[0]
    assert trade.exit_reason == "trailing_stop"

    gross_pnl = (exit_price - 1_000.0) * 10          # = 1_000.0
    brok_frac = 0.05 / 100.0                          # = 0.0005
    expected_brok = exit_price * 10 * brok_frac       # = 5.5
    expected_net_pnl = gross_pnl - expected_brok      # = 994.5

    assert trade.pnl == pytest.approx(expected_net_pnl, abs=0.01)
    # Cash should also reflect the brokerage deduction
    assert portfolio.cash == pytest.approx(90_000.0 + exit_price * 10 - expected_brok, abs=0.01)


# ---------------------------------------------------------------------------
# Test 12 — check_exits: days_held > max_hold_days triggers "max_hold_days" exit
# ---------------------------------------------------------------------------

_CONFIG_MAXHOLD = {
    "paper_trading": {
        "initial_capital": 100_000.0,
        "max_positions": 10,
        "risk_per_trade_pct": 2.0,
        "slippage_pct": 0.15,
        "brokerage_pct": 0.0,
        "min_score_to_trade": 70,
        "max_hold_days": 20,
    }
}


def test_check_exits_max_hold_days_exit():
    """After days_held exceeds max_hold_days the position is closed with reason='max_hold_days'."""
    from paper_trading.portfolio import Position as _Pos

    portfolio = Portfolio(initial_capital=100_000.0, config=_CONFIG_MAXHOLD)
    pos = _Pos(
        symbol="HELD",
        entry_date=date(2024, 1, 1),
        entry_price=500.0,
        quantity=5,
        stop_loss=400.0,
        target_price=700.0,
        sepa_score=75,
        setup_quality="B",
        peak_close=600.0,
        trailing_stop=558.0,   # 600 × 0.93 — below current price
        days_held=20,          # at limit; one more increment → 21 > 20
    )
    portfolio.positions["HELD"] = pos
    portfolio.cash = 97_500.0

    current_price = 620.0  # above trailing stop (which will ratchet to ~576) & below target
    closed = check_exits(portfolio, {"HELD": current_price}, date(2024, 3, 21))

    assert len(closed) == 1
    trade = closed[0]
    assert trade.exit_reason == "max_hold_days", (
        f"Expected 'max_hold_days' but got '{trade.exit_reason}'"
    )
    assert trade.symbol == "HELD"
    assert "HELD" not in portfolio.positions


# ---------------------------------------------------------------------------
# Test 13 — save_state / load_state: full round-trip preserves all data
# ---------------------------------------------------------------------------


def test_save_load_state_round_trip(tmp_path, monkeypatch):
    """save_state followed by load_state must reproduce the exact portfolio."""
    import paper_trading.simulator as sim_mod

    monkeypatch.setattr(sim_mod, "_PORTFOLIO_FILE", tmp_path / "portfolio.json")
    monkeypatch.setattr(sim_mod, "_TRADES_FILE", tmp_path / "trades.json")
    monkeypatch.setattr(sim_mod, "_PT_DIR", tmp_path)

    from paper_trading.portfolio import Position as _Pos
    from paper_trading.simulator import load_state, save_state

    portfolio = Portfolio(initial_capital=100_000.0, config=_CONFIG)

    pos = _Pos(
        symbol="SAVE",
        entry_date=date(2024, 3, 1),
        entry_price=200.0,
        quantity=50,
        stop_loss=180.0,
        target_price=260.0,
        sepa_score=78,
        setup_quality="A",
        peak_close=220.0,
        trailing_stop=204.6,
        days_held=5,
    )
    portfolio.positions["SAVE"] = pos
    portfolio.cash = 90_000.0

    portfolio.closed_trades.append(
        ClosedTrade(
            symbol="DONE",
            entry_date=date(2024, 1, 5),
            exit_date=date(2024, 2, 10),
            entry_price=300.0,
            exit_price=360.0,
            quantity=10,
            pnl=600.0,
            pnl_pct=20.0,
            exit_reason="target",
            r_multiple=2.0,
        )
    )

    save_state(portfolio)
    restored = load_state(_CONFIG)

    assert abs(restored.cash - portfolio.cash) < 0.01
    assert set(restored.positions.keys()) == {"SAVE"}

    rpos = restored.positions["SAVE"]
    assert rpos.entry_price == pytest.approx(200.0)
    assert rpos.quantity == 50
    assert rpos.entry_date == date(2024, 3, 1)
    assert rpos.peak_close == pytest.approx(220.0)
    assert rpos.trailing_stop == pytest.approx(204.6)
    assert rpos.days_held == 5

    assert len(restored.closed_trades) == 1
    ct = restored.closed_trades[0]
    assert ct.symbol == "DONE"
    assert ct.pnl == pytest.approx(600.0)
    assert ct.exit_reason == "target"
    assert ct.entry_date == date(2024, 1, 5)
    assert ct.exit_date == date(2024, 2, 10)

    # Both JSON files must actually exist on disk
    assert (tmp_path / "portfolio.json").exists()
    assert (tmp_path / "trades.json").exists()


# ---------------------------------------------------------------------------
# Test 14 — load_state with missing file: returns fresh portfolio, no exception
# ---------------------------------------------------------------------------


def test_load_state_missing_file_returns_fresh_portfolio(tmp_path, monkeypatch):
    """load_state must not raise when portfolio.json is absent."""
    import paper_trading.simulator as sim_mod
    from paper_trading.simulator import load_state

    monkeypatch.setattr(sim_mod, "_PORTFOLIO_FILE", tmp_path / "nonexistent.json")

    result = load_state(_CONFIG)

    assert result is not None
    assert result.cash == pytest.approx(100_000.0)
    assert len(result.positions) == 0
    assert len(result.closed_trades) == 0


# ---------------------------------------------------------------------------
# Test 15 — record_equity_point appends a daily snapshot
# ---------------------------------------------------------------------------


def test_record_equity_point_appends_snapshot():
    """record_equity_point should add one entry per call to equity_curve."""
    portfolio = _make_portfolio(cash=100_000.0)
    _open_position(portfolio, symbol="TCS", entry_price=3_800.0, quantity=5)
    # After _open_position: cash = 100_000 - 3_800*5 = 81_000
    run_date = date(2024, 3, 15)
    current_prices = {"TCS": 4_000.0}

    portfolio.record_equity_point(current_prices, run_date)

    assert len(portfolio.equity_curve) == 1
    snap = portfolio.equity_curve[0]
    assert snap["date"] == "2024-03-15"
    # total_value = cash (81_000) + 4_000 * 5 (20_000) = 101_000
    assert snap["total_value"] == pytest.approx(101_000.0, abs=0.01)
    assert snap["cash"] == pytest.approx(portfolio.cash, abs=0.01)

    # A second call on the next day appends another entry
    portfolio.record_equity_point({"TCS": 4_100.0}, date(2024, 3, 18))
    assert len(portfolio.equity_curve) == 2
    assert portfolio.equity_curve[1]["date"] == "2024-03-18"
    assert portfolio.equity_curve[1]["total_value"] == pytest.approx(
        portfolio.cash + 4_100.0 * 5, abs=0.01
    )


# ---------------------------------------------------------------------------
# Test 16 — get_summary.win_rate = 0.67 for 2 wins + 1 loss
# ---------------------------------------------------------------------------


def _make_closed_trade(
    pnl: float,
    pnl_pct: float,
    r_multiple: float = 1.0,
    entry_date: date = date(2024, 1, 1),
    exit_date: date = date(2024, 2, 1),
) -> ClosedTrade:
    return ClosedTrade(
        symbol="X",
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=100.0,
        exit_price=110.0,
        quantity=1,
        pnl=pnl,
        pnl_pct=pnl_pct,
        exit_reason="target",
        r_multiple=r_multiple,
    )


def test_get_summary_win_rate_two_wins_one_loss():
    """win_rate must be a fraction (0-1): 2 wins out of 3 → ≈0.6667."""
    portfolio = _make_portfolio(cash=100_000.0)
    portfolio.closed_trades.extend([
        _make_closed_trade(pnl=500.0,  pnl_pct=10.0),
        _make_closed_trade(pnl=300.0,  pnl_pct=6.0),
        _make_closed_trade(pnl=-200.0, pnl_pct=-4.0),
    ])

    summary = portfolio.get_summary({})

    assert summary["win_rate"] == pytest.approx(2 / 3, abs=0.001)


# ---------------------------------------------------------------------------
# Test 17 — get_summary.profit_factor = sum_wins / abs_sum_losses
# ---------------------------------------------------------------------------


def test_get_summary_profit_factor_correct_ratio():
    """profit_factor = total winning PnL / abs(total losing PnL)."""
    portfolio = _make_portfolio(cash=100_000.0)
    # wins: 500 + 300 = 800 ; losses: 200
    portfolio.closed_trades.extend([
        _make_closed_trade(pnl=500.0,  pnl_pct=10.0),
        _make_closed_trade(pnl=300.0,  pnl_pct=6.0),
        _make_closed_trade(pnl=-200.0, pnl_pct=-4.0),
    ])

    summary = portfolio.get_summary({})

    assert summary["profit_factor"] == pytest.approx(800.0 / 200.0, abs=0.001)


# ---------------------------------------------------------------------------
# Test 18 — get_summary.avg_r_multiple from 3 closed trades
# ---------------------------------------------------------------------------


def test_get_summary_avg_r_multiple_three_trades():
    """avg_r_multiple must be the arithmetic mean of all r_multiples."""
    portfolio = _make_portfolio(cash=100_000.0)
    r_values = [2.0, 1.5, -0.5]
    portfolio.closed_trades.extend([
        _make_closed_trade(pnl=200.0,  pnl_pct=20.0, r_multiple=r)
        for r in r_values
    ])

    summary = portfolio.get_summary({})

    expected = sum(r_values) / len(r_values)   # (2.0 + 1.5 - 0.5) / 3 = 1.0
    assert summary["avg_r_multiple"] == pytest.approx(expected, abs=0.001)


# ---------------------------------------------------------------------------
# Test 19 — get_summary with 0 closed trades → no ZeroDivisionError
# ---------------------------------------------------------------------------


def test_get_summary_zero_closed_trades_no_division_error():
    """win_rate, profit_factor, and avg_r_multiple must all default to 0 safely."""
    portfolio = _make_portfolio(cash=100_000.0)

    summary = portfolio.get_summary({})

    assert summary["win_rate"] == 0.0
    assert summary["profit_factor"] == 0.0
    assert summary["avg_r_multiple"] == 0.0
    assert summary["best_trade_pct"] == 0.0
    assert summary["worst_trade_pct"] == 0.0
    assert summary["avg_hold_days"] == 0.0


# ---------------------------------------------------------------------------
# Test 20 — to_json includes equity_curve; from_json restores it
# ---------------------------------------------------------------------------


def test_to_json_includes_equity_curve_and_from_json_restores():
    """equity_curve must survive a full to_json → from_json round-trip."""
    portfolio = _make_portfolio(cash=100_000.0)
    portfolio.equity_curve = [
        {"date": "2024-03-15", "total_value": 102_000.0, "cash": 82_000.0},
        {"date": "2024-03-18", "total_value": 103_500.0, "cash": 82_000.0},
    ]

    data = portfolio.to_json()

    # Serialised dict must contain the key
    assert "equity_curve" in data
    assert len(data["equity_curve"]) == 2

    # Restore and verify
    restored = Portfolio.from_json(data, _CONFIG)
    assert len(restored.equity_curve) == 2
    assert restored.equity_curve[0]["date"] == "2024-03-15"
    assert restored.equity_curve[0]["total_value"] == pytest.approx(102_000.0)
    assert restored.equity_curve[1]["date"] == "2024-03-18"
    assert restored.equity_curve[1]["total_value"] == pytest.approx(103_500.0)


# ===========================================================================
# Tests 21-27 — order_queue: is_market_open, queue_order, execute_pending_orders
# ===========================================================================

import json as _json
from datetime import datetime as _datetime

try:
    from zoneinfo import ZoneInfo as _ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo as _ZoneInfo  # type: ignore

_IST = _ZoneInfo("Asia/Kolkata")


# ---------------------------------------------------------------------------
# Test 21 — is_market_open: 10:00 IST on a trading day → True
# ---------------------------------------------------------------------------


@patch("paper_trading.order_queue.is_trading_day", return_value=True)
def test_is_market_open_during_hours_returns_true(mock_td):
    from paper_trading.order_queue import is_market_open

    dt = _datetime(2024, 3, 15, 10, 0, tzinfo=_IST)   # 10:00 IST, trading day
    assert is_market_open(dt) is True


# ---------------------------------------------------------------------------
# Test 22 — is_market_open: 16:00 IST (after close) → False
# ---------------------------------------------------------------------------


@patch("paper_trading.order_queue.is_trading_day", return_value=True)
def test_is_market_open_after_close_returns_false(mock_td):
    from paper_trading.order_queue import is_market_open

    dt = _datetime(2024, 3, 15, 16, 0, tzinfo=_IST)   # 16:00 IST — after 15:30
    assert is_market_open(dt) is False


# ---------------------------------------------------------------------------
# Test 23 — is_market_open: NSE holiday → False even within market hours
# ---------------------------------------------------------------------------


@patch("paper_trading.order_queue.is_trading_day", return_value=False)
def test_is_market_open_on_holiday_returns_false(mock_td):
    from paper_trading.order_queue import is_market_open

    dt = _datetime(2024, 1, 26, 10, 0, tzinfo=_IST)   # Republic Day
    assert is_market_open(dt) is False
    mock_td.assert_called_once_with(date(2024, 1, 26))


# ---------------------------------------------------------------------------
# Test 24 — queue_order: written order has queued_at + expiry_date fields
# ---------------------------------------------------------------------------


def test_queue_order_writes_expiry_fields(tmp_path, monkeypatch):
    """queue_order must persist queued_at and expiry_date to ORDERS_FILE."""
    import paper_trading.order_queue as oq

    monkeypatch.setattr(oq, "ORDERS_FILE", str(tmp_path / "orders.json"))

    fixed_today  = date(2024, 3, 15)
    fixed_expiry = date(2024, 3, 20)

    # Patch _add_trading_days so the test is calendar-independent
    monkeypatch.setattr(oq, "_add_trading_days", lambda _start, _n: fixed_expiry)
    # Patch datetime.now(IST) → known date
    with patch("paper_trading.order_queue.datetime") as mock_dt_cls:
        mock_now = MagicMock()
        mock_now.date.return_value = fixed_today
        mock_dt_cls.now.return_value = mock_now

        oq.queue_order("INFY", "BUY", {"score": 80, "stop_loss": 1_400.0}, expiry_days=3)

    orders = _json.loads((tmp_path / "orders.json").read_text())
    assert len(orders) == 1
    order = orders[0]
    assert order["symbol"]      == "INFY"
    assert order["order_type"]  == "BUY"
    assert order["queued_at"]   == "2024-03-15"
    assert order["expiry_date"] == "2024-03-20"


# ---------------------------------------------------------------------------
# Test 25 — execute_pending_orders: valid non-expired order → Position filled
# ---------------------------------------------------------------------------


def test_execute_pending_orders_valid_buy_creates_position(tmp_path, monkeypatch):
    """A BUY with expiry in the future and a known price must fill immediately."""
    import paper_trading.order_queue as oq

    monkeypatch.setattr(oq, "ORDERS_FILE", str(tmp_path / "orders.json"))

    run_date = date(2024, 3, 15)
    (tmp_path / "orders.json").write_text(_json.dumps([{
        "symbol":      "RELIANCE",
        "order_type":  "BUY",
        "result":      {"score": 80, "stop_loss": 2_400.0, "target_price": 3_200.0, "setup_quality": "A"},
        "queued_at":   "2024-03-12",
        "expiry_date": "2024-03-20",   # not yet expired
    }]))

    portfolio     = _make_portfolio(100_000.0)
    current_prices = {"RELIANCE": 2_800.0}

    filled = oq.execute_pending_orders(portfolio, current_prices, run_date)

    assert len(filled) == 1
    assert isinstance(filled[0], Position)
    assert filled[0].symbol == "RELIANCE"
    assert "RELIANCE" in portfolio.positions
    assert portfolio.cash < 100_000.0          # cash was deducted

    # Queue must now be empty
    remaining = _json.loads((tmp_path / "orders.json").read_text())
    assert remaining == []


# ---------------------------------------------------------------------------
# Test 26 — execute_pending_orders: expired order is skipped and removed
# ---------------------------------------------------------------------------


def test_execute_pending_orders_expired_order_removed(tmp_path, monkeypatch):
    """An order whose expiry_date < run_date must be logged and dropped from queue."""
    import paper_trading.order_queue as oq

    monkeypatch.setattr(oq, "ORDERS_FILE", str(tmp_path / "orders.json"))

    run_date = date(2024, 3, 15)
    (tmp_path / "orders.json").write_text(_json.dumps([{
        "symbol":      "TCS",
        "order_type":  "BUY",
        "result":      {"score": 85, "stop_loss": 3_500.0},
        "queued_at":   "2024-03-10",   # 5 calendar days ago
        "expiry_date": "2024-03-14",   # yesterday — expired
    }]))

    portfolio = _make_portfolio(100_000.0)

    filled = oq.execute_pending_orders(portfolio, {"TCS": 3_800.0}, run_date)

    assert filled == []
    assert "TCS" not in portfolio.positions
    assert portfolio.cash == pytest.approx(100_000.0)   # unchanged

    # Expired order must have been removed — queue is now empty
    remaining = _json.loads((tmp_path / "orders.json").read_text())
    assert remaining == []


# ---------------------------------------------------------------------------
# Test 27 — execute_pending_orders: symbol missing from prices → stays in queue
# ---------------------------------------------------------------------------


def test_execute_pending_orders_missing_price_keeps_in_queue(tmp_path, monkeypatch):
    """An order for a symbol not in current_prices must remain in the queue."""
    import paper_trading.order_queue as oq

    monkeypatch.setattr(oq, "ORDERS_FILE", str(tmp_path / "orders.json"))

    run_date = date(2024, 3, 15)
    original_order = {
        "symbol":      "WIPRO",
        "order_type":  "BUY",
        "result":      {"score": 75, "stop_loss": 420.0},
        "queued_at":   "2024-03-12",
        "expiry_date": "2024-03-20",   # still valid
    }
    (tmp_path / "orders.json").write_text(_json.dumps([original_order]))

    portfolio = _make_portfolio(100_000.0)

    # current_prices is empty → WIPRO has no price
    filled = oq.execute_pending_orders(portfolio, {}, run_date)

    assert filled == []
    assert "WIPRO" not in portfolio.positions

    # Order must still be in queue, unchanged
    remaining = _json.loads((tmp_path / "orders.json").read_text())
    assert len(remaining) == 1
    assert remaining[0]["symbol"] == "WIPRO"
    assert remaining[0]["expiry_date"] == "2024-03-20"
