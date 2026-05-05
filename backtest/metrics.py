"""
backtest/metrics.py
-------------------
Performance metric computation for the SEPA AI backtesting engine.

All functions are pure (no side-effects) and accept plain Python types so
they can be called from notebooks, the dashboard, or unit tests without
standing up the full engine.

Public API
----------
compute_metrics(trades, equity_curve, initial_capital) → dict
compute_cagr(initial, final, years)                    → float
compute_max_drawdown(equity_values)                    → float
compute_sharpe(daily_returns, risk_free_daily)         → float
"""

from __future__ import annotations

import math
from datetime import date

from backtest.engine import BacktestTrade


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------


def compute_cagr(initial: float, final: float, years: float) -> float:
    """Compound Annual Growth Rate.

    Parameters
    ----------
    initial:
        Starting portfolio value.
    final:
        Ending portfolio value.
    years:
        Elapsed time in years (may be fractional).

    Returns
    -------
    float
        CAGR as a decimal (e.g. 0.225 for 22.5 %).  Returns 0.0 when
        *years* ≤ 0 or *initial* ≤ 0.
    """
    if years <= 0 or initial <= 0:
        return 0.0
    return (final / initial) ** (1.0 / years) - 1.0


def compute_max_drawdown(equity_values: list[float]) -> float:
    """Maximum peak-to-trough drawdown expressed as a positive percentage.

    Parameters
    ----------
    equity_values:
        Ordered sequence of portfolio values (e.g. from equity_curve).

    Returns
    -------
    float
        Maximum drawdown as a positive percentage (e.g. 25.0 for 25 %).
        Returns 0.0 for empty or single-element inputs.
    """
    if len(equity_values) < 2:
        return 0.0

    peak        = equity_values[0]
    max_dd      = 0.0

    for value in equity_values:
        if value > peak:
            peak = value
        if peak > 0:
            dd = (peak - value) / peak * 100.0
            if dd > max_dd:
                max_dd = dd

    return round(max_dd, 4)


def compute_sharpe(
    daily_returns: list[float],
    risk_free_daily: float = 0.06 / 252,
) -> float:
    """Annualised Sharpe ratio.

    Parameters
    ----------
    daily_returns:
        Sequence of daily portfolio returns as decimals
        (e.g. 0.01 for +1 %).
    risk_free_daily:
        Daily risk-free rate (default: 6 % annual / 252 trading days).

    Returns
    -------
    float
        Annualised Sharpe ratio.  Returns 0.0 when fewer than 2 data
        points are supplied or standard deviation is zero.
    """
    n = len(daily_returns)
    if n < 2:
        return 0.0

    excess   = [r - risk_free_daily for r in daily_returns]
    mean_exc = sum(excess) / n
    variance = sum((r - mean_exc) ** 2 for r in excess) / (n - 1)
    std_dev  = math.sqrt(variance)

    if std_dev == 0.0:
        return 0.0

    return round((mean_exc / std_dev) * math.sqrt(252), 4)


# ---------------------------------------------------------------------------
# Full metrics bundle
# ---------------------------------------------------------------------------


