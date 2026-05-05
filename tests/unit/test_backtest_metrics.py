"""
tests/unit/test_backtest_metrics.py
------------------------------------
Unit tests for backtest/metrics.py and backtest/portfolio.py.

Tests
-----
1.  compute_cagr: 100k → 150k over 2 years → CAGR ≈ 22.47 %
2.  compute_max_drawdown: [100, 120, 90, 110] → 25 % drawdown
3.  compute_sharpe: known returns → within 5 % of manually computed value
4.  compute_metrics with 10 trades (6 wins) → win_rate=0.6, profit_factor correct
5.  compute_metrics with 0 trades → all-zero dict, no division errors
6.  BacktestPortfolio.enter: position sizing follows the 1 % risk rule
7.  BacktestPortfolio capacity: refuses entry when positions at max
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from backtest.engine import BacktestTrade
from backtest.metrics import (
    compute_cagr,
    compute_max_drawdown,
    compute_metrics,
    compute_sharpe,
)
from backtest.portfolio import BacktestPortfolio

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_ENTRY_DATE = date(2023, 1, 2)
_EXIT_DATE  = date(2023, 3, 1)


def _make_trade(
    pnl: float,
    pnl_pct: float,
    r_multiple: float = 1.0,
    entry_date: date = _ENTRY_DATE,
    exit_date: date = _EXIT_DATE,
    symbol: str = "TEST",
    quantity: int = 10,
    entry_price: float = 100.0,
    exit_price: float | None = None,
    stop_loss_price: float = 90.0,
) -> BacktestTrade:
    """Build a minimal BacktestTrade for metric testing."""
    if exit_price is None:
        exit_price = entry_price * (1 + pnl_pct / 100)
    return BacktestTrade(
        symbol=symbol,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=entry_price,
        exit_price=exit_price,
        stop_loss_price=stop_loss_price,
        peak_price=exit_price,
        trailing_stop_used=stop_loss_price,
        stop_type="trailing",
        quantity=quantity,
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct, 4),
        r_multiple=round(r_multiple, 3),
        exit_reason="target" if pnl > 0 else "trailing_stop",
        regime="Bull",
        setup_quality="A+",
        sepa_score=85,
    )


def _make_sepa_result(
    symbol: str = "TEST",
    entry_price: float = 100.0,
    stop_loss: float = 90.0,
    setup_quality: str = "A+",
    score: int = 85,
) -> MagicMock:
    """Return a lightweight mock that looks like a SEPAResult."""
    r = MagicMock()
    r.symbol        = symbol
    r.entry_price   = entry_price
    r.stop_loss     = stop_loss
    r.setup_quality = setup_quality
    r.score         = score
    return r


_BASE_CONFIG = {
    "backtest": {
        "max_positions": 3,
    }
}


# ===========================================================================
# Test 1 — compute_cagr
# ===========================================================================

def test_compute_cagr_two_years():
    """100 000 → 150 000 over 2 years should give CAGR ≈ 22.47 %."""
    cagr = compute_cagr(100_000, 150_000, 2.0)
    expected = (150_000 / 100_000) ** (1 / 2) - 1  # ≈ 0.22474
    assert cagr == pytest.approx(expected, rel=1e-6), (
        f"Expected CAGR ≈ {expected:.4%}, got {cagr:.4%}"
    )
    assert 0.22 < cagr < 0.23, "CAGR should be approximately 22–23 %"


def test_compute_cagr_zero_years_returns_zero():
    assert compute_cagr(100_000, 150_000, 0) == 0.0


def test_compute_cagr_zero_initial_returns_zero():
    assert compute_cagr(0, 150_000, 2.0) == 0.0


# ===========================================================================
# Test 2 — compute_max_drawdown
# ===========================================================================

def test_compute_max_drawdown_classic():
    """[100, 120, 90, 110] → peak=120, trough=90 → drawdown=25 %."""
    dd = compute_max_drawdown([100, 120, 90, 110])
    assert dd == pytest.approx(25.0, rel=1e-4), f"Expected 25.0, got {dd}"


def test_compute_max_drawdown_no_drawdown():
    """Monotonically rising equity → drawdown = 0."""
    assert compute_max_drawdown([100, 110, 120, 130]) == 0.0


def test_compute_max_drawdown_single_element():
    """Single value → 0 (no drawdown possible)."""
    assert compute_max_drawdown([100.0]) == 0.0


def test_compute_max_drawdown_empty():
    assert compute_max_drawdown([]) == 0.0


# ===========================================================================
# Test 3 — compute_sharpe
# ===========================================================================

def test_compute_sharpe_known_returns():
    """Manually verify Sharpe within 5 % of hand-computed value."""
    # Constant daily return of 0.001 (0.1 %) with no variance → Sharpe = ∞
    # Instead use a realistic set of returns.
    returns = [0.01, -0.005, 0.008, 0.003, -0.002, 0.006, 0.004, -0.001]
    rf_daily = 0.06 / 252

    n         = len(returns)
    excess    = [r - rf_daily for r in returns]
    mean_exc  = sum(excess) / n
    var       = sum((r - mean_exc) ** 2 for r in excess) / (n - 1)
    std_dev   = math.sqrt(var)
    expected_sharpe = (mean_exc / std_dev) * math.sqrt(252)

    computed = compute_sharpe(returns, rf_daily)

    assert computed == pytest.approx(expected_sharpe, rel=0.05), (
        f"Expected Sharpe ≈ {expected_sharpe:.4f}, got {computed:.4f}"
    )


def test_compute_sharpe_constant_returns_zero_std():
    """Identical returns equal to the risk-free rate → excess = 0 → Sharpe = 0.

    We set each daily return exactly equal to the risk-free rate so that all
    excess returns are 0.0, giving a sample std of 0 and Sharpe of 0.

    Note: using arbitrary returns with fp arithmetic can produce a tiny but
    non-zero sample std due to IEEE-754 rounding in sum/n, so we use the
    risk-free rate itself to guarantee zero excess.
    """
    rf_daily = 0.06 / 252
    returns  = [rf_daily] * 10   # excess = 0 for every observation
    sharpe   = compute_sharpe(returns, risk_free_daily=rf_daily)
    assert sharpe == 0.0


def test_compute_sharpe_single_return():
    """Fewer than 2 data points → 0.0."""
    assert compute_sharpe([0.01]) == 0.0


# ===========================================================================
# Test 4 — compute_metrics with 10 trades (6 wins)
# ===========================================================================

def test_compute_metrics_ten_trades_win_rate_and_profit_factor():
    """6 winning / 4 losing trades → win_rate=0.6, profit_factor verified."""
    # Winners: each earns £200 PnL, +10 % PnL_pct, R=1.0
    winners = [
        _make_trade(pnl=200.0, pnl_pct=10.0, r_multiple=1.0, symbol=f"W{i}")
        for i in range(6)
    ]
    # Losers: each loses £100 PnL, -5 % PnL_pct, R=-0.5
    losers = [
        _make_trade(pnl=-100.0, pnl_pct=-5.0, r_multiple=-0.5, symbol=f"L{i}")
        for i in range(4)
    ]
    trades = winners + losers

    # Build a simple equity curve: 100 000 → 101 200 (6*200 - 4*100 net)
    equity_curve = [
        {"date": date(2023, 1, 2), "portfolio_value": 100_000.0},
        {"date": date(2023, 6, 1), "portfolio_value": 100_800.0},
    ]

    m = compute_metrics(trades, equity_curve, initial_capital=100_000.0)

    assert m["total_trades"] == 10
    assert m["win_rate"]     == pytest.approx(0.6, rel=1e-4)

    # profit_factor = gross_profit / gross_loss = (6*200) / (4*100) = 1200/400 = 3.0
    assert m["profit_factor"] == pytest.approx(3.0, rel=1e-4)

    # win_rate, avg_r, expectancy sanity
    assert m["avg_r_multiple"] == pytest.approx(
        (6 * 1.0 + 4 * -0.5) / 10, rel=1e-4
    )
    assert m["best_trade_pct"]  == pytest.approx(10.0, rel=1e-4)
    assert m["worst_trade_pct"] == pytest.approx(-5.0, rel=1e-4)


# ===========================================================================
# Test 5 — compute_metrics with 0 trades → zeros, no exceptions
# ===========================================================================

def test_compute_metrics_zero_trades():
    """Empty trade list should return a well-formed all-zero dict."""
    m = compute_metrics(trades=[], equity_curve=[], initial_capital=100_000.0)

    assert m["total_trades"]     == 0
    assert m["win_rate"]         == 0.0
    assert m["profit_factor"]    == 0.0
    assert m["cagr"]             == 0.0
    assert m["total_return_pct"] == 0.0
    assert m["sharpe_ratio"]     == 0.0
    assert m["max_drawdown_pct"] == 0.0
    assert m["avg_hold_days"]    == 0.0
    assert m["best_trade_pct"]   == 0.0
    assert m["worst_trade_pct"]  == 0.0


# ===========================================================================
# Test 6 — BacktestPortfolio.enter: 1 % risk-based position sizing
# ===========================================================================

def test_portfolio_enter_position_sizing():
    """Entry quantity must follow the 1 % risk-per-trade rule exactly."""
    portfolio = BacktestPortfolio(initial_capital=100_000.0, config=_BASE_CONFIG)

    entry_price  = 100.0
    stop_loss    = 90.0          # risk_per_share = 10
    # portfolio_value ≈ 100 000, risk_per_trade = 1 000
    # quantity = int(1000 / 10) = 100

    result = _make_sepa_result(
        symbol="AAPL",
        entry_price=entry_price,
        stop_loss=stop_loss,
    )

    entered = portfolio.enter(result, entry_price=entry_price, entry_date=_ENTRY_DATE)
    assert entered, "Expected enter() to return True"
    assert "AAPL" in portfolio.positions

    trade    = portfolio.positions["AAPL"]
    expected_qty = max(1, int(100_000 * 0.01 / (entry_price - stop_loss)))
    assert trade.quantity == expected_qty, (
        f"Expected quantity={expected_qty}, got {trade.quantity}"
    )

    expected_cost = expected_qty * entry_price
    assert portfolio.capital == pytest.approx(100_000.0 - expected_cost, rel=1e-6)


def test_portfolio_enter_bad_stop_rejected():
    """Entry should fail when stop_loss >= entry_price."""
    portfolio = BacktestPortfolio(initial_capital=100_000.0, config=_BASE_CONFIG)
    result    = _make_sepa_result(entry_price=100.0, stop_loss=100.0)
    entered   = portfolio.enter(result, entry_price=100.0, entry_date=_ENTRY_DATE)
    assert not entered
    assert len(portfolio.positions) == 0


# ===========================================================================
# Test 7 — BacktestPortfolio capacity: refuses when at max_positions
# ===========================================================================

def test_portfolio_capacity_refuses_at_max():
    """Portfolio with max_positions=3 should refuse the 4th entry."""
    config    = {"backtest": {"max_positions": 3}}
    portfolio = BacktestPortfolio(initial_capital=1_000_000.0, config=config)

    for i in range(3):
        result  = _make_sepa_result(
            symbol=f"SYM{i}",
            entry_price=100.0,
            stop_loss=90.0,
        )
        entered = portfolio.enter(result, entry_price=100.0, entry_date=_ENTRY_DATE)
        assert entered, f"Expected entry {i} to succeed"

    assert len(portfolio.positions) == 3

    # 4th entry must be refused
    overflow = _make_sepa_result(symbol="SYM_OVERFLOW", entry_price=100.0, stop_loss=90.0)
    entered  = portfolio.enter(overflow, entry_price=100.0, entry_date=_ENTRY_DATE)
    assert not entered, "4th entry should have been refused (max_positions=3)"
    assert len(portfolio.positions) == 3, "Position count must not exceed max_positions"


# ===========================================================================
# Test 8 — BacktestPortfolio.close round-trip
# ===========================================================================

def test_portfolio_close_updates_capital_and_pnl():
    """Close should realise PnL and restore capital correctly."""
    portfolio   = BacktestPortfolio(initial_capital=100_000.0, config=_BASE_CONFIG)
    entry_price = 100.0
    stop_loss   = 90.0
    result      = _make_sepa_result(entry_price=entry_price, stop_loss=stop_loss)

    portfolio.enter(result, entry_price=entry_price, entry_date=_ENTRY_DATE)
    capital_after_entry = portfolio.capital
    trade = portfolio.positions["TEST"]
    qty   = trade.quantity

    exit_price = 115.0
    closed     = portfolio.close("TEST", exit_price, _EXIT_DATE, "target")

    assert "TEST" not in portfolio.positions
    assert len(portfolio.closed_trades) == 1
    assert portfolio.capital == pytest.approx(capital_after_entry + qty * exit_price, rel=1e-6)
    assert closed.pnl        == pytest.approx((exit_price - entry_price) * qty, rel=1e-4)
    assert closed.exit_reason == "target"
