"""
tests/unit/test_paper_report.py
--------------------------------
Unit tests for paper_trading/report.py.

Coverage
--------
1.  generate_performance_report with 5 closed trades + 2 open → HTML file created
2.  get_quality_breakdown: 3 A+ trades (2 wins, 1 loss) → win_rate≈0.667
3.  get_monthly_pnl: groups by exit_date month correctly
4.  Empty trades → report generates with "No closed trades yet" section
5.  Equity curve section is present in HTML (<img> tag with base64 PNG)
6.  get_quality_breakdown: multiple qualities bucketed independently
7.  get_monthly_pnl: empty trade list → empty dict
8.  Monthly P&L sums correctly when multiple trades share a month
"""

from __future__ import annotations

from datetime import date

import pytest

from paper_trading.portfolio import ClosedTrade, Portfolio, Position
from paper_trading.report import (
    generate_performance_report,
    get_monthly_pnl,
    get_quality_breakdown,
)

# ---------------------------------------------------------------------------
# Shared config / factories
# ---------------------------------------------------------------------------

_CONFIG = {
    "paper_trading": {
        "initial_capital": 100_000.0,
        "max_positions": 10,
        "risk_per_trade_pct": 2.0,
        "slippage_pct": 0.0,
        "min_score_to_trade": 70,
    }
}


def _make_portfolio(cash: float = 100_000.0) -> Portfolio:
    p = Portfolio(initial_capital=cash, config=_CONFIG)
    p.cash = cash
    return p


def _make_closed_trade(
    symbol: str = "TCS",
    pnl: float = 1_000.0,
    pnl_pct: float = 10.0,
    r_multiple: float = 2.0,
    exit_date: date = date(2024, 3, 15),
    entry_date: date = date(2024, 2, 1),
    setup_quality: str = "A+",
    exit_reason: str = "target",
) -> ClosedTrade:
    t = ClosedTrade(
        symbol=symbol,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_price=100.0,
        exit_price=110.0,
        quantity=10,
        pnl=pnl,
        pnl_pct=pnl_pct,
        exit_reason=exit_reason,
        r_multiple=r_multiple,
    )
    # setup_quality is not on the ClosedTrade dataclass — attach dynamically
    # (the report module uses getattr with fallback "Unknown")
    t.setup_quality = setup_quality  # type: ignore[attr-defined]
    return t


def _make_open_position(
    symbol: str = "INFY",
    entry_price: float = 1_500.0,
    stop_loss: float = 1_350.0,
    quantity: int = 5,
) -> Position:
    return Position(
        symbol=symbol,
        entry_date=date(2024, 3, 1),
        entry_price=entry_price,
        quantity=quantity,
        stop_loss=stop_loss,
        target_price=entry_price * 1.2,
        sepa_score=80,
        setup_quality="A",
    )


# ---------------------------------------------------------------------------
# Test 1 — generate_performance_report: 5 closed + 2 open → HTML file created
# ---------------------------------------------------------------------------

def test_generate_report_creates_html_file(tmp_path):
    """Report with 5 closed trades and 2 open positions must write a valid HTML file."""
    portfolio = _make_portfolio(cash=70_000.0)

    # 5 closed trades
    for i, sym in enumerate(("TCS", "INFY", "WIPRO", "HDFC", "RELIANCE")):
        trade = _make_closed_trade(
            symbol=sym,
            pnl=500.0 * (1 if i % 2 == 0 else -1),
            pnl_pct=5.0 * (1 if i % 2 == 0 else -1),
            exit_date=date(2024, 3, i + 10),
            setup_quality="A+" if i < 3 else "A",
        )
        portfolio.closed_trades.append(trade)

    # 2 open positions
    for sym, price, stop in (("BAJFINANCE", 7_000.0, 6_500.0), ("TITAN", 3_200.0, 3_000.0)):
        pos = _make_open_position(symbol=sym, entry_price=price, stop_loss=stop)
        portfolio.positions[sym] = pos
        portfolio.cash -= price * pos.quantity

    # Provide an equity curve so the chart renders
    portfolio.equity_curve = [
        {"date": f"2024-03-{d:02d}", "total_value": 100_000 + d * 100, "cash": 70_000.0}
        for d in range(1, 16)
    ]

    current_prices = {"BAJFINANCE": 7_200.0, "TITAN": 3_300.0}
    out_path = generate_performance_report(
        portfolio, current_prices, str(tmp_path), date(2024, 3, 15)
    )

    assert out_path == str(tmp_path / "paper_trading_2024-03-15.html")
    assert (tmp_path / "paper_trading_2024-03-15.html").exists()

    html = (tmp_path / "paper_trading_2024-03-15.html").read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "Paper Trading Report" in html
    assert "BAJFINANCE" in html
    assert "TITAN" in html


# ---------------------------------------------------------------------------
# Test 2 — get_quality_breakdown: 3 A+ trades (2 wins, 1 loss) → win_rate≈0.667
# ---------------------------------------------------------------------------

