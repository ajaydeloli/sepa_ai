"""api/schemas — Pydantic schema layer for the SEPA AI FastAPI service."""

from api.schemas.common import APIResponse, ErrorResponse, PaginationMeta
from api.schemas.stock import (
    StockHistorySchema,
    StockResultSchema,
    TrendTemplateSchema,
    VCPSchema,
)
from api.schemas.portfolio import (
    PortfolioSummarySchema,
    PositionSchema,
    SummaryPositionSchema,
    TradeSchema,
)

__all__ = [
    # common
    "APIResponse",
    "ErrorResponse",
    "PaginationMeta",
    # stock
    "TrendTemplateSchema",
    "VCPSchema",
    "StockResultSchema",
    "StockHistorySchema",
    # portfolio
    "PositionSchema",
    "SummaryPositionSchema",
    "TradeSchema",
    "PortfolioSummarySchema",
]
