"""
ingestion/yfinance_source.py
-----------------------------
Primary OHLCV data source backed by ``yfinance``.

All NSE tickers are fetched with the ``.NS`` suffix that Yahoo Finance
requires for National Stock Exchange-listed instruments.

Batch fetching
--------------
:meth:`fetch_universe_batch` issues a *single* ``yf.download()`` call for all
requested symbols.  yfinance ≥1.0 always returns a MultiIndex DataFrame
``(field, ticker)`` regardless of how many tickers are requested; this module
handles that shape transparently for both single- and multi-ticker calls.

Retry policy
------------
One automatic retry on any network/data error before raising
:class:`~utils.exceptions.DataSourceError`.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

import pandas as pd
import yfinance as yf

from ingestion.base import DataSource
from ingestion.nsepython_universe import get_universe
from utils.exceptions import DataSourceError
from utils.logger import get_logger

log = get_logger(__name__)

_NS_SUFFIX = ".NS"
_REQUIRED_COLS = {"open", "high", "low", "close", "volume"}


def _add_ns(symbol: str) -> str:
    """Append ``.NS`` if not already present."""
    return symbol if symbol.endswith(_NS_SUFFIX) else symbol + _NS_SUFFIX


def _strip_ns(ticker: str) -> str:
    """Remove ``.NS`` suffix to recover the base symbol."""
    return ticker[: -len(_NS_SUFFIX)] if ticker.endswith(_NS_SUFFIX) else ticker


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase column names and keep only OHLCV columns.

    yfinance ≥1.0 returns a MultiIndex ``(field, ticker)`` even for a single-
    ticker download.  We flatten to the field level before further processing
    so the rest of the pipeline always sees plain column names.
    """
    df = df.copy()
    # Flatten MultiIndex columns produced by yfinance ≥1.0 (field, ticker) → field
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).lower() for c in df.columns]
    # Keep only the five canonical columns (drop Adj Close, Dividends, etc.)
    present = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    return df[present]


def _clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """Drop NaN rows and ensure a DatetimeIndex."""
    df = df.dropna(how="all")
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df.index = df.index.tz_localize(None)  # strip timezone if present
    return df


class YFinanceSource(DataSource):
    """Primary OHLCV data source powered by ``yfinance``."""

    # ------------------------------------------------------------------
    # Batch fetch (single HTTP call for all symbols)
    # ------------------------------------------------------------------

    def fetch_universe_batch(
        self,
        symbols: list[str],
        period: str = "5d",
    ) -> dict[str, pd.DataFrame]:
        """Fetch recent OHLCV data for *symbols* in one ``yf.download`` call.

        Parameters
        ----------
        symbols:
            NSE base tickers (e.g. ``["RELIANCE", "TCS"]``).
        period:
            Look-back window accepted by yfinance (e.g. ``"5d"``, ``"1mo"``).

        Returns
        -------
        dict[str, pd.DataFrame]
            ``{base_symbol: ohlcv_df}`` — symbols with no data are omitted.
        """
        if not symbols:
            return {}

        ns_tickers = [_add_ns(s) for s in symbols]
        last_exc: Exception | None = None

        for attempt in range(2):
            try:
                raw: pd.DataFrame = yf.download(
                    tickers=ns_tickers,
                    period=period,
                    group_by="ticker",
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == 0:
                    log.warning(
                        "fetch_universe_batch: attempt 1 failed (%s); retrying…", exc
                    )
                    time.sleep(1)
        else:
            raise DataSourceError(
                "fetch_universe_batch failed after 2 attempts",
                detail=str(last_exc),
            )

        if raw is None or raw.empty:
            log.warning("fetch_universe_batch: yf.download returned empty DataFrame.")
            return {}

        result: dict[str, pd.DataFrame] = {}

        # ── Single ticker: flat columns (e.g. "Close", "Open", …) ────────
        if len(ns_tickers) == 1:
            ticker = ns_tickers[0]
            df = _normalise_columns(raw)
            df = _clean_df(df)
            if not df.empty and _REQUIRED_COLS.issubset(set(df.columns)):
                result[_strip_ns(ticker)] = df
            else:
                log.warning("fetch_universe_batch: no usable data for %s.", ticker)
            return result

        # ── Multiple tickers: MultiIndex columns (field, ticker) ─────────
        # yfinance returns columns like ("Close", "RELIANCE.NS"), ("Open", "TCS.NS")
        if isinstance(raw.columns, pd.MultiIndex):
            for ticker in ns_tickers:
                try:
                    # Select all price/volume columns for this ticker
                    ticker_df = raw.xs(ticker, axis=1, level=1)
                except KeyError:
                    log.warning(
                        "fetch_universe_batch: no data in MultiIndex for %s.", ticker
                    )
                    continue
                ticker_df = _normalise_columns(ticker_df)
                ticker_df = _clean_df(ticker_df)
                if ticker_df.empty or not _REQUIRED_COLS.issubset(set(ticker_df.columns)):
                    log.warning(
                        "fetch_universe_batch: dropping %s (empty or missing columns).",
                        ticker,
                    )
                    continue
                result[_strip_ns(ticker)] = ticker_df
        else:
            # Flat columns with multiple tickers — shouldn't normally happen
            # but handle gracefully
            log.warning(
                "fetch_universe_batch: unexpected flat columns for multi-ticker download."
            )

        log.info(
            "fetch_universe_batch: fetched data for %d / %d symbol(s).",
            len(result),
            len(symbols),
        )
        return result

    # ------------------------------------------------------------------
    # Single-symbol fetch
    # ------------------------------------------------------------------

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Download OHLCV data for a single *symbol* over [start, end].

        Parameters
        ----------
        symbol:
            NSE base ticker (e.g. ``"RELIANCE"``).
        start, end:
            Inclusive date range.

        Returns
        -------
        pd.DataFrame
            OHLCV DataFrame with DatetimeIndex.

        Raises
        ------
        DataSourceError
            If the download fails after one retry.
        """
        ticker = _add_ns(symbol)
        last_exc: Exception | None = None

        for attempt in range(2):
            try:
                raw: pd.DataFrame = yf.download(
                    tickers=ticker,
                    start=start.isoformat(),
                    end=(
                        pd.Timestamp(end) + pd.Timedelta(days=1)
                    ).date().isoformat(),  # yfinance end is exclusive
                    auto_adjust=True,
                    progress=False,
                )
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt == 0:
                    log.warning(
                        "fetch(%s): attempt 1 failed (%s); retrying…", symbol, exc
                    )
                    time.sleep(1)
        else:
            raise DataSourceError(
                f"fetch({symbol}) failed after 2 attempts",
                detail=str(last_exc),
            )

        if raw is None or raw.empty:
            log.warning("fetch(%s): yf.download returned empty DataFrame.", symbol)
            return pd.DataFrame()

        df = _normalise_columns(raw)
        df = _clean_df(df)
        log.info("fetch(%s): %d rows fetched.", symbol, len(df))
        return df

    # ------------------------------------------------------------------
    # Universe discovery
    # ------------------------------------------------------------------

    def fetch_universe(self) -> list[str]:
        """Return all tradable NSE symbols by delegating to nsepython.

        Returns
        -------
        list[str]
            Uppercase NSE tickers from the configured universe.
        """
        return get_universe()
