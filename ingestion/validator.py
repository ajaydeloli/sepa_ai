"""
ingestion/validator.py
-----------------------
OHLCV DataFrame validation and cleaning for the SEPA pipeline.

Public surface
--------------
:func:`validate`
    The single entry-point.  Call it immediately after fetching data from
    any :class:`~ingestion.base.DataSource` adapter before passing the
    DataFrame further into the pipeline.

Validation steps (in order)
----------------------------
1. **Schema check** — ensures ``open``, ``high``, ``low``, ``close``,
   ``volume`` columns all exist.
2. **Index sort** — sorts the DatetimeIndex ascending.
3. **Sanity rows** — drops rows where any of:
   * ``high < low``
   * ``close`` outside ``[low, high]``
   * ``volume <= 0``
   * any price column ``<= 0``
   If more than 5 % of rows are dropped, raises
   :exc:`~utils.exceptions.DataValidationError`.
4. **Gap detection** — compares the index against the NSE trading calendar
   and logs one warning per missing trading day.
5. **Minimum length** — raises
   :exc:`~utils.exceptions.InsufficientDataError` if fewer than 50 rows
   remain after cleaning.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from utils.exceptions import DataValidationError, InsufficientDataError
from utils.logger import get_logger
from utils.trading_calendar import trading_days

log = get_logger(__name__)

_MIN_ROWS = 50
_MAX_DROP_PCT = 0.05
_REQUIRED_COLS = ["open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate(
    df: pd.DataFrame,
    symbol: str,
    run_date: date | None = None,
    min_rows: int = _MIN_ROWS,
) -> pd.DataFrame:
    """Validate and clean an OHLCV DataFrame.

    Parameters
    ----------
    df:
        Raw OHLCV DataFrame as returned by any
        :class:`~ingestion.base.DataSource` adapter.  Expected columns:
        ``open``, ``high``, ``low``, ``close``, ``volume`` (lowercase).
        Index should be a :class:`pandas.DatetimeIndex`.
    symbol:
        Ticker symbol, used only for log/error messages.
    run_date:
        Optional reference date for gap detection.  When *None* the last
        date in the DataFrame is used as the upper bound.
    min_rows:
        Minimum number of rows required after cleaning.  Defaults to
        ``_MIN_ROWS`` (50) for full historical loads.  Pass a smaller
        value (e.g. ``1``) for incremental daily fetches where only the
        most recent rows are needed.

    Returns
    -------
    pd.DataFrame
        Cleaned DataFrame with a sorted DatetimeIndex.

    Raises
    ------
    DataValidationError
        * Missing required columns.
        * More than 5 % of rows fail sanity checks.
    InsufficientDataError
        Fewer than *min_rows* rows after cleaning.
    """
    # ── 1. Schema check ────────────────────────────────────────────────────
    _check_schema(df, symbol)

    # ── 2. Ensure DatetimeIndex and sort ascending ─────────────────────────
    df = _ensure_datetime_index(df, symbol)
    df = df.sort_index()

    # ── 3. Sanity-check rows ───────────────────────────────────────────────
    df = _drop_bad_rows(df, symbol)

    # ── 4. Gap detection ───────────────────────────────────────────────────
    _detect_gaps(df, symbol)

    # ── 5. Minimum length ─────────────────────────────────────────────────
    if len(df) < min_rows:
        raise InsufficientDataError(
            f"{symbol}: insufficient data after validation",
            required=min_rows,
            available=len(df),
        )

    return df


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_schema(df: pd.DataFrame, symbol: str) -> None:
    """Raise DataValidationError if any required column is missing."""
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise DataValidationError(
            f"{symbol}: missing required column(s): {missing}",
            detail=f"present columns: {list(df.columns)}",
        )


def _ensure_datetime_index(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Convert index to DatetimeIndex (tz-naive) if not already."""
    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df = df.copy()
            df.index = pd.to_datetime(df.index)
        except Exception as exc:
            raise DataValidationError(
                f"{symbol}: cannot convert index to DatetimeIndex",
                detail=str(exc),
            ) from exc
    # Strip timezone
    if df.index.tz is not None:
        df = df.copy()
        df.index = df.index.tz_localize(None)
    return df


def _drop_bad_rows(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Drop rows that fail OHLCV sanity checks.

    Bad row conditions (any one is sufficient to drop the row):
    * high < low
    * close < low or close > high
    * volume <= 0
    * any price column (open/high/low/close) <= 0

    If more than 5 % of rows are dropped, raises DataValidationError.
    """
    total = len(df)
    if total == 0:
        return df

    price_cols = ["open", "high", "low", "close"]

    # Build a boolean mask: True = KEEP
    mask_prices_positive = (df[price_cols] > 0).all(axis=1)
    mask_high_ge_low = df["high"] >= df["low"]
    mask_close_in_range = (df["close"] >= df["low"]) & (df["close"] <= df["high"])
    mask_volume_positive = df["volume"] > 0

    keep_mask = (
        mask_prices_positive
        & mask_high_ge_low
        & mask_close_in_range
        & mask_volume_positive
    )

    bad_indices = df.index[~keep_mask]
    n_bad = len(bad_indices)

    # Log individual bad rows (up to a reasonable limit to avoid log spam)
    for idx in bad_indices:
        row = df.loc[idx]
        log.warning(
            "%s: dropping bad row at %s — open=%.4f high=%.4f low=%.4f "
            "close=%.4f volume=%.0f",
            symbol,
            idx.date() if hasattr(idx, "date") else idx,
            row.get("open", float("nan")),
            row.get("high", float("nan")),
            row.get("low", float("nan")),
            row.get("close", float("nan")),
            row.get("volume", float("nan")),
        )

    if n_bad / total > _MAX_DROP_PCT:
        raise DataValidationError(
            f"{symbol}: {n_bad}/{total} rows ({n_bad/total:.1%}) failed sanity checks "
            f"— exceeds 5 % threshold",
            detail="too many rows with high < low, close out of range, or volume <= 0",
        )

    return df[keep_mask].copy()


def _detect_gaps(df: pd.DataFrame, symbol: str) -> None:
    """Log a warning for each trading day that is missing from the DataFrame."""
    if df.empty:
        return

    start_date = df.index.min().date()
    end_date = df.index.max().date()

    try:
        expected = trading_days(start_date.isoformat(), end_date.isoformat())
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "%s: could not compute expected trading days for gap check (%s).", symbol, exc
        )
        return

    # Normalise DataFrame dates to date-only for comparison
    actual_dates = set(ts.date() for ts in df.index)
    expected_dates = set(ts.date() for ts in expected)

    missing = sorted(expected_dates - actual_dates)
    for d in missing:
        log.warning("%s: missing trading day %s.", symbol, d)
