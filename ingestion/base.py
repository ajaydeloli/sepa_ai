"""
ingestion/base.py
-----------------
Abstract base class that every data-source adapter must implement.

Any concrete adapter (yfinance, nsepython, flat-file, etc.) subclasses
:class:`DataSource` and fills in the three methods so the pipeline can
stay source-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataSource(ABC):
    """Contract for OHLCV data providers used by the SEPA pipeline."""

    # ------------------------------------------------------------------
    # Single-symbol fetch
    # ------------------------------------------------------------------

    @abstractmethod
    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Return an OHLCV DataFrame for *symbol* over [start, end].

        Parameters
        ----------
        symbol:
            Exchange ticker (e.g. ``"RELIANCE"`` for NSE).
        start:
            First date to include (inclusive).
        end:
            Last date to include (inclusive).

        Returns
        -------
        pd.DataFrame
            * Index  : :class:`pandas.DatetimeIndex`, one row per trading day.
            * Columns: ``open``, ``high``, ``low``, ``close``, ``volume``
              (all lowercase, numeric dtypes).
            * Empty DataFrame (zero rows) is acceptable when no data exists
              for the requested range — callers must guard against this.
        """
        ...

    # ------------------------------------------------------------------
    # Batch fetch
    # ------------------------------------------------------------------

    @abstractmethod
    def fetch_universe_batch(
        self,
        symbols: list[str],
        period: str = "5d",
    ) -> dict[str, pd.DataFrame]:
        """Fetch recent OHLCV data for a batch of *symbols* efficiently.

        Implementations should use a single HTTP call where the upstream
        API supports it (e.g. yfinance multi-ticker download).

        Parameters
        ----------
        symbols:
            List of exchange tickers to fetch.
        period:
            Look-back window accepted by the underlying API,
            e.g. ``"5d"``, ``"1mo"``, ``"1y"``.

        Returns
        -------
        dict[str, pd.DataFrame]
            Mapping of *symbol* → OHLCV DataFrame (same schema as
            :meth:`fetch`).  Symbols for which data could not be
            retrieved are omitted (not mapped to empty DataFrames).
        """
        ...

    # ------------------------------------------------------------------
    # Universe discovery
    # ------------------------------------------------------------------

    @abstractmethod
    def fetch_universe(self) -> list[str]:
        """Return all tradable symbols available from this data source.

        Returns
        -------
        list[str]
            Uppercase ticker symbols.  May be empty if the source is
            unavailable; callers should handle the empty-list case.
        """
        ...
