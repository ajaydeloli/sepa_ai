"""
features/moving_averages.py
---------------------------
Minervini SEPA stage-analysis moving-average feature module.

Implements the ``compute`` interface contract:
  - Pure function: no I/O, no side effects, no global state.
  - Appends indicator columns to the input DataFrame and returns it.
  - Raises InsufficientDataError when the DataFrame is too short for
    the longest window (SMA-200).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.exceptions import InsufficientDataError
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REQUIRED_ROWS: int = 200  # SMA-200 is the longest window

_SMA_WINDOWS: list[int] = [10, 21, 50, 150, 200]

_DEFAULT_MA200_SLOPE_LOOKBACK: int = 20
_DEFAULT_MA50_SLOPE_LOOKBACK: int = 10


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Append moving-average indicator columns to *df* and return it.

    Parameters
    ----------
    df:
        Cleaned OHLCV DataFrame with a DatetimeIndex and columns:
        ``open``, ``high``, ``low``, ``close``, ``volume``.
    config:
        Screening configuration dict.  Relevant keys::

            config["stage"]["ma200_slope_lookback"]  (default 20)
            config["stage"]["ma50_slope_lookback"]   (default 10)

    Returns
    -------
    pd.DataFrame
        The same DataFrame with eight new float64 columns appended:
        ``sma_10``, ``sma_21``, ``sma_50``, ``sma_150``, ``sma_200``,
        ``ema_21``, ``ma_slope_50``, ``ma_slope_200``.

    Raises
    ------
    InsufficientDataError
        When ``len(df) < 200``.
    """
    n_rows = len(df)
    if n_rows < _REQUIRED_ROWS:
        raise InsufficientDataError(
            "DataFrame too short for SMA-200 computation",
            required=_REQUIRED_ROWS,
            available=n_rows,
        )

    stage_cfg: dict = config.get("stage", {})
    ma200_lookback: int = stage_cfg.get(
        "ma200_slope_lookback", _DEFAULT_MA200_SLOPE_LOOKBACK
    )
    ma50_lookback: int = stage_cfg.get(
        "ma50_slope_lookback", _DEFAULT_MA50_SLOPE_LOOKBACK
    )

    log.debug(
        "computing moving averages: rows=%d ma200_lookback=%d ma50_lookback=%d",
        n_rows,
        ma200_lookback,
        ma50_lookback,
    )

    close: pd.Series = df["close"]

    # ------------------------------------------------------------------
    # Simple moving averages
    # ------------------------------------------------------------------
    for window in _SMA_WINDOWS:
        col = f"sma_{window}"
        df[col] = close.rolling(window=window, min_periods=window).mean()

    # ------------------------------------------------------------------
    # Exponential moving average — matches ewm(span=21, adjust=False)
    # ------------------------------------------------------------------
    df["ema_21"] = close.ewm(span=21, adjust=False).mean()

    # ------------------------------------------------------------------
    # Linear-regression slope of SMA series
    # ------------------------------------------------------------------
    df["ma_slope_50"] = _rolling_slope(df["sma_50"], ma50_lookback)
    df["ma_slope_200"] = _rolling_slope(df["sma_200"], ma200_lookback)

    log.debug("moving_averages.compute finished; shape=%s", df.shape)
    return df



# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _rolling_slope(series: pd.Series, window: int) -> pd.Series:
    """Return per-bar linear-regression slope of *series* over *window* bars.

    Uses ``numpy.polyfit(degree=1)`` on each rolling window of length
    *window*.  The x-axis is a unit-spaced integer index (0, 1, …, N-1),
    so the returned coefficient is *slope per bar* (not annualised).

    Rows with fewer than *window* preceding values produce ``NaN``.

    Parameters
    ----------
    series:
        The MA series to differentiate (e.g. ``sma_50``).
    window:
        Number of bars to include in each regression.

    Returns
    -------
    pd.Series
        Same index as *series*; dtype float64.
    """
    x = np.arange(window, dtype=float)

    def _slope(y: np.ndarray) -> float:
        # polyfit returns [slope, intercept]; we only need the slope.
        return float(np.polyfit(x, y, 1)[0])

    return series.rolling(window=window, min_periods=window).apply(
        _slope, raw=True
    )
