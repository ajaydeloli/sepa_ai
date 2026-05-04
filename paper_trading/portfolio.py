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
    pyramided: bool = False    # True once a pyramid add has been made
    pyramid_qty: int = 0       # Extra shares added via pyramid
    peak_close: float = 0.0    # Highest close since entry (trailing stop anchor)
    trailing_stop: float = 0.0 # Current trailing stop (updated each check_exits call)
    days_held: int = 0         # Trading days held since entry


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
    exit_reason: str   # "trailing_stop" | "target" | "max_hold_days" | "manual" | "end_of_backtest"
    r_multiple: float  # (exit_price - entry_price) / (entry_price - stop_loss)


# ---------------------------------------------------------------------------
# Portfolio — live state container
# ---------------------------------------------------------------------------


@dataclass
class Portfolio:
    """In-memory container for cash, open positions, and closed trade history."""

    initial_capital: float
    config: dict = field(repr=False)
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict, init=False)
    closed_trades: list[ClosedTrade] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.cash = self.initial_capital
        self.equity_curve: list[dict] = []
        # Each entry: {"date": str(date), "total_value": float, "cash": float}

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add_position(self, position: Position) -> None:
        """Add *position* to the portfolio, deducting cost from cash.

        If the full cost exceeds available cash the quantity is capped to
        what cash can cover.  A warning is logged and the capped position
        is still added.  If even qty=1 is unaffordable the position is
        silently skipped.
        """
        cost = position.entry_price * position.quantity
        if cost > self.cash:
            max_qty = max(1, int(self.cash / position.entry_price))
            log.warning(
                "add_position: %s qty capped %d→%d (cash=%.2f < cost=%.2f)",
                position.symbol, position.quantity, max_qty, self.cash, cost,
            )
            position.quantity = max_qty
            cost = position.entry_price * max_qty
        if cost > self.cash:
            log.warning(
                "add_position: %s cannot afford qty=1 @ %.2f — skipping",
                position.symbol, position.entry_price,
            )
            return
        self.cash -= cost
        self.positions[position.symbol] = position
        log.debug(
            "add_position: %s qty=%d entry=%.2f cash_left=%.2f",
            position.symbol, position.quantity, position.entry_price, self.cash,
        )

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str,
        exit_date: date,
    ) -> ClosedTrade:
        """Remove *symbol* from open positions, credit cash, return ClosedTrade.

        total_qty = original quantity + any pyramid add shares.
        pnl reflects the gross gain/loss before any brokerage deduction
        (caller is responsible for subtracting brokerage when needed).
        """
        pos = self.positions.pop(symbol)
        total_qty = pos.quantity + pos.pyramid_qty
        gross_proceeds = exit_price * total_qty
        pnl = gross_proceeds - pos.entry_price * total_qty
        pnl_pct = (exit_price / pos.entry_price - 1.0) * 100.0
        risk = pos.entry_price - pos.stop_loss
        r_multiple = (exit_price - pos.entry_price) / risk if risk > 0 else 0.0
        self.cash += gross_proceeds
        trade = ClosedTrade(
            symbol=symbol,
            entry_date=pos.entry_date,
            exit_date=exit_date,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            quantity=total_qty,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            r_multiple=r_multiple,
        )
        self.closed_trades.append(trade)
        log.debug(
            "close_position: %s exit=%.2f reason=%s pnl=%.2f",
            symbol, exit_price, exit_reason, pnl,
        )
        return trade

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_total_value(self, current_prices: dict[str, float]) -> float:
        """Return cash + mark-to-market value of all open positions.

        Positions with no price in *current_prices* fall back to entry_price.
        """
        pos_value = 0.0
        for sym, pos in self.positions.items():
            price = current_prices.get(sym, pos.entry_price)
            pos_value += price * (pos.quantity + pos.pyramid_qty)
        return self.cash + pos_value

    def record_equity_point(self, current_prices: dict, run_date: date) -> None:
        """Append a daily equity snapshot to equity_curve.

        Called once per day by pipeline/runner.py after check_exits.
        Each entry: {"date": str(date), "total_value": float, "cash": float}
        """
        open_value = sum(
            current_prices.get(sym, pos.entry_price) * (pos.quantity + pos.pyramid_qty)
            for sym, pos in self.positions.items()
        )
        self.equity_curve.append({
            "date":        str(run_date),
            "total_value": round(self.cash + open_value, 2),
            "cash":        round(self.cash, 2),
        })

    def get_summary(self, current_prices: dict[str, float]) -> dict[str, Any]:
        """Return full portfolio summary including P&L, risk metrics, and open positions.

        Returns
        -------
        dict with keys:
            cash, open_value, total_value, initial_capital,
            total_return_pct, realised_pnl, unrealised_pnl,
            win_rate (fraction 0-1), total_trades, open_count, closed_count,
            avg_r_multiple, profit_factor,
            best_trade_pct, worst_trade_pct, avg_hold_days,
            positions: list[{symbol, entry_price, current_price,
                              unrealised_pnl_pct, days_held,
                              stop_loss, trailing_stop, quality}]
        """
        # --- open position value -------------------------------------------------
        open_value = sum(
            current_prices.get(sym, pos.entry_price) * (pos.quantity + pos.pyramid_qty)
            for sym, pos in self.positions.items()
        )
        total = round(self.cash + open_value, 2)

        # --- P&L ----------------------------------------------------------------
        cost_basis = sum(
            pos.entry_price * (pos.quantity + pos.pyramid_qty)
            for pos in self.positions.values()
        )
        unrealised_pnl = open_value - cost_basis
        realised_pnl = sum(t.pnl for t in self.closed_trades)

        # --- win / loss stats ---------------------------------------------------
        wins   = [t for t in self.closed_trades if t.pnl > 0]
        losses = [t for t in self.closed_trades if t.pnl <= 0]
        n = len(self.closed_trades)
        win_rate = round(len(wins) / n, 4) if n else 0.0

        sum_wins   = sum(t.pnl for t in wins)
        sum_losses = abs(sum(t.pnl for t in losses))
        profit_factor = round(sum_wins / sum_losses, 4) if sum_losses > 0 else 0.0

        avg_r = round(sum(t.r_multiple for t in self.closed_trades) / n, 4) if n else 0.0

        best_trade_pct  = round(max((t.pnl_pct for t in self.closed_trades), default=0.0), 4)
        worst_trade_pct = round(min((t.pnl_pct for t in self.closed_trades), default=0.0), 4)

        avg_hold_days = 0.0
        if self.closed_trades:
            avg_hold_days = round(
                sum((t.exit_date - t.entry_date).days for t in self.closed_trades) / n, 2
            )

        # --- open positions detail ----------------------------------------------
        positions_list = []
        for sym, pos in self.positions.items():
            cp = current_prices.get(sym, pos.entry_price)
            positions_list.append({
                "symbol":            sym,
                "entry_price":       pos.entry_price,
                "current_price":     cp,
                "unrealised_pnl_pct": round((cp / pos.entry_price - 1.0) * 100.0, 4),
                "days_held":         pos.days_held,
                "stop_loss":         pos.stop_loss,
                "trailing_stop":     pos.trailing_stop,
                "quality":           pos.setup_quality,
            })

        return {
            "cash":             round(self.cash, 2),
            "open_value":       round(open_value, 2),
            "total_value":      total,
            "initial_capital":  self.initial_capital,
            "total_return_pct": round((total / self.initial_capital - 1.0) * 100.0, 4),
            "realised_pnl":     round(realised_pnl, 2),
            "unrealised_pnl":   round(unrealised_pnl, 2),
            "win_rate":         win_rate,
            "total_trades":     n,
            "open_count":       len(self.positions),
            "closed_count":     n,
            "avg_r_multiple":   avg_r,
            "profit_factor":    profit_factor,
            "best_trade_pct":   best_trade_pct,
            "worst_trade_pct":  worst_trade_pct,
            "avg_hold_days":    avg_hold_days,
            "positions":        positions_list,
        }

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict; round-trips via from_json."""

        def _pos(p: Position) -> dict:
            return {
                "symbol": p.symbol,
                "entry_date": p.entry_date.isoformat(),
                "entry_price": p.entry_price,
                "quantity": p.quantity,
                "stop_loss": p.stop_loss,
                "target_price": p.target_price,
                "sepa_score": p.sepa_score,
                "setup_quality": p.setup_quality,
                "pyramided": p.pyramided,
                "pyramid_qty": p.pyramid_qty,
                "peak_close": p.peak_close,
                "trailing_stop": p.trailing_stop,
                "days_held": p.days_held,
            }

        def _trade(t: ClosedTrade) -> dict:
            return {
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

        return {
            "initial_capital": self.initial_capital,
            "cash": self.cash,
            "positions": {sym: _pos(pos) for sym, pos in self.positions.items()},
            "closed_trades": [_trade(t) for t in self.closed_trades],
            "equity_curve": self.equity_curve,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any], config: dict) -> "Portfolio":
        """Reconstruct a Portfolio from a dict produced by to_json()."""
        portfolio = cls(initial_capital=data["initial_capital"], config=config)
        portfolio.cash = data["cash"]
        portfolio.equity_curve = data.get("equity_curve", [])

        for sym, pd_ in data.get("positions", {}).items():
            pos = Position(
                symbol=pd_["symbol"],
                entry_date=date.fromisoformat(pd_["entry_date"]),
                entry_price=pd_["entry_price"],
                quantity=pd_["quantity"],
                stop_loss=pd_["stop_loss"],
                target_price=pd_.get("target_price"),
                sepa_score=pd_["sepa_score"],
                setup_quality=pd_["setup_quality"],
                pyramided=pd_.get("pyramided", False),
                pyramid_qty=pd_.get("pyramid_qty", 0),
                peak_close=pd_.get("peak_close", 0.0),
                trailing_stop=pd_.get("trailing_stop", 0.0),
                days_held=pd_.get("days_held", 0),
            )
            portfolio.positions[sym] = pos

        for td_ in data.get("closed_trades", []):
            trade = ClosedTrade(
                symbol=td_["symbol"],
                entry_date=date.fromisoformat(td_["entry_date"]),
                exit_date=date.fromisoformat(td_["exit_date"]),
                entry_price=td_["entry_price"],
                exit_price=td_["exit_price"],
                quantity=td_["quantity"],
                pnl=td_["pnl"],
                pnl_pct=td_["pnl_pct"],
                exit_reason=td_["exit_reason"],
                r_multiple=td_["r_multiple"],
            )
            portfolio.closed_trades.append(trade)

        log.debug(
            "from_json: loaded portfolio cash=%.2f open=%d closed=%d",
            portfolio.cash, len(portfolio.positions), len(portfolio.closed_trades),
        )
        return portfolio


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def get_r_multiple(trade: ClosedTrade) -> float:
    """Return the R-multiple for *trade*.

    R = (exit_price − entry_price) / (entry_price − stop_loss)

    The value is pre-computed and stored on the trade at close time.
    Returns 0.0 if entry_price == stop_loss (zero risk; avoid division error).
    """
    return trade.r_multiple
