"""
backtest/portfolio.py
---------------------
Portfolio state manager for the walk-forward backtesting engine.

Handles position sizing (1% risk-per-trade rule), capital tracking,
open/close lifecycle, and equity-curve snapshots.

Public API
----------
BacktestPortfolio   — stateful portfolio object used by the backtest runner.
"""

from __future__ import annotations

from datetime import date

from backtest.engine import BacktestTrade
from rules.scorer import SEPAResult
from utils.logger import get_logger

log = get_logger(__name__)


class BacktestPortfolio:
    """Stateful portfolio used during a walk-forward backtest.

    Position sizing follows the 1R = 1% rule:
        risk_per_trade  = portfolio_value * 0.01
        risk_per_share  = entry_price - stop_loss
        quantity        = max(1, int(risk_per_trade / risk_per_share))

    Parameters
    ----------
    initial_capital:
        Starting cash in the portfolio.
    config:
        Project config dict.  Reads ``config["backtest"]["max_positions"]``
        (default 10).
    """

    def __init__(self, initial_capital: float, config: dict) -> None:
        self.capital: float = initial_capital
        self.positions: dict[str, BacktestTrade] = {}   # symbol → open BacktestTrade
        self.closed_trades: list[BacktestTrade] = []
        self.equity_curve: list[dict] = []
        self.max_positions: int = config.get("backtest", {}).get("max_positions", 10)
        self._initial_capital: float = initial_capital

    # ------------------------------------------------------------------
    # Capacity check
    # ------------------------------------------------------------------

    def can_enter(self) -> bool:
        """Return True when there is room for a new position and capital > 0."""
        return len(self.positions) < self.max_positions and self.capital > 0

    # ------------------------------------------------------------------
    # Enter
    # ------------------------------------------------------------------

    def enter(
        self,
        result: SEPAResult,
        entry_price: float,
        entry_date: date,
    ) -> bool:
        """Open a new position sized by the 1% risk rule.

        Parameters
        ----------
        result:
            SEPAResult for the symbol being entered.  ``result.stop_loss``
            must be set and strictly less than *entry_price*.
        entry_price:
            Execution price (typically today's close).
        entry_date:
            Date of entry.

        Returns
        -------
        bool
            ``True`` if the position was opened, ``False`` otherwise
            (capacity full, insufficient capital, or bad stop).
        """
        if not self.can_enter():
            log.debug(
                "portfolio.enter: SKIP %s — capacity full (%d/%d) or no capital",
                result.symbol, len(self.positions), self.max_positions,
            )
            return False

        stop_loss = result.stop_loss
        if stop_loss is None or stop_loss >= entry_price:
            log.debug(
                "portfolio.enter: SKIP %s — invalid stop_loss (%.2f) vs entry (%.2f)",
                result.symbol, stop_loss if stop_loss is not None else float("nan"), entry_price,
            )
            return False

        if result.symbol in self.positions:
            log.debug("portfolio.enter: SKIP %s — already open", result.symbol)
            return False

        # 1% risk-per-trade sizing
        portfolio_value = self.get_portfolio_value({result.symbol: entry_price})
        risk_per_trade  = portfolio_value * 0.01
        risk_per_share  = entry_price - stop_loss
        quantity        = max(1, int(risk_per_trade / risk_per_share))
        cost            = quantity * entry_price

        if cost > self.capital:
            # Scale down to what we can afford
            quantity = max(1, int(self.capital / entry_price))
            cost     = quantity * entry_price
            if cost > self.capital:
                log.debug(
                    "portfolio.enter: SKIP %s — insufficient capital (need %.2f, have %.2f)",
                    result.symbol, cost, self.capital,
                )
                return False

        # Build a partial BacktestTrade to represent the open position.
        # Exit fields are left at placeholder values and will be filled in
        # by close().
        trade = BacktestTrade(
            symbol=result.symbol,
            entry_date=entry_date,
            exit_date=entry_date,           # placeholder
            entry_price=entry_price,
            exit_price=entry_price,         # placeholder
            stop_loss_price=stop_loss,
            peak_price=entry_price,
            trailing_stop_used=stop_loss,
            stop_type="trailing",
            quantity=quantity,
            pnl=0.0,
            pnl_pct=0.0,
            r_multiple=0.0,
            exit_reason="open",
            regime="Unknown",
            setup_quality=result.setup_quality,
            sepa_score=result.score,
        )

        self.positions[result.symbol] = trade
        self.capital -= cost

        log.info(
            "portfolio.enter: ENTERED %s @ %.2f × %d (cost=%.2f, capital_left=%.2f)",
            result.symbol, entry_price, quantity, cost, self.capital,
        )
        return True

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------

    def close(
        self,
        symbol: str,
        exit_price: float,
        exit_date: date,
        reason: str,
    ) -> BacktestTrade:
        """Close an open position and realise P&L.

        Parameters
        ----------
        symbol:
            Ticker of the position to close.
        exit_price:
            Execution price at exit.
        exit_date:
            Date of exit.
        reason:
            Exit reason string (e.g. ``"trailing_stop"``, ``"target"``).

        Returns
        -------
        BacktestTrade
            Fully populated trade with P&L and R-multiple fields filled in.

        Raises
        ------
        KeyError
            If *symbol* is not in ``self.positions``.
        """
        open_trade = self.positions.pop(symbol)

        pnl       = (exit_price - open_trade.entry_price) * open_trade.quantity
        pnl_pct   = (exit_price / open_trade.entry_price - 1.0) * 100.0
        risk_amt  = open_trade.entry_price - open_trade.stop_loss_price
        r_multiple = (exit_price - open_trade.entry_price) / risk_amt if risk_amt > 0 else 0.0

        closed = BacktestTrade(
            symbol=open_trade.symbol,
            entry_date=open_trade.entry_date,
            exit_date=exit_date,
            entry_price=open_trade.entry_price,
            exit_price=exit_price,
            stop_loss_price=open_trade.stop_loss_price,
            peak_price=open_trade.peak_price,
            trailing_stop_used=open_trade.trailing_stop_used,
            stop_type=open_trade.stop_type,
            quantity=open_trade.quantity,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 4),
            r_multiple=round(r_multiple, 3),
            exit_reason=reason,
            regime=open_trade.regime,
            setup_quality=open_trade.setup_quality,
            sepa_score=open_trade.sepa_score,
        )

        self.capital += exit_price * open_trade.quantity
        self.closed_trades.append(closed)

        log.info(
            "portfolio.close: CLOSED %s @ %.2f on %s (%s) pnl=%.2f R=%.2f",
            symbol, exit_price, exit_date, reason, pnl, r_multiple,
        )
        return closed

    # ------------------------------------------------------------------
    # Equity snapshot
    # ------------------------------------------------------------------

    def record_equity(self, current_prices: dict[str, float], backtest_date: date) -> None:
        """Append an equity snapshot to ``self.equity_curve``.

        Parameters
        ----------
        current_prices:
            Mapping of symbol → latest close price.
        backtest_date:
            The date of this snapshot.
        """
        portfolio_value = self.get_portfolio_value(current_prices)
        self.equity_curve.append(
            {
                "date": backtest_date,
                "portfolio_value": round(portfolio_value, 2),
                "cash": round(self.capital, 2),
                "open_positions": len(self.positions),
            }
        )

    # ------------------------------------------------------------------
    # Portfolio valuation
    # ------------------------------------------------------------------

    def get_portfolio_value(self, current_prices: dict[str, float]) -> float:
        """Return total portfolio value: cash + mark-to-market open positions.

        Parameters
        ----------
        current_prices:
            Mapping of symbol → latest close price.  Symbols not in the
            dict are valued at their entry price (safe fallback).

        Returns
        -------
        float
            Total portfolio value.
        """
        position_value = sum(
            trade.quantity * current_prices.get(sym, trade.entry_price)
            for sym, trade in self.positions.items()
        )
        return self.capital + position_value
