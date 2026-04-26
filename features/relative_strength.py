"""
features/relative_strength.py
------------------------------
Minervini RS Rating — measures how much stronger a stock's 63-day return
is versus the Nifty-500 benchmark, then ranks it as a 0–99 percentile
score across the entire universe.

TWO-STAGE DESIGN
----------------
Stage 1  (per-symbol)  : compute_rs_raw()
    Appends a single ``rs_raw`` column to the symbol DataFrame.
    rs_raw = symbol_63d_return / benchmark_63d_return
    where return = (close_today / close_63_days_ago) - 1

Stage 2  (cross-universe) : compute_rs_rating()
    Called once after rs_raw is collected for ALL symbols.
    Returns an integer 0–99 percentile rank per symbol.

IMPORTANT
---------
* Do NOT merge these stages — compute_rs_raw touches DataFrames,
  compute_rs_rating never sees a DataFrame; it only works with floats.
* The benchmark ticker is NEVER hardcoded; it is passed in as a
  prepared DataFrame by the caller (loaded from config upstream).
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

_DEFAULT_RS_PERIOD: int = 63
_REQUIRED_ROWS_BUFFER: int = 2          # extra rows beyond the period
_MIN_ROWS: int = _DEFAULT_RS_PERIOD + _REQUIRED_ROWS_BUFFER  # 65


# ---------------------------------------------------------------------------
# Stage 1 — per-symbol
# ---------------------------------------------------------------------------


def compute_rs_raw(
    symbol_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    config: dict,
) -> pd.DataFrame:
    """Append ``rs_raw`` to *symbol_df* and return it.

    Parameters
    ----------
    symbol_df:
        OHLCV DataFrame with a DatetimeIndex and at least a ``close`` column.
    benchmark_df:
        Benchmark OHLCV (e.g. Nifty 500 index) with the same DatetimeIndex
        schema and a ``close`` column.  It is assumed to be aligned to
        *symbol_df* by the caller (same dates).
    config:
        Screening config dict.  Relevant key::

            config["rs"]["period"]  (default 63)

    Returns
    -------
    pd.DataFrame
        *symbol_df* with one additional float64 column ``rs_raw``.

    Raises
    ------
    InsufficientDataError
        When ``len(symbol_df) < period + 2`` (default: < 65).

    Notes
    -----
    rs_raw is computed only for the *last* row (today's screen date) because
    the screener is a point-in-time daily snapshot.  All other rows receive
    NaN for rs_raw, consistent with the moving_averages pattern.
    """
    period: int = config.get("rs", {}).get("period", _DEFAULT_RS_PERIOD)
    min_rows: int = period + _REQUIRED_ROWS_BUFFER

    n_rows = len(symbol_df)
    if n_rows < min_rows:
        raise InsufficientDataError(
            "DataFrame too short for RS computation",
            required=min_rows,
            available=n_rows,
        )

    log.debug("compute_rs_raw: rows=%d period=%d", n_rows, period)

    close_sym: pd.Series = symbol_df["close"]
    close_bm: pd.Series = benchmark_df["close"]

    # Compute rolling rs_raw across the whole series so the column is fully
    # populated (useful for back-testing / feature store).
    sym_return = close_sym / close_sym.shift(period) - 1.0
    bm_return = close_bm / close_bm.shift(period) - 1.0

    # Guard against division by zero when benchmark return is exactly 0
    rs_raw = sym_return / bm_return.replace(0.0, np.nan)

    symbol_df = symbol_df.copy()
    symbol_df["rs_raw"] = rs_raw

    log.debug(
        "compute_rs_raw finished; last rs_raw=%.4f",
        rs_raw.iloc[-1] if not rs_raw.empty else float("nan"),
    )
    return symbol_df


# ---------------------------------------------------------------------------
# Stage 2 — cross-universe
# ---------------------------------------------------------------------------


def compute_rs_rating(
    all_rs_raw: dict[str, float],
) -> dict[str, int]:
    """Rank all symbols by rs_raw and return a 0–99 percentile score.

    Parameters
    ----------
    all_rs_raw:
        Mapping of symbol → most-recent rs_raw float value, e.g.::

            {"RELIANCE": 1.23, "TCS": 0.87, "INFY": 0.95}

    Returns
    -------
    dict[str, int]
        Mapping of symbol → integer RS Rating in [0, 99], e.g.::

            {"RELIANCE": 88, "TCS": 30, "INFY": 55}

        A rating of 88 means the symbol outperformed 88 % of the universe.

    Notes
    -----
    * NaN / ±Inf values are assigned a rating of 0 rather than causing a crash.
    * When the universe has only one symbol it receives a rating of 99.
    * The percentile is computed with "weak" ranking: ties get the same score.
    """
    if not all_rs_raw:
        return {}

    symbols = list(all_rs_raw.keys())
    raw_values = np.array([all_rs_raw[s] for s in symbols], dtype=float)

    n = len(symbols)

    if n == 1:
        # Edge case: single symbol gets the maximum score
        return {symbols[0]: 99}

    # Replace NaN / ±Inf with -inf so they rank at the bottom
    finite_mask = np.isfinite(raw_values)
    values_clean = np.where(finite_mask, raw_values, -np.inf)

    # Percentile rank: fraction of universe that this symbol beats (strictly).
    # Equivalent to scipy.stats.percentileofscore(values, v, kind='weak') / 100
    # but vectorised over all symbols at once.
    #
    # For each symbol i: rank_i = number of symbols with value < values[i]
    # percentile_i = rank_i / (n - 1) scaled to [0, 99] and floored to int.
    #
    # Using broadcasting: count how many values each element beats.
    beats = np.sum(values_clean[:, None] > values_clean[None, :], axis=1)

    # Scale to [0, 99]
    ratings_float = beats / (n - 1) * 99.0
    ratings_int = np.floor(ratings_float).astype(int)

    # Clamp to [0, 99] as a safety measure
    ratings_int = np.clip(ratings_int, 0, 99)

    result = {sym: int(rating) for sym, rating in zip(symbols, ratings_int)}

    log.debug(
        "compute_rs_rating: universe_size=%d min=%d max=%d",
        n,
        min(result.values()),
        max(result.values()),
    )
    return result
