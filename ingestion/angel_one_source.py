"""
ingestion/angel_one_source.py
------------------------------
FALLBACK 1 — Angel One SmartAPI data source (stub).

This module defines :class:`AngelOneSource` which will connect to the
Angel One SmartAPI to provide live and historical OHLCV data for NSE
instruments.  Authentication uses an API key and client ID that must be
supplied via environment variables.

Current status
--------------
All data-fetch methods are **stubs** that raise :exc:`NotImplementedError`.
Full implementation is planned for a future phase once the broker
integration sprint is scheduled.  Until then, the
:func:`~ingestion.source_factory.get_source` factory automatically falls
back to :class:`~ingestion.yfinance_source.YFinanceSource` whenever
:class:`AngelOneSource` cannot be initialised (missing credentials).

Required environment variables
-------------------------------
``ANGEL_ONE_API_KEY``
    The API key issued by Angel One for your SmartAPI application.
``ANGEL_ONE_CLIENT_ID``
    The Angel One client/user ID associated with the API key.
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd

from ingestion.base import DataSource
from utils.exceptions import ConfigurationError
from utils.logger import get_logger

log = get_logger(__name__)


class AngelOneSource(DataSource):
    """Angel One SmartAPI data source — stub implementation.

    Raises
    ------
    ConfigurationError
        Immediately in ``__init__`` if ``ANGEL_ONE_API_KEY`` or
        ``ANGEL_ONE_CLIENT_ID`` environment variables are absent.
    """

    def __init__(self) -> None:
        api_key = os.environ.get("ANGEL_ONE_API_KEY", "").strip()
        client_id = os.environ.get("ANGEL_ONE_CLIENT_ID", "").strip()

        missing = []
        if not api_key:
            missing.append("ANGEL_ONE_API_KEY")
        if not client_id:
            missing.append("ANGEL_ONE_CLIENT_ID")

        if missing:
            raise ConfigurationError(
                f"Angel One source requires environment variable(s): {', '.join(missing)}",
                detail="Set the missing variables and restart the application.",
            )

        self._api_key = api_key
        self._client_id = client_id
        log.info("AngelOneSource initialised (client_id=%s).", client_id)

    # ------------------------------------------------------------------
    # DataSource interface — stubs
    # ------------------------------------------------------------------

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Not yet implemented — use yfinance source instead."""
        raise NotImplementedError("Angel One source not yet implemented — use yfinance")

    def fetch_universe_batch(
        self,
        symbols: list[str],
        period: str = "5d",
    ) -> dict[str, pd.DataFrame]:
        """Not yet implemented — use yfinance source instead."""
        raise NotImplementedError("Angel One source not yet implemented — use yfinance")

    def fetch_universe(self) -> list[str]:
        """Not yet implemented — use yfinance source instead."""
        raise NotImplementedError("Angel One source not yet implemented — use yfinance")
