"""
features/volume.py
------------------
Volume-analysis feature module for the Minervini SEPA screener.

Implements the ``compute`` interface contract:
  - Pure function: no I/O, no side effects, no global state.
  - Appends indicator columns to the input DataFrame and returns it.
  - Raises InsufficientDataError when the DataFrame is too short.

New columns appended
--------------------
vol_50d_avg   : Simple 50-day rolling average of ``volume``.
vol_ratio     : volume / vol_50d_avg — today's volume relative to its avg.
                NaN for the first 49 rows (rolling window not yet full).
up_vol_days   : Count of days in the trailing ``lookback_days`` window where
                close > prev_close AND volume > vol_50d_avg.
down_vol_days : Count of days in the trailing ``lookback_days`` window where
                close < prev_close AND volume > vol_50d_avg.
acc_dist_score: up_vol_days - down_vol_days.
                Positive → accumulation pressure; negative → distribution.
"""

from __future__ import annotations

import pandas as pd

from utils.exceptions import InsufficientDataError
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REQUIRED_ROWS: int = 55           # 50-day avg + 5-row safety buffer
_DEFAULT_AVG_PERIOD: int = 50
_DEFAULT_LOOKBACK_DAYS: int = 20


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Append volume indicator columns to *df* and return it.

    Parameters
    ----------
    df:
        Cleaned OHLCV DataFrame with a DatetimeIndex and columns:
        ``open``, ``high``, ``low``, ``close``, ``volume``.
    config:
        Screening configuration dict.  Relevant keys::

            config["volume"]["avg_period"]     (default 50)
            config["volume"]["lookback_days"]  (default 20)

    Returns
    -------
    pd.DataFrame
        The same DataFrame with five new columns appended:
        ``vol_50d_avg``, ``vol_ratio``, ``up_vol_days``,
        ``down_vol_days``, ``acc_dist_score``.

    Raises
    ------
    InsufficientDataError
        When ``len(df) < 55``.
    """
    n_rows = len(df)
    if n_rows < _REQUIRED_ROWS:
        raise InsufficientDataError(
            "DataFrame too short for volume computation",
            required=_REQUIRED_ROWS,
            available=n_rows,
        )

    vol_cfg: dict = config.get("volume", {})
    avg_period: int = vol_cfg.get("avg_period", _DEFAULT_AVG_PERIOD)
    lookback_days: int = vol_cfg.get("lookback_days", _DEFAULT_LOOKBACK_DAYS)

    log.debug(
        "computing volume features: rows=%d avg_period=%d lookback_days=%d",
        n_rows,
        avg_period,
        lookback_days,
    )

    volume: pd.Series = df["volume"]
    close: pd.Series = df["close"]
    prev_close: pd.Series = close.shift(1)

    # ------------------------------------------------------------------
    # 50-day average volume and today's relative volume
    # ------------------------------------------------------------------
    avg_col = f"vol_{avg_period}d_avg"
    df[avg_col] = volume.rolling(window=avg_period, min_periods=avg_period).mean()
    df["vol_ratio"] = volume / df[avg_col]

    # ------------------------------------------------------------------
    # Up / down volume days over the lookback window
    # ------------------------------------------------------------------
    above_avg_vol: pd.Series = (volume > df[avg_col]).astype(float)

    up_days: pd.Series = (
        ((close > prev_close) & (volume > df[avg_col]))
        .astype(float)
        .rolling(window=lookback_days, min_periods=lookback_days)
        .sum()
    )

    down_days: pd.Series = (
        ((close < prev_close) & (volume > df[avg_col]))
        .astype(float)
        .rolling(window=lookback_days, min_periods=lookback_days)
        .sum()
    )

    df["up_vol_days"] = up_days
    df["down_vol_days"] = down_days
    df["acc_dist_score"] = up_days - down_days

    log.debug("volume.compute finished; shape=%s", df.shape)
    return df
