"""
features/atr.py
---------------
Average True Range (ATR) feature module for the Minervini SEPA screener.

Implements the ``compute`` interface contract:
  - Pure function: no I/O, no side effects, no global state.
  - Appends indicator columns to the input DataFrame and returns it.
  - Raises InsufficientDataError when the DataFrame is too short.

New columns appended
--------------------
atr_14  : Average True Range over ``period`` bars (Wilder's EWM smoothing,
          alpha=1/period, adjust=False).
atr_pct : atr_14 / close * 100 — ATR expressed as a percentage of closing price.
"""

from __future__ import annotations

import pandas as pd

from utils.exceptions import InsufficientDataError
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REQUIRED_ROWS: int = 20          # period (14) + 6-row safety buffer
_DEFAULT_PERIOD: int = 14


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Append ATR indicator columns to *df* and return it.

    Parameters
    ----------
    df:
        Cleaned OHLCV DataFrame with a DatetimeIndex and columns:
        ``open``, ``high``, ``low``, ``close``, ``volume``.
    config:
        Screening configuration dict.  Relevant keys::

            config["atr"]["period"]   (default 14)

    Returns
    -------
    pd.DataFrame
        The same DataFrame with two new float64 columns appended:
        ``atr_14`` and ``atr_pct``.

    Raises
    ------
    InsufficientDataError
        When ``len(df) < 20``.
    """
    n_rows = len(df)
    if n_rows < _REQUIRED_ROWS:
        raise InsufficientDataError(
            "DataFrame too short for ATR computation",
            required=_REQUIRED_ROWS,
            available=n_rows,
        )

    period: int = config.get("atr", {}).get("period", _DEFAULT_PERIOD)

    log.debug("computing ATR: rows=%d period=%d", n_rows, period)

    high: pd.Series = df["high"]
    low: pd.Series = df["low"]
    close: pd.Series = df["close"]
    prev_close: pd.Series = close.shift(1)

    # True Range: max of the three components (element-wise)
    tr: pd.Series = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder's smoothing: EWM with alpha = 1/period, adjust=False
    col_name = f"atr_{period}"
    df[col_name] = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    df["atr_pct"] = df[col_name] / close * 100.0

    log.debug("atr.compute finished; shape=%s", df.shape)
    return df
