"""
ingestion/__init__.py
---------------------
Public surface of the ingestion package.
"""

from ingestion.base import DataSource
from ingestion.nsepython_universe import get_nifty500, get_nse_all, get_universe
from ingestion.universe_loader import (
    RunSymbols,
    load_watchlist_file,
    resolve_symbols,
    validate_symbol,
)

__all__ = [
    # Abstract base
    "DataSource",
    # Universe helpers
    "get_nifty500",
    "get_nse_all",
    "get_universe",
    # Symbol resolution
    "RunSymbols",
    "load_watchlist_file",
    "resolve_symbols",
    "validate_symbol",
]