def test_quality_breakdown_aplus_two_wins_one_loss():
    trades = [
        _make_closed_trade(pnl=500.0,  r_multiple=2.0,  setup_quality="A+"),
        _make_closed_trade(pnl=300.0,  r_multiple=1.5,  setup_quality="A+"),
        _make_closed_trade(pnl=-200.0, r_multiple=-0.5, setup_quality="A+"),
    ]
    result = get_quality_breakdown(trades)

    assert "A+" in result
    bucket = result["A+"]
    assert bucket["trades"] == 3
    assert bucket["wins"]   == 2
    assert bucket["win_rate"] == pytest.approx(2 / 3, abs=0.001)
    avg_r_expected = (2.0 + 1.5 - 0.5) / 3
    assert bucket["avg_r"] == pytest.approx(avg_r_expected, abs=0.001)


# ---------------------------------------------------------------------------
# Test 3 — get_monthly_pnl groups by month correctly
# ---------------------------------------------------------------------------

def test_get_monthly_pnl_groups_by_exit_month():
    trades = [
        _make_closed_trade(pnl=1_000.0, exit_date=date(2024, 1, 10)),
        _make_closed_trade(pnl=2_000.0, exit_date=date(2024, 1, 25)),
        _make_closed_trade(pnl=-500.0,  exit_date=date(2024, 2, 5)),
        _make_closed_trade(pnl=3_000.0, exit_date=date(2024, 3, 1)),
    ]
    result = get_monthly_pnl(trades)

    assert set(result.keys()) == {"2024-01", "2024-02", "2024-03"}
    assert result["2024-01"] == pytest.approx(3_000.0)
    assert result["2024-02"] == pytest.approx(-500.0)
    assert result["2024-03"] == pytest.approx(3_000.0)


# ---------------------------------------------------------------------------
# Test 4 — Empty trades → report generates with "No closed trades yet" section
# ---------------------------------------------------------------------------

def test_generate_report_empty_trades_shows_no_closed_message(tmp_path):
    portfolio = _make_portfolio(cash=100_000.0)
    # No closed trades, no open positions

    out_path = generate_performance_report(
        portfolio, {}, str(tmp_path), date(2024, 4, 1)
    )

    html = (tmp_path / "paper_trading_2024-04-01.html").read_text(encoding="utf-8")
    assert "No closed trades yet" in html
    assert out_path.endswith("paper_trading_2024-04-01.html")


# ---------------------------------------------------------------------------
# Test 5 — Equity curve section is present in HTML (<img> tag with base64 PNG)
# ---------------------------------------------------------------------------

def test_generate_report_equity_curve_img_present(tmp_path):
    """When equity_curve is populated, the HTML must embed a base64 <img> tag."""
    portfolio = _make_portfolio(cash=100_000.0)
    portfolio.equity_curve = [
        {"date": f"2024-01-{d:02d}", "total_value": 100_000 + d * 50, "cash": 80_000.0}
        for d in range(1, 11)
    ]

    generate_performance_report(portfolio, {}, str(tmp_path), date(2024, 1, 10))
    html = (tmp_path / "paper_trading_2024-01-10.html").read_text(encoding="utf-8")

    # Must contain an embedded PNG image (base64 data URI)
    assert '<img src="data:image/png;base64,' in html


# ---------------------------------------------------------------------------
# Test 6 — get_quality_breakdown: multiple qualities bucketed independently
# ---------------------------------------------------------------------------

def test_quality_breakdown_multiple_buckets():
    trades = [
        _make_closed_trade(pnl=400.0,  setup_quality="A+"),
        _make_closed_trade(pnl=-100.0, setup_quality="A+"),
        _make_closed_trade(pnl=200.0,  setup_quality="A"),
        _make_closed_trade(pnl=100.0,  setup_quality="B"),
        _make_closed_trade(pnl=-50.0,  setup_quality="B"),
    ]
    result = get_quality_breakdown(trades)

    assert result["A+"]["trades"] == 2
    assert result["A+"]["wins"]   == 1
    assert result["A"]["trades"]  == 1
    assert result["A"]["wins"]    == 1
    assert result["B"]["trades"]  == 2
    assert result["B"]["wins"]    == 1
    assert result["B"]["win_rate"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Test 7 — get_monthly_pnl: empty list → empty dict
# ---------------------------------------------------------------------------

def test_get_monthly_pnl_empty_trades():
    result = get_monthly_pnl([])
    assert result == {}


# ---------------------------------------------------------------------------
# Test 8 — Monthly P&L sums correctly when multiple trades share a month
# ---------------------------------------------------------------------------

def test_get_monthly_pnl_sums_same_month():
    trades = [
        _make_closed_trade(pnl=1_000.0,  exit_date=date(2024, 6, 5)),
        _make_closed_trade(pnl=-300.0,   exit_date=date(2024, 6, 12)),
        _make_closed_trade(pnl=2_500.0,  exit_date=date(2024, 6, 28)),
    ]
    result = get_monthly_pnl(trades)

    assert list(result.keys()) == ["2024-06"]
    assert result["2024-06"] == pytest.approx(1_000.0 - 300.0 + 2_500.0)
