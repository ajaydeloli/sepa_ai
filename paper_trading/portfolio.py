"""
paper_trading/portfolio.py
--------------------------
In-memory portfolio state with JSON persistence.

Design rules
------------
- Cash can never go negative; add_position silently caps quantity to
  what available cash can afford and logs a warning.
- to_json / from_json provide a lossless round-trip (dates stored as
  ISO-8601 strings).
- No SQLite; all state lives in data/paper_trading/portfolio.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from utils.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data-classes
# ---------------------------------------------------------------------------


@dataclass
class Position:
    symbol: str
    entry_date: date
    entry_price: float
    quantity: int
    stop_loss: float
    target_price: float | None
    sepa_score: int
    setup_quality: str
    pyramided: bool = False   # True once a pyramid add has been made
    pyramid_qty: int = 0      # Extra shares added via pyramid


@dataclass
class ClosedTrade:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    exit_reason: str    # "stop_loss" | "target" | "manual" | "end_of_backtest"
    r_multiple: float   # (exit_price - entry_price) / (entry_price - stop_loss)


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


class Portfolio:
    def __init__(self, initial_capital: float, config: dict) -> None:
        self.cash: float = initial_capital
        self.initial_capital: float = initial_capital
        self.positions: dict[str, Position] = {}
        self.closed_trades: list[ClosedTrade] = []
        self.config = config

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add_position(self, position: Position) -> None:
        """Open a new position, deducting its cost from cash.

        Quantity is silently reduced if cost exceeds available cash so
        cash never goes negative.  A warning is logged when capping occurs.
        """
        cost = position.entry_price * position.quantity
        if cost > self.cash:
            log.warning(
                "add_position: %s cost %.2f exceeds cash %.2f — capping quantity",
                position.symbol,
                cost,
                self.cash,
            )
            position.quantity = max(1, int(self.cash / position.entry_price))
            cost = position.entry_price * position.quantity

        self.cash -= cost
        self.positions[position.symbol] = position
        log.info(
            "add_position: %s qty=%d @ %.2f  cash_remaining=%.2f",
            position.symbol,
            position.quantity,
            position.entry_price,
            self.cash,
        )

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        reason: str,
        exit_date: date,
    ) -> ClosedTrade:
        """Close an open position, return proceeds to cash, record the trade.

        Parameters
        ----------
        symbol:      Ticker of the position to close.
        exit_price:  Fill price (caller applies any slippage before passing).
        reason:      One of "stop_loss", "target", "manual", "end_of_backtest".
        exit_date:   Trading date of the exit.
        """
        position = self.positions.pop(symbol)
        total_qty = position.quantity + position.pyramid_qty
        proceeds = exit_price * total_qty
        cost_basis = position.entry_price * total_qty

        pnl = proceeds - cost_basis
        pnl_pct = (pnl / cost_basis * 100.0) if cost_basis else 0.0

        risk = position.entry_price - position.stop_loss
        r_multiple = (
            (exit_price - position.entry_price) / risk if risk > 0 else 0.0
        )

        self.cash += proceeds

        trade = ClosedTrade(
            symbol=symbol,
            entry_date=position.entry_date,
            exit_date=exit_date,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=total_qty,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 4),
            exit_reason=reason,
            r_multiple=round(r_multiple, 4),
        )
        self.closed_trades.append(trade)
        log.info(
            "close_position: %s qty=%d @ %.2f  pnl=%.2f  reason=%s",
            symbol,
            total_qty,
            exit_price,
            pnl,
            reason,
        )
        return trade

    # ------------------------------------------------------------------
    # Read-only queries
    # ------------------------------------------------------------------

    def get_open_value(self, current_prices: dict[str, float]) -> float:
        """Mark-to-market value of all open positions.

        Falls back to entry_price for any symbol absent from current_prices.
        """
        total = 0.0
        for symbol, pos in self.positions.items():
            price = current_prices.get(symbol, pos.entry_price)
            total += price * (pos.quantity + pos.pyramid_qty)
        return total

    def get_total_value(self, current_prices: dict[str, float]) -> float:
        """Cash + open market value."""
        return self.cash + self.get_open_value(current_prices)

    def get_summary(self, current_prices: dict[str, float]) -> dict[str, Any]:
        """Return a comprehensive snapshot dict.

        Keys: cash, open_value, total_value, initial_capital,
        total_return_pct, realised_pnl, unrealised_pnl, win_rate,
        total_trades, open_count, closed_count, positions.
        """
        open_value = self.get_open_value(current_prices)
        total_value = self.cash + open_value
        total_return_pct = (
            (total_value - self.initial_capital) / self.initial_capital * 100.0
            if self.initial_capital
            else 0.0
        )
        realised_pnl = sum(t.pnl for t in self.closed_trades)
        winning_count = sum(1 for t in self.closed_trades if t.pnl > 0)
        win_rate = (
            winning_count / len(self.closed_trades) * 100.0
            if self.closed_trades
            else 0.0
        )

        unrealised_pnl = 0.0
        positions_list: list[dict] = []
        for symbol, pos in self.positions.items():
            price = current_prices.get(symbol, pos.entry_price)
            total_qty = pos.quantity + pos.pyramid_qty
            cost = pos.entry_price * total_qty
            mkt_val = price * total_qty
            unreal = mkt_val - cost
            unreal_pct = (unreal / cost * 100.0) if cost else 0.0
            unrealised_pnl += unreal
            positions_list.append(
                {
                    "symbol": symbol,
                    "entry_date": pos.entry_date.isoformat(),
                    "entry_price": pos.entry_price,
                    "current_price": price,
                    "quantity": total_qty,
                    "stop_loss": pos.stop_loss,
                    "target_price": pos.target_price,
                    "setup_quality": pos.setup_quality,
                    "sepa_score": pos.sepa_score,
                    "pyramided": pos.pyramided,
                    "unrealised_pnl": round(unreal, 2),
                    "unrealised_pnl_pct": round(unreal_pct, 4),
                }
            )

        return {
            "cash": round(self.cash, 2),
            "open_value": round(open_value, 2),
            "total_value": round(total_value, 2),
            "initial_capital": self.initial_capital,
            "total_return_pct": round(total_return_pct, 4),
            "realised_pnl": round(realised_pnl, 2),
            "unrealised_pnl": round(unrealised_pnl, 2),
            "win_rate": round(win_rate, 4),
            "total_trades": len(self.closed_trades),
            "open_count": len(self.positions),
            "closed_count": len(self.closed_trades),
            "positions": positions_list,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_json(self) -> dict:
        """Serialise full portfolio state to a JSON-compatible dict."""
        return {
            "cash": self.cash,
            "initial_capital": self.initial_capital,
            "positions": {
                sym: {
                    "symbol": pos.symbol,
                    "entry_date": pos.entry_date.isoformat(),
                    "entry_price": pos.entry_price,
                    "quantity": pos.quantity,
                    "stop_loss": pos.stop_loss,
                    "target_price": pos.target_price,
                    "sepa_score": pos.sepa_score,
                    "setup_quality": pos.setup_quality,
                    "pyramided": pos.pyramided,
                    "pyramid_qty": pos.pyramid_qty,
                }
                for sym, pos in self.positions.items()
            },
            "closed_trades": [
                {
                    "symbol": t.symbol,
                    "entry_date": t.entry_date.isoformat(),
                    "exit_date": t.exit_date.isoformat(),
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "quantity": t.quantity,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct,
                    "exit_reason": t.exit_reason,
                    "r_multiple": t.r_multiple,
                }
                for t in self.closed_trades
            ],
        }

    @classmethod
    def from_json(cls, data: dict, config: dict) -> "Portfolio":
        """Reconstruct a Portfolio from a previously serialised dict."""
        portfolio = cls(initial_capital=data["initial_capital"], config=config)
        portfolio.cash = data["cash"]

        for sym, p in data.get("positions", {}).items():
            portfolio.positions[sym] = Position(
                symbol=p["symbol"],
                entry_date=date.fromisoformat(p["entry_date"]),
                entry_price=p["entry_price"],
                quantity=p["quantity"],
                stop_loss=p["stop_loss"],
                target_price=p.get("target_price"),
                sepa_score=p["sepa_score"],
                setup_quality=p["setup_quality"],
                pyramided=p.get("pyramided", False),
                pyramid_qty=p.get("pyramid_qty", 0),
            )

        for t in data.get("closed_trades", []):
            portfolio.closed_trades.append(
                ClosedTrade(
                    symbol=t["symbol"],
                    entry_date=date.fromisoformat(t["entry_date"]),
                    exit_date=date.fromisoformat(t["exit_date"]),
                    entry_price=t["entry_price"],
                    exit_price=t["exit_price"],
                    quantity=t["quantity"],
                    pnl=t["pnl"],
                    pnl_pct=t["pnl_pct"],
                    exit_reason=t["exit_reason"],
                    r_multiple=t["r_multiple"],
                )
            )

        return portfolio
