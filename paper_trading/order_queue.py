"""
paper_trading/order_queue.py
-----------------------------
File-based pending-order queue for the paper-trading simulator.

Orders are persisted to ``data/paper_trading/pending_orders.json`` so they
survive process restarts.  They execute at the next market open (09:15 IST)
via ``execute_pending_orders()``.

Design rules
------------
- Only BUY and SELL order types are supported.
- Slippage for BUY:  fill = price × (1 + slippage_pct)
- Slippage for SELL: fill = price × (1 − slippage_pct)
- Orders for symbols with no price in current_prices are left in queue.
- A BUY for a symbol already in the portfolio is silently dropped.
- A SELL for a symbol not in the portfolio is silently dropped.
- Never import from screener/, api/, or dashboard/.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from utils.logger import get_logger
from utils.trading_calendar import is_trading_day, next_trading_day  # noqa: F401

if TYPE_CHECKING:
    from paper_trading.portfolio import ClosedTrade, Portfolio, Position

log = get_logger(__name__)

# Resolve absolute path so it works regardless of process cwd.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
ORDERS_FILE = str(_PROJECT_ROOT / "data" / "paper_trading" / "pending_orders.json")


# ---------------------------------------------------------------------------
# Private I/O helpers
# ---------------------------------------------------------------------------


def _read_orders() -> list[dict]:
    path = Path(ORDERS_FILE)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("_read_orders: could not parse %s (%s) — returning []", ORDERS_FILE, exc)
        return []


def _write_orders(orders: list[dict]) -> None:
    path = Path(ORDERS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(orders, fh, indent=2, default=str)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def queue_order(symbol: str, order_type: str, result_dict: dict) -> None:
    """Persist a pending order to ORDERS_FILE.

    Parameters
    ----------
    symbol:      Ticker symbol.
    order_type:  "BUY" or "SELL".
    result_dict: JSON-serialisable dict (typically from SEPAResult fields).
    """
    orders = _read_orders()
    orders.append(
        {
            "symbol": symbol,
            "order_type": order_type,
            "result": result_dict,
        }
    )
    _write_orders(orders)
    log.info("queue_order: %s %s queued for next open", order_type, symbol)


def execute_pending_orders(
    portfolio: "Portfolio",
    current_prices: dict[str, float],
    run_date: date,
) -> list:
    """Execute all pending orders at market open with slippage applied.

    Called at 09:15 IST (or the start of the daily pipeline run).

    Parameters
    ----------
    portfolio:      Live Portfolio instance — mutated in place.
    current_prices: Symbol → latest price dict (e.g. from today's open bar).
    run_date:       The trading date being processed.

    Returns
    -------
    list
        Mixed list of Position (for BUY fills) and ClosedTrade (for SELL
        fills) objects, in execution order.
    """
    from paper_trading.portfolio import Position  # local import to avoid circular

    orders = _read_orders()
    if not orders:
        return []

    config_pt = portfolio.config.get("paper_trading", {})
    slippage_pct = config_pt.get("slippage_pct", 0.15) / 100.0
    risk_per_trade_pct = config_pt.get("risk_per_trade_pct", 2.0)

    executed: list = []
    remaining: list[dict] = []

    for order in orders:
        symbol = order["symbol"]
        order_type = order["order_type"]

        if symbol not in current_prices:
            log.warning(
                "execute_pending_orders: no price for %s — keeping in queue", symbol
            )
            remaining.append(order)
            continue

        price = current_prices[symbol]

        if order_type == "BUY":
            if symbol in portfolio.positions:
                log.warning(
                    "execute_pending_orders: %s already in portfolio — dropping BUY", symbol
                )
                continue

            fill_price = price * (1.0 + slippage_pct)
            result_d = order.get("result", {})
            stop_loss = result_d.get("stop_loss") or fill_price * 0.93
            target_price = result_d.get("target_price")
            sepa_score = int(result_d.get("score", 0))
            setup_quality = result_d.get("setup_quality", "B")

            risk_per_share = fill_price - float(stop_loss)
            if risk_per_share <= 0:
                risk_per_share = fill_price * 0.07

            total_val = portfolio.get_total_value(current_prices)
            risk_amount = total_val * (risk_per_trade_pct / 100.0)
            quantity = max(1, int(risk_amount / risk_per_share))

            # Cap at available cash
            if fill_price * quantity > portfolio.cash:
                quantity = max(1, int(portfolio.cash / fill_price))

            position = Position(
                symbol=symbol,
                entry_date=run_date,
                entry_price=fill_price,
                quantity=quantity,
                stop_loss=float(stop_loss),
                target_price=target_price,
                sepa_score=sepa_score,
                setup_quality=setup_quality,
            )
            portfolio.add_position(position)
            executed.append(position)
            log.info(
                "execute_pending_orders: BUY FILLED %s qty=%d @ %.2f",
                symbol,
                quantity,
                fill_price,
            )

        elif order_type == "SELL":
            if symbol not in portfolio.positions:
                log.warning(
                    "execute_pending_orders: %s not in portfolio — dropping SELL", symbol
                )
                continue

            fill_price = price * (1.0 - slippage_pct)
            trade = portfolio.close_position(symbol, fill_price, "manual", run_date)
            executed.append(trade)
            log.info(
                "execute_pending_orders: SELL FILLED %s @ %.2f  pnl=%.2f",
                symbol,
                fill_price,
                trade.pnl,
            )

        else:
            log.warning(
                "execute_pending_orders: unknown order_type '%s' for %s — skipping",
                order_type,
                symbol,
            )
            remaining.append(order)

    _write_orders(remaining)
    return executed


def get_pending_orders() -> list[dict]:
    """Return all currently queued orders without modifying the queue."""
    return _read_orders()


def clear_pending_orders() -> None:
    """Remove all pending orders from the queue."""
    _write_orders([])
    log.info("clear_pending_orders: queue cleared")