def compute_metrics(
    trades: list[BacktestTrade],
    equity_curve: list[dict],
    initial_capital: float,
) -> dict:
    """Compute the full suite of backtest performance metrics.

    Parameters
    ----------
    trades:
        All *closed* ``BacktestTrade`` objects.
    equity_curve:
        List of equity snapshots produced by
        ``BacktestPortfolio.record_equity()``.  Each dict must contain a
        ``"portfolio_value"`` key and optionally a ``"date"`` key.
    initial_capital:
        Starting portfolio value used for CAGR and return calculations.

    Returns
    -------
    dict
        Keys:

        ==================  ============================================
        cagr                Annualised return (decimal)
        total_return_pct    Total return over the full period (%)
        sharpe_ratio        Annualised Sharpe (risk-free = 6 % p.a.)
        max_drawdown_pct    Maximum peak-to-trough drawdown (%)
        win_rate            Fraction of profitable trades (0–1)
        avg_r_multiple      Mean R-multiple across all trades
        profit_factor       Gross profit / gross loss (∞ when no losers)
        expectancy          avg_win*win_rate − avg_loss*(1−win_rate)
        total_trades        Number of closed trades
        avg_hold_days       Average holding period in calendar days
        best_trade_pct      Largest single-trade gain (%)
        worst_trade_pct     Largest single-trade loss (%)
        ==================  ============================================
    """
    # ------------------------------------------------------------------
    # Zero-trade guard
    # ------------------------------------------------------------------
    if not trades:
        return {
            "cagr":             0.0,
            "total_return_pct": 0.0,
            "sharpe_ratio":     0.0,
            "max_drawdown_pct": 0.0,
            "win_rate":         0.0,
            "avg_r_multiple":   0.0,
            "profit_factor":    0.0,
            "expectancy":       0.0,
            "total_trades":     0,
            "avg_hold_days":    0.0,
            "best_trade_pct":   0.0,
            "worst_trade_pct":  0.0,
        }

    total_trades = len(trades)

    # ------------------------------------------------------------------
    # Win / loss split
    # ------------------------------------------------------------------
    winners = [t for t in trades if t.pnl > 0]
    losers  = [t for t in trades if t.pnl <= 0]

    win_rate = len(winners) / total_trades

    avg_win  = sum(t.pnl_pct for t in winners) / len(winners) if winners else 0.0
    avg_loss = sum(abs(t.pnl_pct) for t in losers) / len(losers) if losers else 0.0

    gross_profit = sum(t.pnl for t in winners)
    gross_loss   = abs(sum(t.pnl for t in losers))

    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    expectancy    = avg_win * win_rate - avg_loss * (1.0 - win_rate)

    avg_r_multiple = sum(t.r_multiple for t in trades) / total_trades

    # ------------------------------------------------------------------
    # Hold duration
    # ------------------------------------------------------------------
    def _hold_days(trade: BacktestTrade) -> float:
        try:
            return (trade.exit_date - trade.entry_date).days
        except Exception:
            return 0.0

    avg_hold_days = sum(_hold_days(t) for t in trades) / total_trades

    best_trade_pct  = max(t.pnl_pct for t in trades)
    worst_trade_pct = min(t.pnl_pct for t in trades)

    # ------------------------------------------------------------------
    # Equity-curve derived metrics
    # ------------------------------------------------------------------
    equity_values: list[float] = [
        float(snap["portfolio_value"]) for snap in equity_curve
    ] if equity_curve else []

    # Final portfolio value: last equity snapshot or fallback via trade PnL
    if equity_values:
        final_value = equity_values[-1]
    else:
        final_value = initial_capital + sum(t.pnl for t in trades)

    total_return_pct = (final_value / initial_capital - 1.0) * 100.0 if initial_capital > 0 else 0.0

    # CAGR — derive years from equity curve dates when available
    years = _derive_years(equity_curve, trades)
    cagr  = compute_cagr(initial_capital, final_value, years)

    max_drawdown_pct = compute_max_drawdown(equity_values) if equity_values else 0.0

    # Daily returns from equity curve
    sharpe_ratio = 0.0
    if len(equity_values) >= 2:
        daily_returns = [
            (equity_values[i] / equity_values[i - 1] - 1.0)
            for i in range(1, len(equity_values))
        ]
        sharpe_ratio = compute_sharpe(daily_returns)

    return {
        "cagr":             round(cagr, 6),
        "total_return_pct": round(total_return_pct, 4),
        "sharpe_ratio":     round(sharpe_ratio, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "win_rate":         round(win_rate, 4),
        "avg_r_multiple":   round(avg_r_multiple, 4),
        "profit_factor":    round(profit_factor, 4) if math.isfinite(profit_factor) else profit_factor,
        "expectancy":       round(expectancy, 4),
        "total_trades":     total_trades,
        "avg_hold_days":    round(avg_hold_days, 2),
        "best_trade_pct":   round(best_trade_pct, 4),
        "worst_trade_pct":  round(worst_trade_pct, 4),
    }


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _derive_years(equity_curve: list[dict], trades: list[BacktestTrade]) -> float:
    """Estimate elapsed years from equity curve dates or trade dates.

    Falls back to 1.0 when no date information is available.
    """
    # Try equity curve first
    if len(equity_curve) >= 2:
        start = equity_curve[0].get("date")
        end   = equity_curve[-1].get("date")
        if isinstance(start, date) and isinstance(end, date):
            days = (end - start).days
            return max(days / 365.25, 1 / 365.25)

    # Try trade dates
    if trades:
        all_dates: list[date] = []
        for t in trades:
            try:
                all_dates.append(t.entry_date)
                all_dates.append(t.exit_date)
            except Exception:
                pass
        if all_dates:
            days = (max(all_dates) - min(all_dates)).days
            return max(days / 365.25, 1 / 365.25)

    return 1.0
