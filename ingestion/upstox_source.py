"""
ingestion/upstox_source.py
---------------------------
FALLBACK 2 — Upstox API data source (stub).

This module defines :class:`UpstoxSource` which will connect to the
Upstox v2 REST API to provide live and historical OHLCV data for NSE
instruments.

Current status
--------------
All data-fetch methods are **stubs** that raise :exc:`NotImplementedError`.
Full implementation is deferred to a future phase.  The
:func:`~ingestion.source_factory.get_source` factory falls back to
:class:`~ingestion.yfinance_source.YFinanceSource` whenever
:class:`UpstoxSource` cannot be initialised (missing credentials).

Required environment variables
-------------------------------
``UPSTOX_API_KEY``
    The API key issued by Upstox for your registered application.
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd

from ingestion.base import DataSource
from utils.exceptions import ConfigurationError
from utils.logger import get_logger

log = get_logger(__name__)


class UpstoxSource(DataSource):
    """Upstox API data source — stub implementation.

    Raises
    ------
    ConfigurationError
        Immediately in ``__init__`` if ``UPSTOX_API_KEY`` environment
        variable is absent.
    """

    def __init__(self) -> None:
        api_key = os.environ.get("UPSTOX_API_KEY", "").strip()

        if not api_key:
            raise ConfigurationError(
                "Upstox source requires environment variable: UPSTOX_API_KEY",
                detail="Set the missing variable and restart the application.",
            )

        self._api_key = api_key
        log.info("UpstoxSource initialised.")

    # ------------------------------------------------------------------
    # DataSource interface — stubs
    # ------------------------------------------------------------------

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Not yet implemented — use yfinance source instead."""
        raise NotImplementedError("Upstox source not yet implemented — use yfinance")

    def fetch_universe_batch(
        self,
        symbols: list[str],
        period: str = "5d",
    ) -> dict[str, pd.DataFrame]:
        """Not yet implemented — use yfinance source instead."""
        raise NotImplementedError("Upstox source not yet implemented — use yfinance")

    def fetch_universe(self) -> list[str]:
        """Not yet implemented — use yfinance source instead."""
        raise NotImplementedError("Upstox source not yet implemented — use yfinance")
