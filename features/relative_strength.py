"""
features/relative_strength.py
------------------------------
Minervini RS Rating — IBD-style multi-period weighted return, ranked as a
0–99 percentile score across the full universe.

FORMULA (IBD / Minervini)
-------------------------
Stage 1  (per-symbol)  : compute_rs_raw()

    rs_raw = 0.4 * (C / C_63)
           + 0.2 * (C / C_126)
           + 0.2 * (C / C_189)
           + 0.2 * (C / C_252)

    where C = today's close, C_N = close N trading days ago.
    The most-recent quarter (63 days) is double-weighted (0.4) to emphasise
    recent momentum.  All four windows together span one full year (252 days).

    Crucially, rs_raw is the stock's own weighted return score — NOT divided
    by a benchmark.  The benchmark is irrelevant at this stage; the ranking
    step (Stage 2) is what makes it relative.

Stage 2  (cross-universe) : compute_rs_rating()
    Called ONCE after rs_raw is collected for ALL symbols.
    Percentile-ranks every symbol's rs_raw against the rest of the universe
    and returns an integer 0–99 score.  A rating of 88 means the stock
    outperformed 88 % of the universe.

DATA REQUIREMENT
----------------
Full formula requires 254 trading days (~1 year).  For IPOs and recently
listed stocks, a **degraded formula** is used automatically — weights are
renormalised to 1.0 using only the quarterly windows that have data:

  ≥ 254 days → Q1+Q2+Q3+Q4  weights 0.40/0.20/0.20/0.20  (full IBD)
  ≥ 191 days → Q1+Q2+Q3     weights 0.50/0.25/0.25
  ≥ 128 days → Q1+Q2        weights 0.67/0.33
  ≥  65 days → Q1 only      weight  1.00
  <  65 days → rating = 0   (too new for any meaningful RS)

IMPORTANT
---------
* Do NOT merge the two stages — compute_rs_raw touches DataFrames,
  compute_rs_rating never sees a DataFrame; it only works with floats.
* The benchmark_df parameter is kept in compute_rs_raw's signature for
  future use (e.g. plotting the RS Line on charts) but is NOT used in the
  rating calculation.
* Lookback periods and minimum rows are read from config so they can be
  changed in settings.yaml without touching this file.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.exceptions import InsufficientDataError
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants  (used as fallbacks when config keys are absent)
# ---------------------------------------------------------------------------

_DEFAULT_Q1: int = 63    # 3-month  — weight 0.4
_DEFAULT_Q2: int = 126   # 6-month  — weight 0.2
_DEFAULT_Q3: int = 189   # 9-month  — weight 0.2
_DEFAULT_Q4: int = 252   # 12-month — weight 0.2
_DEFAULT_MIN_ROWS: int = 254   # period_q4 + 2 buffer rows

_W1: float = 0.4
_W2: float = 0.2
_W3: float = 0.2
_W4: float = 0.2


def _rs_config(config: dict) -> tuple[int, int, int, int, int, int]:
    """Return (q1, q2, q3, q4, min_rows, min_ipo_rows) from config with safe defaults."""
    rs = config.get("rs", {})
    q1 = int(rs.get("period_q1", _DEFAULT_Q1))
    q2 = int(rs.get("period_q2", _DEFAULT_Q2))
    q3 = int(rs.get("period_q3", _DEFAULT_Q3))
    q4 = int(rs.get("period_q4", _DEFAULT_Q4))
    min_rows = int(rs.get("min_rows", q4 + 2))
    min_ipo_rows = int(rs.get("min_ipo_rows", q1 + 2))   # floor: one quarter + buffer
    return q1, q2, q3, q4, min_rows, min_ipo_rows


# ---------------------------------------------------------------------------
# Stage 1 — per-symbol
# ---------------------------------------------------------------------------


def compute_rs_raw(
    symbol_df: pd.DataFrame,
    benchmark_df: pd.DataFrame | None,
    config: dict,
) -> pd.DataFrame:
    """Append ``rs_raw`` to *symbol_df* and return it.

    Supports a **degraded formula** for IPOs and recently listed stocks that
    do not yet have a full year of history.  The weights are renormalised to
    sum to 1.0 using only the quarterly windows for which data is available:

    +------------------+-------------------+---------------------+
    | History          | Windows used      | Renormalised weights|
    +==================+===================+=====================+
    | ≥ 254 days       | Q1+Q2+Q3+Q4       | 0.40/0.20/0.20/0.20 |
    | ≥ 191 days       | Q1+Q2+Q3          | 0.50/0.25/0.25      |
    | ≥ 128 days       | Q1+Q2             | 0.67/0.33           |
    | ≥ 65 days        | Q1 only           | 1.00                |
    | < 65 days        | —                 | raises              |
    +------------------+-------------------+---------------------+

    Parameters
    ----------
    symbol_df:
        OHLCV DataFrame with a DatetimeIndex and at least a ``close`` column.
        Must have at least ``min_ipo_rows`` rows (default 65).
    benchmark_df:
        Accepted for API compatibility / future RS-Line charting.
        NOT used in the rating calculation.
    config:
        Screening config dict.  Relevant keys::

            config["rs"]["period_q1"]    (default 63)
            config["rs"]["period_q2"]    (default 126)
            config["rs"]["period_q3"]    (default 189)
            config["rs"]["period_q4"]    (default 252)
            config["rs"]["min_rows"]     (default 254)
            config["rs"]["min_ipo_rows"] (default 65)

    Returns
    -------
    pd.DataFrame
        *symbol_df* with one additional float64 column ``rs_raw``.

    Raises
    ------
    InsufficientDataError
        When ``len(symbol_df) < min_ipo_rows`` (default: < 65).
        This is the absolute floor — below one full quarter no RS is possible.
    """
    q1, q2, q3, q4, min_rows, min_ipo_rows = _rs_config(config)

    n_rows = len(symbol_df)

    # Absolute floor — less than one quarter means no meaningful RS at all
    if n_rows < min_ipo_rows:
        raise InsufficientDataError(
            f"DataFrame too short for RS computation "
            f"(need at least {min_ipo_rows} rows for single-quarter formula, got {n_rows})",
            required=min_ipo_rows,
            available=n_rows,
        )

    close: pd.Series = symbol_df["close"]

    # ── Select the highest-tier formula available for this symbol ──────────
    # Weights are renormalised so they always sum to 1.0 regardless of tier.
    # Tier boundaries use a 2-row buffer beyond each period (same as min_rows).
    if n_rows >= q4 + 2:
        # Full formula — all four quarters
        w1, w2, w3, w4 = 0.40, 0.20, 0.20, 0.20
        rs_raw = (
            w1 * (close / close.shift(q1))
            + w2 * (close / close.shift(q2))
            + w3 * (close / close.shift(q3))
            + w4 * (close / close.shift(q4))
        )
        tier = "full (Q1+Q2+Q3+Q4)"

    elif n_rows >= q3 + 2:
        # Three quarters — renormalise: original weights 0.4/0.2/0.2 → sum 0.8
        w1, w2, w3 = 0.50, 0.25, 0.25
        rs_raw = (
            w1 * (close / close.shift(q1))
            + w2 * (close / close.shift(q2))
            + w3 * (close / close.shift(q3))
        )
        tier = "degraded (Q1+Q2+Q3)"

    elif n_rows >= q2 + 2:
        # Two quarters — renormalise: original weights 0.4/0.2 → sum 0.6
        w1, w2 = round(0.4 / 0.6, 6), round(0.2 / 0.6, 6)
        rs_raw = (
            w1 * (close / close.shift(q1))
            + w2 * (close / close.shift(q2))
        )
        tier = "degraded (Q1+Q2)"

    else:
        # Single quarter only — weight 1.0
        rs_raw = close / close.shift(q1)
        tier = "degraded (Q1 only)"

    log.debug(
        "compute_rs_raw: rows=%d  tier=%s  last_rs_raw=%.4f",
        n_rows, tier, float(rs_raw.iloc[-1]) if not rs_raw.empty else float("nan"),
    )

    symbol_df = symbol_df.copy()
    symbol_df["rs_raw"] = rs_raw
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
        Mapping of symbol → integer RS Rating in [0, 99].

        A rating of 88 means the symbol's weighted 12-month return
        outperformed 88 % of the universe.

    Notes
    -----
    * NaN / ±Inf values are assigned a rating of 0 rather than causing a crash.
    * When the universe has only one symbol it receives a rating of 99.
    * The percentile uses "weak" ranking: ties receive the same score.
    """
    if not all_rs_raw:
        return {}

    symbols = list(all_rs_raw.keys())
    raw_values = np.array([all_rs_raw[s] for s in symbols], dtype=float)

    n = len(symbols)

    if n == 1:
        return {symbols[0]: 99}

    # Replace NaN / ±Inf with -inf so they sink to the bottom of the ranking
    values_clean = np.where(np.isfinite(raw_values), raw_values, -np.inf)

    # Vectorised percentile rank:
    #   beats[i] = number of symbols whose rs_raw is strictly less than symbol i
    #   rating[i] = floor(beats[i] / (n-1) * 99)  →  range [0, 99]
    beats = np.sum(values_clean[:, None] > values_clean[None, :], axis=1)
    ratings_int = np.clip(np.floor(beats / (n - 1) * 99.0).astype(int), 0, 99)

    result = {sym: int(r) for sym, r in zip(symbols, ratings_int)}

    log.debug(
        "compute_rs_rating: universe=%d  min=%d  max=%d",
        n, min(result.values()), max(result.values()),
    )
    return result


