"""
features/sector_rs.py
----------------------
Rank market sectors by the median RS Rating of their member symbols.

The top-N sectors (default top-5) earn a +5 point bonus in the external
scorer.  This module only PRODUCES the sector ranking — it never applies
the bonus itself.

DESIGN NOTES
------------
* Pure functions — no I/O, no global state, no side effects.
* symbol_info is a DataFrame loaded from ``data/metadata/symbol_info.csv``
  with at least the columns: ``symbol``, ``sector``.
* Missing symbols in symbol_info are handled gracefully (returns 0 / rank
  stays unaffected).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utils.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_sector_ranks(
    symbol_rs_ratings: dict[str, int],
    symbol_info: pd.DataFrame,
) -> dict[str, int]:
    """Return sector → rank mapping (rank 1 = strongest sector).

    Sectors are ordered by the **median** RS Rating of their member symbols.
    Only symbols present in *symbol_info* AND in *symbol_rs_ratings* are
    considered; unknown symbols are silently ignored.

    Parameters
    ----------
    symbol_rs_ratings:
        Universe-wide RS Ratings, e.g.::

            {"RELIANCE": 88, "TCS": 62, "HDFCBANK": 75}

    symbol_info:
        DataFrame loaded from ``data/metadata/symbol_info.csv``.
        Must contain at minimum the columns ``symbol`` and ``sector``.

    Returns
    -------
    dict[str, int]
        ``{"sector_name": rank}`` where ``rank=1`` is the strongest sector.

    Notes
    -----
    * Sectors with no rated symbols are excluded from the result.
    * Ties in median RS Rating receive the same rank (dense ranking).
    """
    _validate_symbol_info(symbol_info)

    # Build a Series: symbol → RS rating (only known symbols)
    ratings_series = pd.Series(symbol_rs_ratings, name="rs_rating")

    # Merge ratings onto symbol_info to get sector labels per symbol
    info_indexed = symbol_info.set_index("symbol")[["sector"]]
    merged = info_indexed.join(ratings_series, how="inner")

    if merged.empty:
        log.warning("compute_sector_ranks: no symbols matched between ratings and symbol_info")
        return {}

    # Median RS rating per sector
    sector_medians: pd.Series = (
        merged.groupby("sector")["rs_rating"]
        .median()
        .sort_values(ascending=False)
    )

    # Dense rank: strongest sector = 1
    # Use pandas rank with method='dense' then cast to int
    sector_ranks_float = sector_medians.rank(method="dense", ascending=False)
    sector_ranks: dict[str, int] = {
        sector: int(rank)
        for sector, rank in sector_ranks_float.items()
    }

    log.debug(
        "compute_sector_ranks: %d sectors ranked; top sector=%s",
        len(sector_ranks),
        sector_medians.index[0] if not sector_medians.empty else "N/A",
    )
    return sector_ranks


def get_sector_score(
    symbol: str,
    sector_ranks: dict[str, int],
    symbol_info: pd.DataFrame,
) -> float:
    """Return a 0–100 sector strength score for *symbol*.

    Replaces the legacy binary ``+5`` bonus with a continuous score that can
    be used as a proper weighted component in the composite scorer.

    Scoring formula (linear, rank-proportional):
      score = 100 × (1 − (rank − 1) / max(1, total_sectors − 1))

    This means:
      • Rank 1  (strongest sector) → 100.0
      • Rank N  (weakest sector)   → 0.0
      • Intermediate ranks         → linear interpolation

    Returns 50.0 (neutral) when the symbol or its sector is not found, so
    an unknown sector neither helps nor hurts a stock's composite score.

    Parameters
    ----------
    symbol:
        Ticker to look up (e.g. ``"RELIANCE"``).
    sector_ranks:
        Output of :func:`compute_sector_ranks`.
    symbol_info:
        DataFrame with at least ``symbol`` and ``sector`` columns.

    Returns
    -------
    float
        Sector strength score in the range [0.0, 100.0].
    """
    _validate_symbol_info(symbol_info)

    sym_row = symbol_info.loc[symbol_info["symbol"] == symbol, "sector"]
    if sym_row.empty:
        log.debug("get_sector_score: symbol %r not found in symbol_info — returning neutral 50", symbol)
        return 50.0

    sector: str = sym_row.iloc[0]
    rank = sector_ranks.get(sector)

    if rank is None:
        log.debug(
            "get_sector_score: sector %r for symbol %r not in sector_ranks — returning neutral 50",
            sector, symbol,
        )
        return 50.0

    total_sectors = max(sector_ranks.values()) if sector_ranks else 1
    score = max(0.0, 100.0 * (1.0 - (rank - 1) / max(1, total_sectors - 1)))

    log.debug(
        "get_sector_score: symbol=%r sector=%r rank=%d/%d score=%.1f",
        symbol, sector, rank, total_sectors, score,
    )
    return score


def get_sector_score_bonus(
    symbol: str,
    sector_ranks: dict[str, int],
    symbol_info: pd.DataFrame,
    top_n: int = 5,
) -> int:
    """Legacy helper — kept for backward compatibility only.

    New code should use :func:`get_sector_score` instead.
    Always returns 0 now that sector strength is a full weighted component.
    """
    return 0


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _validate_symbol_info(symbol_info: pd.DataFrame) -> None:
    """Raise ValueError if required columns are missing."""
    required = {"symbol", "sector"}
    missing = required - set(symbol_info.columns)
    if missing:
        raise ValueError(
            f"symbol_info is missing required columns: {missing}"
        )
