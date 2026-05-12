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

_52W_WINDOW: int = 252          # ~1 trading year
_52W_MIN_PERIODS: int = 200     # minimum history to emit a non-NaN value


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

            config["trend_template"]["ma200_slope_lookback"]  (default 20)
            config["trend_template"]["ma50_slope_lookback"]   (default 10)

    Returns
    -------
    pd.DataFrame
        The same DataFrame with eight new float64 columns appended:
        ``sma_10``, ``sma_21``, ``sma_50``, ``sma_150``, ``sma_200``,
        ``ema_21``, ``ma_slope_50``, ``ma_slope_200``,
        ``high_52w``, ``low_52w``.

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

    trend_cfg: dict = config.get("trend_template", {})
    ma200_lookback: int = trend_cfg.get(
        "ma200_slope_lookback", _DEFAULT_MA200_SLOPE_LOOKBACK
    )
    ma50_lookback: int = trend_cfg.get(
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

    # ------------------------------------------------------------------
    # 52-week (252-bar) rolling high and low — required by trend_template
    # conditions 6 ("price ≥ N% above 52w low") and 7 ("price within N%
    # of 52w high").  Uses min_periods=200 so values appear as soon as
    # SMA-200 is available; for shorter histories they stay NaN.
    # ------------------------------------------------------------------
    df["high_52w"] = df["high"].rolling(window=_52W_WINDOW, min_periods=_52W_MIN_PERIODS).max()
    df["low_52w"]  = df["low"].rolling(window=_52W_WINDOW, min_periods=_52W_MIN_PERIODS).min()

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