# ---------------------------------------------------------------------------
# Cross-symbol orchestration helpers  (called from screener/pipeline.py)
# ---------------------------------------------------------------------------


def run_rs_rating_pass(
    universe: list[str],
    run_date: "date",
    config: dict,
    benchmark_df: pd.DataFrame,
) -> dict[str, int]:
    """Compute RS ratings for every symbol in *universe* in a single pass.

    Orchestrates the two-stage IBD RS calculation:

    Stage 1 (per symbol):
        Read processed parquet → call ``compute_rs_raw()`` → extract last
        ``rs_raw`` value.  Symbols with fewer than ``min_rows`` trading days
        (default 254 ≈ 1 year) receive ``rs_raw = NaN``.

    Stage 2 (cross-universe):
        Call ``compute_rs_rating()`` on all valid rs_raw values.
        Symbols with NaN are excluded from the ranking pool and assigned
        rating = 0.

    Parameters
    ----------
    universe:
        List of ticker symbols to process.
    run_date:
        The trading date being processed.  Used for log messages only.
    config:
        Screening configuration dict.  Relevant keys::

            config["data"]["processed_dir"]
            config["rs"]["period_q1..q4"]
            config["rs"]["min_rows"]
    benchmark_df:
        Passed through to ``compute_rs_raw`` for API compatibility.
        Not used in the IBD rating calculation.

    Returns
    -------
    dict[str, int]
        ``symbol → rs_rating`` (0–99) for every symbol in *universe*.
        Symbols with insufficient data receive 0.
    """
    from pathlib import Path
    from storage.parquet_store import read_last_n_rows

    processed_dir = Path(config["data"]["processed_dir"])
    _, _, _, q4, min_rows, min_ipo_rows = _rs_config(config)

    # Read enough rows to cover the full formula; compute_rs_raw degrades
    # automatically when fewer rows are available (IPO / recently listed stocks).
    rows_needed: int = q4 + 10

    all_rs_raw: dict[str, float] = {}
    n_full = n_degraded = n_zero = 0

    for symbol in universe:
        path = processed_dir / f"{symbol}.parquet"
        try:
            symbol_df = read_last_n_rows(path, rows_needed)
            n_rows = len(symbol_df)

            # compute_rs_raw handles all tiers internally and only raises
            # InsufficientDataError when rows < min_ipo_rows (< 1 quarter).
            result_df = compute_rs_raw(symbol_df, benchmark_df, config)
            rs_val = float(result_df["rs_raw"].iloc[-1])
            all_rs_raw[symbol] = rs_val

            if n_rows >= min_rows:
                n_full += 1
            else:
                n_degraded += 1
                log.info(
                    "run_rs_rating_pass %s %s: IPO/new listing — %d rows, "
                    "using degraded formula",
                    symbol, run_date, n_rows,
                )

        except InsufficientDataError:
            # Below absolute floor (< min_ipo_rows ≈ 65 days) — too new for any RS
            all_rs_raw[symbol] = float("nan")
            n_zero += 1
            log.debug(
                "run_rs_rating_pass %s %s: < %d rows — rating=0 (too new)",
                symbol, run_date, min_ipo_rows,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "run_rs_rating_pass %s %s: unexpected error — %s",
                symbol, run_date, exc,
            )
            all_rs_raw[symbol] = float("nan")
            n_zero += 1

    # Stage 2: rank only valid symbols; zero-rate the rest
    valid_rs = {s: v for s, v in all_rs_raw.items() if np.isfinite(v)}
    ratings = compute_rs_rating(valid_rs) if valid_rs else {}

    result: dict[str, int] = {symbol: int(ratings.get(symbol, 0)) for symbol in universe}

    log.info(
        "run_rs_rating_pass %s: %d symbols — %d full, %d degraded (IPO), "
        "%d zero-rated (too new / error)",
        run_date, len(universe), n_full, n_degraded, n_zero,
    )
    return result


