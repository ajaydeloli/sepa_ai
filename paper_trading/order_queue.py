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
- Expired BUY orders (expiry_date < run_date) are logged and removed.
- Never import from screener/, api/, or dashboard/.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

from utils.logger import get_logger
from utils.trading_calendar import is_trading_day, next_trading_day  # noqa: F401

if TYPE_CHECKING:
    from paper_trading.portfolio import ClosedTrade, Portfolio, Position

log = get_logger(__name__)

# Resolve absolute path so it works regardless of process cwd.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
ORDERS_FILE = str(_PROJECT_ROOT / "data" / "paper_trading" / "pending_orders.json")

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN_HOUR   = 9
MARKET_OPEN_MINUTE = 15
MARKET_CLOSE_HOUR   = 15
MARKET_CLOSE_MINUTE = 30


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


def _add_trading_days(start: date, n: int) -> date:
    """Return the date that is *n* NSE trading days strictly after *start*."""
    current = start
    for _ in range(n):
        current = next_trading_day(current)
    return current


# ---------------------------------------------------------------------------
# Public API — market hours
# ---------------------------------------------------------------------------


def is_market_open(dt: datetime | None = None) -> bool:
    """Return True if *dt* falls within NSE market hours on a trading day.

    Parameters
    ----------
    dt:
        Datetime to test.  Defaults to ``datetime.now(IST)``.
        A naive datetime is assumed to be in IST.

    Returns
    -------
    bool
        ``True`` iff today is an NSE trading day **and** the time is
        between 09:15 and 15:30 IST (both endpoints inclusive).
    """
    if dt is None:
        dt = datetime.now(IST)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)

    if not is_trading_day(dt.date()):
        return False

    market_open  = dt.replace(hour=MARKET_OPEN_HOUR,  minute=MARKET_OPEN_MINUTE,  second=0, microsecond=0)
    market_close = dt.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    return market_open <= dt <= market_close


# ---------------------------------------------------------------------------
# Public API — queue management
# ---------------------------------------------------------------------------


def queue_order(
    symbol: str,
    order_type: str,
    result_dict: dict,
    expiry_days: int = 3,
) -> None:
    """Persist a pending order to ORDERS_FILE.

    Parameters
    ----------
    symbol:       Ticker symbol.
    order_type:   "BUY" or "SELL".
    result_dict:  JSON-serialisable dict (typically from SEPAResult fields).
    expiry_days:  Number of NSE trading days until the order expires.
                  Defaults to 3.  ``expiry_date`` = queued_at + expiry_days
                  trading days.
    """
    orders = _read_orders()
    queued_at   = datetime.now(IST).date()
    expiry_date = _add_trading_days(queued_at, expiry_days)

    orders.append({
        "symbol":      symbol,
        "order_type":  order_type,
        "result":      result_dict,
        "queued_at":   queued_at.isoformat(),
        "expiry_date": expiry_date.isoformat(),
    })
    _write_orders(orders)
    log.info(
        "queue_order: %s %s queued for next open, expires %s",
        order_type, symbol, expiry_date,
    )


def execute_pending_orders(
    portfolio: "Portfolio",
    current_prices: dict[str, float],
    run_date: date,
) -> list:
    """Execute all pending orders at market open with slippage applied.

    Called at 09:15 IST (or the start of the daily pipeline run).

    For each pending BUY order the function:
    1. Skips (and removes) orders whose ``expiry_date`` is before *run_date*,
       logging ``"Order expired: {symbol} queued {days} ago"``.
    2. Keeps in queue orders for symbols absent from *current_prices*.
    3. Drops (without error) BUY orders for symbols already held.
    4. Keeps in queue BUY orders when ``max_positions`` is already reached.
    5. Fills the order at ``current_prices[symbol] * (1 + slippage_pct)``.

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
    from paper_trading.portfolio import Position  # local import — avoids circular

    orders = _read_orders()
    if not orders:
        return []

    config_pt        = portfolio.config.get("paper_trading", {})
    slippage_pct     = config_pt.get("slippage_pct", 0.15) / 100.0
    risk_per_trade_pct = config_pt.get("risk_per_trade_pct", 2.0)
    max_positions    = config_pt.get("max_positions", 10)

    executed:  list       = []
    remaining: list[dict] = []

    for order in orders:
        symbol     = order["symbol"]
        order_type = order["order_type"]

        # ------------------------------------------------------------------ BUY
        if order_type == "BUY":

            # 1. Expiry check -------------------------------------------------
            expiry_str = order.get("expiry_date")
            if expiry_str:
                expiry_date = date.fromisoformat(expiry_str)
                if expiry_date < run_date:
                    queued_str = order.get("queued_at", run_date.isoformat())
                    days_ago   = (run_date - date.fromisoformat(queued_str)).days
                    log.info("Order expired: %s queued %d ago", symbol, days_ago)
                    continue  # drop from queue — do NOT add to remaining

            # 2. Price availability -------------------------------------------
            if symbol not in current_prices:
                log.warning("execute_pending_orders: no price for %s — keeping in queue", symbol)
                remaining.append(order)
                continue

            # 3. Already in portfolio -----------------------------------------
            if symbol in portfolio.positions:
                log.warning("execute_pending_orders: %s already in portfolio — dropping BUY", symbol)
                continue  # drop silently

            # 4. Capacity check -----------------------------------------------
            if len(portfolio.positions) >= max_positions:
                log.info("execute_pending_orders: max_positions reached — keeping %s in queue", symbol)
                remaining.append(order)
                continue

            # 5. Size and fill ------------------------------------------------
            price      = current_prices[symbol]
            fill_price = price * (1.0 + slippage_pct)
            result_d   = order.get("result", {})
            stop_loss    = result_d.get("stop_loss") or fill_price * 0.93
            target_price = result_d.get("target_price")
            sepa_score   = int(result_d.get("score", 0))
            setup_quality = result_d.get("setup_quality", "B")

            risk_per_share = fill_price - float(stop_loss)
            if risk_per_share <= 0:
                risk_per_share = fill_price * 0.07

            total_val   = portfolio.get_total_value(current_prices)
            risk_amount = total_val * (risk_per_trade_pct / 100.0)
            quantity    = max(1, int(risk_amount / risk_per_share))

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
                symbol, quantity, fill_price,
            )

        # ---------------------------------------------------------------- SELL
        elif order_type == "SELL":
            if symbol not in current_prices:
                log.warning("execute_pending_orders: no price for %s — keeping in queue", symbol)
                remaining.append(order)
                continue

            if symbol not in portfolio.positions:
                log.warning("execute_pending_orders: %s not in portfolio — dropping SELL", symbol)
                continue

            price      = current_prices[symbol]
            fill_price = price * (1.0 - slippage_pct)
            trade      = portfolio.close_position(symbol, fill_price, "manual", run_date)
            executed.append(trade)
            log.info(
                "execute_pending_orders: SELL FILLED %s @ %.2f  pnl=%.2f",
                symbol, fill_price, trade.pnl,
            )

        # --------------------------------------------------------- Unknown type
        else:
            log.warning(
                "execute_pending_orders: unknown order_type '%s' for %s — skipping",
                order_type, symbol,
            )
            remaining.append(order)

    _write_orders(remaining)
    return executed


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def get_pending_orders() -> list[dict]:
    """Return all currently queued orders without modifying the queue."""
    return _read_orders()


def clear_pending_orders() -> None:
    """Remove all pending orders from the queue."""
    _write_orders([])
    log.info("clear_pending_orders: queue cleared")
