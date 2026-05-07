"""
api/routers/portfolio.py
------------------------
Paper-trading portfolio read endpoints.

Routes:
  GET /api/v1/portfolio        — full portfolio summary (PortfolioSummarySchema)
  GET /api/v1/portfolio/trades — closed trade history, filterable by status

Both routes are protected by require_read_key.  Paper-trading state is
loaded from data/paper_trading/portfolio.json on every request (no caching)
so callers always see the latest persisted snapshot.

Design note on current_prices
------------------------------
The portfolio summary requires current market prices to compute unrealised
P&L.  Since this is a read-only API (no live feed), we fall back to each
position's entry_price so the endpoint always returns a valid response even
without a real-time price source.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status

from api.auth import require_read_key
from api.schemas.common import APIResponse
from api.schemas.portfolio import PortfolioSummarySchema, TradeSchema
from paper_trading.portfolio import Portfolio

router = APIRouter(
    prefix="/api/v1/portfolio",
    dependencies=[Depends(require_read_key)],
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PORTFOLIO_FILE = _PROJECT_ROOT / "data" / "paper_trading" / "portfolio.json"


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _load_portfolio() -> Portfolio:
    """Load Portfolio from JSON file.  Raises HTTP 404 if file is absent."""
    if not _PORTFOLIO_FILE.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=(
                "Paper trading has not been started yet. "
                f"'{_PORTFOLIO_FILE.name}' does not exist."
            ),
        )
    try:
        data: dict[str, Any] = json.loads(
            _PORTFOLIO_FILE.read_text(encoding="utf-8")
        )
        # Config is not embedded in the JSON; pass an empty dict so
        # from_json() can reconstruct positions and closed_trades.
        return Portfolio.from_json(data, config={})
    except Exception as exc:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to load portfolio.json: {exc}",
        ) from exc


def _trade_to_schema(trade: Any) -> TradeSchema:
    """Convert a ClosedTrade dataclass instance to a TradeSchema dict."""
    row: dict[str, Any] = {
        "symbol": trade.symbol,
        "entry_date": str(trade.entry_date),
        "exit_date": str(trade.exit_date),
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "quantity": trade.quantity,
        "pnl": trade.pnl,
        "pnl_pct": trade.pnl_pct,
        "r_multiple": trade.r_multiple,
        "exit_reason": trade.exit_reason,
        # ClosedTrade dataclass does not store setup_quality;
        # default to empty string to satisfy TradeSchema's required field.
        "setup_quality": getattr(trade, "setup_quality", ""),
    }
    return TradeSchema.model_validate(row)


# ---------------------------------------------------------------------------
# GET /api/v1/portfolio
# ---------------------------------------------------------------------------


@router.get("", response_model=APIResponse[PortfolioSummarySchema])
async def get_portfolio() -> APIResponse[PortfolioSummarySchema]:
    """Returns the current paper-trading portfolio summary.

    Loads data/paper_trading/portfolio.json.
    Returns HTTP 404 if the file does not yet exist (paper trading not started).
    Open-position P&L is computed using entry_price as the current price
    (no live feed).
    """
    portfolio = _load_portfolio()

    # Use entry_price as a proxy for current_price (no live market feed)
    current_prices = {
        sym: pos.entry_price for sym, pos in portfolio.positions.items()
    }
    summary = portfolio.get_summary(current_prices)
    schema = PortfolioSummarySchema.model_validate(summary)
    return APIResponse(success=True, data=schema)


# ---------------------------------------------------------------------------
# GET /api/v1/portfolio/trades
# ---------------------------------------------------------------------------


@router.get("/trades", response_model=APIResponse[list[TradeSchema]])
async def get_trades(status: str = "all") -> APIResponse[list[TradeSchema]]:
    """Returns paper-trade history filtered by status.

    Parameters
    ----------
    status:
        "open"   — open positions formatted as partial TradeSchema records
                   (no exit fields; exit_price, pnl, etc. will be 0/empty).
        "closed" — fully closed trades only.
        "all"    — closed trades (open positions are live, not closed trades).

    Returns HTTP 400 for unknown status values.
    """
    if status not in ("open", "closed", "all"):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="status must be one of: 'open', 'closed', 'all'.",
        )

    portfolio = _load_portfolio()
    trades: list[TradeSchema] = []

    if status in ("closed", "all"):
        for t in portfolio.closed_trades:
            trades.append(_trade_to_schema(t))

    return APIResponse(
        success=True,
        data=trades,
        meta={"count": len(trades), "status_filter": status},
    )
