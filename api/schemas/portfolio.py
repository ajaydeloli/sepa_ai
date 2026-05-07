"""
api/schemas/portfolio.py
------------------------
Pydantic schemas for the paper-trading portfolio layer.

  PositionSchema         — full position detail (for dedicated position endpoints)
  SummaryPositionSchema  — slim position view returned by Portfolio.get_summary()
  TradeSchema            — closed trade record
  PortfolioSummarySchema — complete portfolio summary (maps to get_summary() output)

Mapping notes — PositionSchema vs SummaryPositionSchema
---------------------------------------------------------
Portfolio.get_summary() returns a "slim" position dict per open trade:
    symbol, entry_price, current_price, unrealised_pnl_pct,
    days_held, stop_loss, trailing_stop, quality          ← "quality", not "setup_quality"

PositionSchema mirrors the full Position dataclass and is used by endpoints
that list raw open positions.  PortfolioSummarySchema.positions uses
SummaryPositionSchema so that get_summary() output validates without
post-processing.

Both schemas accept ISO-8601 date strings for all date fields.  Validators
coerce datetime.date objects automatically so dataclasses.asdict() output
round-trips cleanly.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Individual position — full detail
# ---------------------------------------------------------------------------


class PositionSchema(BaseModel):
    """Full open-position record mirroring the Position dataclass."""

    symbol: str
    entry_date: str
    entry_price: float
    quantity: int
    stop_loss: float
    trailing_stop: float
    target_price: float | None = None
    days_held: int
    unrealised_pnl: float
    unrealised_pnl_pct: float
    setup_quality: str

    @field_validator("entry_date", mode="before")
    @classmethod
    def coerce_date(cls, v: object) -> str:
        if hasattr(v, "isoformat"):
            return v.isoformat()  # type: ignore[union-attr]
        return str(v)


# ---------------------------------------------------------------------------
# Slim position view — matches Portfolio.get_summary() positions list
# ---------------------------------------------------------------------------


class SummaryPositionSchema(BaseModel):
    """Slim position view returned inside Portfolio.get_summary()['positions'].

    Key differences from PositionSchema:
      • current_price instead of entry_date / quantity / unrealised_pnl
      • quality  (not setup_quality) — matches get_summary() key naming
    """

    symbol: str
    entry_price: float
    current_price: float
    unrealised_pnl_pct: float
    days_held: int
    stop_loss: float
    trailing_stop: float
    quality: str            # maps to Position.setup_quality in get_summary()


# ---------------------------------------------------------------------------
# Closed trade
# ---------------------------------------------------------------------------


class TradeSchema(BaseModel):
    """Closed trade record mirroring the ClosedTrade dataclass."""

    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    r_multiple: float
    exit_reason: str
    setup_quality: str

    @field_validator("entry_date", "exit_date", mode="before")
    @classmethod
    def coerce_dates(cls, v: object) -> str:
        if hasattr(v, "isoformat"):
            return v.isoformat()  # type: ignore[union-attr]
        return str(v)


# ---------------------------------------------------------------------------
# Full portfolio summary
# ---------------------------------------------------------------------------


class PortfolioSummarySchema(BaseModel):
    """Complete portfolio summary; maps to Portfolio.get_summary() output.

    Fields beyond the prompt baseline
    ----------------------------------
    best_trade_pct  — best single-trade return (pct); from get_summary()
    worst_trade_pct — worst single-trade return (pct); from get_summary()
    avg_hold_days   — average holding period in calendar days; from get_summary()

    positions uses SummaryPositionSchema which matches the slim dict format
    that get_summary() produces (current_price / quality keys).
    """

    cash: float
    open_value: float
    total_value: float
    initial_capital: float
    total_return_pct: float
    realised_pnl: float
    unrealised_pnl: float
    win_rate: float                     # fraction 0–1
    total_trades: int
    open_count: int
    closed_count: int
    profit_factor: float
    avg_r_multiple: float
    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0
    avg_hold_days: float = 0.0
    positions: list[SummaryPositionSchema]