def write_rs_ratings_to_features(
    rs_ratings: dict[str, int],
    config: dict,
) -> None:
    """Write RS ratings back into each symbol's feature Parquet file (last row only).

    Called ONCE per daily run after :func:`run_rs_rating_pass` has computed
    ratings for the full universe.  Reads the existing feature file, updates
    the last row's ``rs_rating`` column, and writes it back atomically.

    Parameters
    ----------
    rs_ratings:
        Mapping of ``symbol → rs_rating`` as returned by
        :func:`run_rs_rating_pass`.
    config:
        Screening configuration dict.  Relevant key::

            config["data"]["features_dir"]

    Notes
    -----
    * Only symbols whose feature file already exists are processed.
    * Missing feature files are skipped with a warning (not an error).
    * The write is a full atomic overwrite via
      :func:`~storage.parquet_store.write_parquet`.
    """
    from pathlib import Path
    from storage.parquet_store import read_parquet, write_parquet

    features_dir = Path(config["data"]["features_dir"])

    for symbol, rating in rs_ratings.items():
        path = features_dir / f"{symbol}.parquet"

        if not path.exists():
            log.warning(
                "write_rs_ratings_to_features %s: feature file not found — skipped",
                symbol,
            )
            continue

        try:
            df = read_parquet(path)
            if df.empty:
                log.warning(
                    "write_rs_ratings_to_features %s: feature file is empty — skipped",
                    symbol,
                )
                continue

            if "rs_rating" not in df.columns:
                df["rs_rating"] = np.nan
            df.iloc[-1, df.columns.get_loc("rs_rating")] = int(rating)

            write_parquet(path, df)
            log.debug(
                "write_rs_ratings_to_features %s: rs_rating=%d written to last row",
                symbol, rating,
            )

        except Exception as exc:  # noqa: BLE001
            log.warning(
                "write_rs_ratings_to_features %s: failed to update — %s",
                symbol, exc,
            )

    log.info(
        "write_rs_ratings_to_features: updated %d feature files",
        len(rs_ratings),
    )
