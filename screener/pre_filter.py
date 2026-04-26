"""
screener/pre_filter.py
----------------------
Cheap pre-filter gate that eliminates obvious non-candidates before the
expensive full Trend-Template rule engine runs.

Only ``build_features_index()`` performs I/O.
``pre_filter()`` is a pure function — no file reads, no side-effects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from storage.parquet_store import read_last_n_rows
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Required feature keys consumed by pre_filter()
# ---------------------------------------------------------------------------
_REQUIRED_KEYS: tuple[str, ...] = ("close", "high_52w", "rs_rating", "sma_200")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def pre_filter(
    features_index: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> list[str]:
    """Fast gate using only last-row feature summary values.

    Criteria (intentionally MORE PERMISSIVE than the real Trend Template):
      1. close >= min_close_pct_of_52w_high * high_52w   (default 70 %)
      2. rs_rating >= min_rs_rating                       (default 50)
      3. close > sma_200                                  (Stage-2 requires this)

    Symbols missing any required key are EXCLUDED and logged as warnings.

    Parameters
    ----------
    features_index:
        Mapping of symbol → {feature_name: value, ...}.
    config:
        Project-wide config dict (keys read from ``config["pre_filter"]``).

    Returns
    -------
    list[str]
        Symbols that pass all three criteria.
    """
    pf_cfg = config.get("pre_filter", {})
    min_pct: float = float(pf_cfg.get("min_close_pct_of_52w_high", 0.70))
    min_rs: float = float(pf_cfg.get("min_rs_rating", 50))

    passed: list[str] = []

    for symbol, feats in features_index.items():
        # --- Guard: all required keys must be present ---
        missing = [k for k in _REQUIRED_KEYS if k not in feats]
        if missing:
            log.warning(
                "pre_filter: skipping %s — missing keys: %s",
                symbol,
                missing,
            )
            continue

        close: float = float(feats["close"])
        high_52w: float = float(feats["high_52w"])
        rs_rating: float = float(feats["rs_rating"])
        sma_200: float = float(feats["sma_200"])

        # Criterion 1: close ≥ threshold × 52-week high
        if high_52w > 0 and close < min_pct * high_52w:
            continue

        # Criterion 2: RS rating floor
        if rs_rating < min_rs:
            continue

        # Criterion 3: price above 200-day SMA (Stage 2 proxy)
        if close <= sma_200:
            continue

        passed.append(symbol)

    total = len(features_index)
    pct = len(passed) / total if total else 0.0
    log.info(
        "pre_filter: %d/%d symbols passed (%.0f%%)",
        len(passed),
        total,
        pct * 100,
    )
    return passed


def build_features_index(
    universe: list[str],
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Read the last row of each symbol's feature Parquet and build a
    ``features_index`` dict consumable by :func:`pre_filter`.

    For each symbol the function:
    * Reads the last 1 row of ``data/features/{symbol}.parquet`` for
      ``close``, ``sma_200``, and ``rs_rating``.
    * Reads the last 252 rows of ``data/processed/{symbol}.parquet``
      to derive ``high_52w = max("high")``.

    Symbols with missing feature files are silently omitted (logged at
    WARNING level).

    Parameters
    ----------
    universe:
        List of ticker symbols to process.
    config:
        Project-wide config dict.  Uses:
        * ``config["data"]["features_dir"]``   (default ``"data/features"``)
        * ``config["data"]["processed_dir"]``  (default ``"data/processed"``)

    Returns
    -------
    dict[str, dict]
        ``{symbol: {close, sma_200, rs_rating, high_52w}, ...}``
    """
    data_cfg = config.get("data", {})
    features_dir = Path(data_cfg.get("features_dir", "data/features"))
    processed_dir = Path(data_cfg.get("processed_dir", "data/processed"))

    index: dict[str, dict[str, Any]] = {}

    for symbol in universe:
        feat_path = features_dir / f"{symbol}.parquet"
        proc_path = processed_dir / f"{symbol}.parquet"

        # --- Feature row (last 1) ---
        feat_df = read_last_n_rows(feat_path, 1)
        if feat_df.empty:
            log.warning(
                "build_features_index: missing or empty feature file for %s (%s)",
                symbol,
                feat_path,
            )
            continue

        row = feat_df.iloc[-1]

        # Extract the three direct feature columns (may be absent → skip)
        required_cols = {"close", "sma_200", "rs_rating"}
        missing_cols = required_cols - set(feat_df.columns)
        if missing_cols:
            log.warning(
                "build_features_index: %s feature file missing columns %s — skipping",
                symbol,
                sorted(missing_cols),
            )
            continue

        close = float(row["close"])
        sma_200 = float(row["sma_200"])
        rs_rating = float(row["rs_rating"])

        # --- Derive high_52w from processed OHLCV (last 252 rows) ---
        proc_df = read_last_n_rows(proc_path, 252)
        if proc_df.empty or "high" not in proc_df.columns:
            log.warning(
                "build_features_index: cannot derive high_52w for %s — "
                "processed file missing or has no 'high' column (%s)",
                symbol,
                proc_path,
            )
            continue

        high_52w = float(proc_df["high"].max())

        index[symbol] = {
            "close": close,
            "sma_200": sma_200,
            "rs_rating": rs_rating,
            "high_52w": high_52w,
        }

    log.info(
        "build_features_index: built index for %d/%d symbols",
        len(index),
        len(universe),
    )
    return index
