"""
features/feature_store.py
--------------------------
Feature store orchestrator for the Minervini SEPA screening system.

This is the ONLY module that performs I/O for feature data.  It coordinates
all feature computation modules and reads/writes Parquet files via the
storage layer.  No indicators are computed here directly.

Public API
----------
bootstrap(symbol, config)  -- full history computation (run once / repair)
update(symbol, run_date, config) -- incremental daily append (fast path)
needs_bootstrap(symbol, config) -- check if bootstrap is required

Design notes
------------
* rs_raw / rs_rating columns are intentionally left NaN here.
  They are populated by a cross-symbol pass in screener/pipeline.py.
* The 300-row read window in update() is a fixed constant (not configurable).
* This module must NOT import from screener/, rules/, or api/.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from storage.parquet_store import (
    append_row,
    get_last_date,
    read_last_n_rows,
    read_parquet,
    write_parquet,
)
from utils.exceptions import FeatureStoreOutOfSyncError, InsufficientDataError
from utils.logger import get_logger
import features.moving_averages as ma_mod
import features.atr as atr_mod
import features.volume as vol_mod
import features.pivot as pivot_mod
import features.vcp as vcp_mod

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UPDATE_WINDOW: int = 300   # rows read for incremental update — NOT configurable


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _resolve_paths(symbol: str, config: dict) -> tuple[Path, Path]:
    """Return (processed_path, features_path) for *symbol*."""
    processed_path = Path(config["data"]["processed_dir"]) / f"{symbol}.parquet"
    features_path  = Path(config["data"]["features_dir"])  / f"{symbol}.parquet"
    return processed_path, features_path


def _run_pipeline(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Run all feature modules in the canonical order and return result.

    Order: moving_averages → atr → volume → pivot → vcp

    rs_raw / rs_rating are NOT computed here (cross-symbol, filled externally).
    Any InsufficientDataError raised by a module propagates to the caller.
    """
    df = ma_mod.compute(df, config)
    df = atr_mod.compute(df, config)
    df = vol_mod.compute(df, config)
    df = pivot_mod.compute(df, config)
    df = vcp_mod.compute(df, config)
    # rs_raw is intentionally left NaN here; it is filled by run_rs_rating_pass()
    # which requires a cross-symbol benchmark pass that feature_store cannot do alone.
    df["rs_raw"] = float("nan")
    return df



# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def needs_bootstrap(symbol: str, config: dict) -> bool:
    """Return True if the feature file is missing or empty.

    Does NOT check for corruption — just file existence + row count.

    Parameters
    ----------
    symbol:
        Ticker symbol (e.g. ``"RELIANCE"``).
    config:
        Screening configuration dict.

    Returns
    -------
    bool
        ``True``  → feature file absent or has 0 rows (bootstrap required).
        ``False`` → feature file exists with at least 1 row.
    """
    _, features_path = _resolve_paths(symbol, config)
    if not features_path.exists():
        return True
    df = read_parquet(features_path)
    return len(df) == 0


def bootstrap(symbol: str, config: dict) -> None:
    """Full history computation.  Run once on setup or to repair corruption.

    Reads ALL rows from the processed Parquet file, runs every feature module
    in sequence, and writes the result to the features Parquet file (full
    overwrite).  rs_raw / rs_rating columns are left as NaN.

    Parameters
    ----------
    symbol:
        Ticker symbol (e.g. ``"RELIANCE"``).
    config:
        Screening configuration dict.

    Raises
    ------
    InsufficientDataError
        Propagated from any feature module when the processed history is
        too short to compute the required indicators (minimum 200 rows for
        SMA-200).
    """
    processed_path, features_path = _resolve_paths(symbol, config)

    df = read_parquet(processed_path)
    n_input = len(df)

    log.debug("bootstrap %s: reading %d processed rows", symbol, n_input)

    feature_df = _run_pipeline(df.copy(), config)

    n_cols = len(feature_df.columns)
    write_parquet(features_path, feature_df)

    log.info(
        "bootstrap %s: %d rows → %d feature columns",
        symbol,
        n_input,
        n_cols,
    )


def update(symbol: str, run_date: date, config: dict) -> None:
    """Incremental daily update.  FAST PATH — use this for every daily run.

    Reads the last 300 rows from processed data, runs all feature modules,
    and appends only today's computed row to the feature Parquet file.

    Parameters
    ----------
    symbol:
        Ticker symbol (e.g. ``"RELIANCE"``).
    run_date:
        The trading date being processed (used only for log messages;
        the actual row date comes from the processed data index).
    config:
        Screening configuration dict.

    Raises
    ------
    InsufficientDataError
        When the processed file has fewer than 300 rows.  The caller should
        trigger ``bootstrap()`` in response.

    Notes
    -----
    * If ``append_row`` raises ``FeatureStoreOutOfSyncError`` (duplicate date),
      the error is logged as a warning and the function returns silently.
      This makes the operation idempotent — safe to call multiple times.
    """
    processed_path, features_path = _resolve_paths(symbol, config)

    window_df = read_last_n_rows(processed_path, _UPDATE_WINDOW)
    n_rows = len(window_df)

    if n_rows < _UPDATE_WINDOW:
        raise InsufficientDataError(
            f"update {symbol}: processed data has only {n_rows} rows; "
            f"need {_UPDATE_WINDOW} — run bootstrap instead",
            required=_UPDATE_WINDOW,
            available=n_rows,
        )

    feature_df = _run_pipeline(window_df.copy(), config)

    # Extract only the last row (today's values)
    new_row: pd.DataFrame = feature_df.iloc[[-1]]

    try:
        append_row(features_path, new_row)
    except FeatureStoreOutOfSyncError as exc:
        log.warning(
            "update %s %s: skipped — row already exists (%s)",
            symbol,
            run_date,
            exc,
        )
        return

    log.info("update %s %s: appended 1 feature row", symbol, run_date)
