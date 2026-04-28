"""
paper_trading/simulator.py
--------------------------
Core paper-trading logic: enter, pyramid, exit, and reset.

Market-hours gate
-----------------
enter_trade() fills immediately when:
  is_trading_day(run_date) AND clock is between 09:15–15:30 IST.
Otherwise the order is queued via order_queue.queue_order() for the next
market open, and the function returns None.

Position-sizing formula
-----------------------
    risk_amount    = total_portfolio_value × (risk_per_trade_pct / 100)
    risk_per_share = fill_price − stop_loss           (min 7 % fallback)
    quantity       = max(1, int(risk_amount / risk_per_share))
    capped so that fill_price × quantity ≤ portfolio.cash

Slippage
--------
    fill_price = current_price × (1 + slippage_pct / 100)
    config["paper_trading"]["slippage_pct"] is expressed in percent
    (e.g. 0.15 means 0.15 %, i.e. a multiplier of 0.0015).

Anti-patterns enforced
----------------------
- Never import from screener/, api/, or dashboard/.
- Cash never goes negative (double-gated by Portfolio.add_position).
- One pyramid per position — guarded by position.pyramided flag.
- State is file-based JSON only, no SQLite.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from paper_trading.order_queue import (
    ORDERS_FILE,
    queue_order,
)
from paper_trading.portfolio import ClosedTrade, Portfolio, Position
from rules.scorer import SEPAResult
from utils.logger import get_logger
from utils.trading_calendar import is_trading_day

log = get_logger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
_MARKET_OPEN = time(9, 15)
_MARKET_CLOSE = time(15, 30)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PT_DIR = _PROJECT_ROOT / "data" / "paper_trading"
_PORTFOLIO_FILE = _PT_DIR / "portfolio.json"
_TRADES_FILE = _PT_DIR / "trades.json"


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _is_market_hours() -> bool:
    """Return True if current IST wall-clock time is within 09:15–15:30."""
    now_ist = datetime.now(tz=_IST).time()
    return _MARKET_OPEN <= now_ist <= _MARKET_CLOSE


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enter_trade(
    result: SEPAResult,
    portfolio: Portfolio,
    current_price: float,
    run_date: date,
) -> Position | None:
    """Attempt to open a new paper position for result.symbol.

    Returns the new Position if filled immediately, or None if the order
    was queued (non-trading day / outside market hours) or any pre-condition
    failed.

    Pre-conditions (all must pass, else return None silently)
    ---------------------------------------------------------
    1. result.stage == 2
    2. result.score >= config["paper_trading"]["min_score_to_trade"]
    3. symbol NOT already in portfolio.positions
    4. len(portfolio.positions) < config["paper_trading"]["max_positions"]
    5. portfolio.cash > 0
    """
    config_pt = portfolio.config.get("paper_trading", {})
    min_score = config_pt.get("min_score_to_trade", 70)
    max_positions = config_pt.get("max_positions", 10)
    slippage_pct = config_pt.get("slippage_pct", 0.15) / 100.0
    risk_per_trade_pct = config_pt.get("risk_per_trade_pct", 2.0)

    # --- Pre-condition checks ---
    if result.stage != 2:
        log.debug("enter_trade: %s stage=%d ≠ 2 — skip", result.symbol, result.stage)
        return None
    if result.score < min_score:
        log.debug(
            "enter_trade: %s score=%d < min_score=%d — skip",
            result.symbol,
            result.score,
            min_score,
        )
        return None
    if result.symbol in portfolio.positions:
        log.debug("enter_trade: %s already in portfolio — skip", result.symbol)
        return None
    if len(portfolio.positions) >= max_positions:
        log.debug(
            "enter_trade: max_positions=%d reached — skip for %s",
            max_positions,
            result.symbol,
        )
        return None
    if portfolio.cash <= 0:
        log.debug("enter_trade: no cash — skip %s", result.symbol)
        return None

    # --- Position sizing ---
    # Use {symbol: current_price}; other open positions fall back to
    # entry_price inside get_total_value.
    total_value = portfolio.get_total_value({result.symbol: current_price})
    risk_amount = total_value * (risk_per_trade_pct / 100.0)

    stop_loss = result.stop_loss if result.stop_loss is not None else current_price * 0.93
    risk_per_share = current_price - stop_loss
    if risk_per_share <= 0:
        log.warning(
            "enter_trade: %s risk_per_share=%.4f ≤ 0, using 7 %% fallback",
            result.symbol,
            risk_per_share,
        )
        risk_per_share = current_price * 0.07

    fill_price = current_price * (1.0 + slippage_pct)
    quantity = max(1, int(risk_amount / risk_per_share))

    # Cap at available cash — belt-and-suspenders (Portfolio.add_position also guards)
    if fill_price * quantity > portfolio.cash:
        quantity = max(1, int(portfolio.cash / fill_price))

    # --- Market hours gate ---
    if is_trading_day(run_date) and _is_market_hours():
        position = Position(
            symbol=result.symbol,
            entry_date=run_date,
            entry_price=fill_price,
            quantity=quantity,
            stop_loss=stop_loss,
            target_price=result.target_price,
            sepa_score=result.score,
            setup_quality=result.setup_quality,
        )
        portfolio.add_position(position)
        log.info(
            "enter_trade: FILLED %s qty=%d @ %.2f  stop=%.2f  quality=%s",
            result.symbol,
            quantity,
            fill_price,
            stop_loss,
            result.setup_quality,
        )
        return position

    # --- Queue for next open ---
    result_dict = {
        "symbol": result.symbol,
        "score": result.score,
        "setup_quality": result.setup_quality,
        "stop_loss": stop_loss,
        "target_price": result.target_price,
        "entry_price": result.entry_price,
    }
    queue_order(result.symbol, "BUY", result_dict)
    log.info(
        "enter_trade: QUEUED BUY %s (non-trading day or outside market hours)",
        result.symbol,
    )
    return None


def pyramid_position(
    result: SEPAResult,
    portfolio: Portfolio,
    current_price: float,
    run_date: date,
) -> Position | None:
    """Add shares to an existing winning position (one pyramid per position).

    Conditions (ALL must be satisfied; returns None silently otherwise)
    ------------------------------------------------------------------
    1. symbol is in portfolio.positions
    2. position.pyramided is False  (max one pyramid ever)
    3. result.setup_quality == "A" or "A+"
    4. result.vcp_qualified is True
    5. result.vcp_details["vol_ratio"] < 0.4  (strong volume dry-up)
    6. current_price is within [entry_price, entry_price × 1.02]
       where entry_price is result.entry_price (the VCP pivot)

    Add quantity = max(1, int(original_quantity × 0.5)).
    Sets position.pyramided = True to prevent a second add.
    """
    symbol = result.symbol

    if symbol not in portfolio.positions:
        return None

    pos = portfolio.positions[symbol]

    if pos.pyramided:
        log.debug("pyramid_position: %s already pyramided — skip", symbol)
        return None

    if result.setup_quality not in ("A", "A+"):
        log.debug(
            "pyramid_position: %s setup_quality=%s not A/A+ — skip",
            symbol,
            result.setup_quality,
        )
        return None

    if not result.vcp_qualified:
        log.debug("pyramid_position: %s vcp_qualified=False — skip", symbol)
        return None

    vol_ratio = float(result.vcp_details.get("vol_ratio", 1.0))
    if vol_ratio >= 0.4:
        log.debug(
            "pyramid_position: %s vol_ratio=%.3f ≥ 0.4 — skip", symbol, vol_ratio
        )
        return None

    pivot = result.entry_price
    if pivot is None:
        log.debug("pyramid_position: %s result.entry_price is None — skip", symbol)
        return None
    if not (pivot <= current_price <= pivot * 1.02):
        log.debug(
            "pyramid_position: %s price %.2f outside [%.2f, %.2f] — skip",
            symbol,
            current_price,
            pivot,
            pivot * 1.02,
        )
        return None

    # --- Sizing: 50 % of original quantity ---
    config_pt = portfolio.config.get("paper_trading", {})
    slippage_pct = config_pt.get("slippage_pct", 0.15) / 100.0
    fill_price = current_price * (1.0 + slippage_pct)

    add_qty = max(1, int(pos.quantity * 0.5))
    cost = fill_price * add_qty
    if cost > portfolio.cash:
        add_qty = max(1, int(portfolio.cash / fill_price))
        if fill_price * add_qty > portfolio.cash:
            log.warning("pyramid_position: %s insufficient cash — skip", symbol)
            return None

    portfolio.cash -= fill_price * add_qty
    pos.pyramided = True
    pos.pyramid_qty += add_qty

    log.info(
        "pyramid_position: %s +%d @ %.2f  total_qty=%d  cash=%.2f",
        symbol,
        add_qty,
        fill_price,
        pos.quantity + pos.pyramid_qty,
        portfolio.cash,
    )
    return pos


def check_exits(
    portfolio: Portfolio,
    current_prices: dict[str, float],
    run_date: date,
) -> list[ClosedTrade]:
    """Check every open position for stop-loss or target-price hits.

    Returns the list of ClosedTrade objects generated in this check.
    Positions are closed at current_prices[symbol] (no extra slippage
    applied here; use the fill from the intraday price bar).
    """
    closed: list[ClosedTrade] = []
    # Snapshot keys so we can mutate portfolio.positions inside the loop
    for symbol in list(portfolio.positions.keys()):
        pos = portfolio.positions.get(symbol)
        if pos is None:
            continue
        price = current_prices.get(symbol)
        if price is None:
            continue

        if price <= pos.stop_loss:
            trade = portfolio.close_position(symbol, price, "stop_loss", run_date)
            closed.append(trade)
            log.info(
                "check_exits: STOP LOSS %s @ %.2f (stop=%.2f)",
                symbol,
                price,
                pos.stop_loss,
            )
        elif pos.target_price is not None and price >= pos.target_price:
            trade = portfolio.close_position(symbol, price, "target", run_date)
            closed.append(trade)
            log.info(
                "check_exits: TARGET HIT %s @ %.2f (target=%.2f)",
                symbol,
                price,
                pos.target_price,
            )

    return closed


def reset_portfolio(confirm: bool = False) -> None:
    """Reset all paper-trading state to a clean slate.

    Deletes portfolio.json, trades.json, and pending_orders.json from
    data/paper_trading/.  Called by the ``make paper-reset`` target.

    Requires ``confirm=True`` — aborts silently otherwise to prevent
    accidental resets.
    """
    if not confirm:
        log.warning("reset_portfolio: called without confirm=True — aborted")
        return

    for path in (_PORTFOLIO_FILE, _TRADES_FILE, Path(ORDERS_FILE)):
        if path.exists():
            path.unlink()
            log.info("reset_portfolio: deleted %s", path)
        else:
            log.debug("reset_portfolio: %s not found — skipping", path)

    log.info("reset_portfolio: paper-trading state cleared")
