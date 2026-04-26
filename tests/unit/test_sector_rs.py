"""
tests/unit/test_sector_rs.py
------------------------------
Unit tests for features/sector_rs.py.

All tests are fully self-contained — no external fixtures or I/O.

Coverage
--------
1. Sector with highest median RS rating gets rank=1
2. get_sector_score_bonus returns 5 for a top-5 sector symbol
3. get_sector_score_bonus returns 0 for a bottom sector symbol
4. get_sector_score_bonus returns 0 for unknown symbol (no crash)
"""

from __future__ import annotations

import pandas as pd
import pytest

from features.sector_rs import compute_sector_ranks, get_sector_score_bonus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_symbol_info(rows: list[tuple[str, str]]) -> pd.DataFrame:
    """Build a minimal symbol_info DataFrame from (symbol, sector) tuples.

    Parameters
    ----------
    rows:
        List of (symbol, sector) pairs.

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: symbol, sector, industry,
        market_cap_cr, listing_date — matching the full schema so the
        function never complains about missing columns it doesn't use.
    """
    symbols = [r[0] for r in rows]
    sectors = [r[1] for r in rows]
    return pd.DataFrame(
        {
            "symbol": symbols,
            "sector": sectors,
            "industry": ["N/A"] * len(rows),
            "market_cap_cr": [10_000.0] * len(rows),
            "listing_date": ["2000-01-01"] * len(rows),
        }
    )


# ---------------------------------------------------------------------------
# Test 1: Sector with highest median RS rating gets rank=1
# ---------------------------------------------------------------------------


def test_highest_median_sector_gets_rank_1():
    """The sector whose members have the highest median RS rating should be rank 1."""
    # Three sectors with clearly separated RS ratings
    symbol_info = _make_symbol_info(
        [
            ("SYM_A1", "Technology"),
            ("SYM_A2", "Technology"),
            ("SYM_B1", "Finance"),
            ("SYM_B2", "Finance"),
            ("SYM_C1", "Energy"),
            ("SYM_C2", "Energy"),
        ]
    )

    ratings = {
        "SYM_A1": 90,
        "SYM_A2": 85,   # Technology median = 87.5  ← highest
        "SYM_B1": 60,
        "SYM_B2": 55,   # Finance median = 57.5
        "SYM_C1": 30,
        "SYM_C2": 25,   # Energy median = 27.5      ← lowest
    }

    ranks = compute_sector_ranks(ratings, symbol_info)

    assert ranks["Technology"] == 1, (
        f"Technology (highest median) should be rank 1, got {ranks['Technology']}"
    )
    assert ranks["Finance"] == 2, (
        f"Finance should be rank 2, got {ranks['Finance']}"
    )
    assert ranks["Energy"] == 3, (
        f"Energy (lowest median) should be rank 3, got {ranks['Energy']}"
    )


# ---------------------------------------------------------------------------
# Test 2: get_sector_score_bonus returns +5 for a top-5 sector symbol
# ---------------------------------------------------------------------------


def test_bonus_is_5_for_top_sector_symbol():
    """A symbol whose sector is in the top-5 should receive a bonus of 5."""
    symbol_info = _make_symbol_info(
        [
            ("RELIANCE", "Energy"),
            ("TCS", "Technology"),
            ("HDFCBANK", "Finance"),
            ("INFY", "Technology"),
            ("BHARTIARTL", "Telecom"),
            ("SBIN", "Finance"),
            ("WIPRO", "Technology"),
            ("NTPC", "Utilities"),
        ]
    )

    # Create ratings so Technology (median=85) ranks first, Utilities last
    ratings = {
        "RELIANCE": 70,       # Energy
        "TCS": 90,            # Technology
        "HDFCBANK": 75,       # Finance
        "INFY": 80,           # Technology — median Technology = 85
        "BHARTIARTL": 60,     # Telecom
        "SBIN": 65,           # Finance
        "WIPRO": 85,          # Technology
        "NTPC": 20,           # Utilities ← lowest
    }

    sector_ranks = compute_sector_ranks(ratings, symbol_info)

    # Technology should be rank 1 (median = 85)
    assert sector_ranks.get("Technology") == 1, (
        f"Technology should be rank 1, got {sector_ranks}"
    )

    # TCS is in Technology (rank 1) → bonus = 5
    bonus = get_sector_score_bonus("TCS", sector_ranks, symbol_info, top_n=5)
    assert bonus == 5, f"Expected bonus=5 for top-sector symbol, got {bonus}"


# ---------------------------------------------------------------------------
# Test 3: get_sector_score_bonus returns 0 for a bottom sector symbol
# ---------------------------------------------------------------------------


def test_bonus_is_0_for_bottom_sector_symbol():
    """A symbol in a sector ranked below top_n receives a bonus of 0."""
    # Six sectors so rank 6 is clearly outside top-5
    symbol_info = _make_symbol_info(
        [
            ("SYM1", "Sector_A"),
            ("SYM2", "Sector_B"),
            ("SYM3", "Sector_C"),
            ("SYM4", "Sector_D"),
            ("SYM5", "Sector_E"),
            ("SYM6", "Sector_F"),   # ← will be rank 6
        ]
    )

    ratings = {
        "SYM1": 90,  # Sector_A rank 1
        "SYM2": 80,  # Sector_B rank 2
        "SYM3": 70,  # Sector_C rank 3
        "SYM4": 60,  # Sector_D rank 4
        "SYM5": 50,  # Sector_E rank 5
        "SYM6": 10,  # Sector_F rank 6 ← outside top-5
    }

    sector_ranks = compute_sector_ranks(ratings, symbol_info)

    bonus = get_sector_score_bonus("SYM6", sector_ranks, symbol_info, top_n=5)
    assert bonus == 0, (
        f"Expected bonus=0 for bottom-sector symbol, got {bonus}"
    )


# ---------------------------------------------------------------------------
# Test 4: get_sector_score_bonus returns 0 for unknown symbol (no crash)
# ---------------------------------------------------------------------------


def test_bonus_returns_0_for_unknown_symbol_no_crash():
    """An unknown symbol should return 0 without raising any exception."""
    symbol_info = _make_symbol_info(
        [
            ("KNOWN_SYM", "Technology"),
        ]
    )
    sector_ranks = {"Technology": 1}

    # Should not raise; should return 0
    bonus = get_sector_score_bonus("GHOST_SYM", sector_ranks, symbol_info, top_n=5)
    assert bonus == 0, (
        f"Expected bonus=0 for unknown symbol, got {bonus}"
    )


# ---------------------------------------------------------------------------
# Additional: empty ratings → empty sector ranks (no crash)
# ---------------------------------------------------------------------------


def test_empty_ratings_returns_empty_dict():
    """compute_sector_ranks with empty ratings should return {}."""
    symbol_info = _make_symbol_info([("SYM1", "Technology")])
    result = compute_sector_ranks({}, symbol_info)
    assert result == {}


# ---------------------------------------------------------------------------
# Additional: symbol_info missing required columns raises ValueError
# ---------------------------------------------------------------------------


def test_missing_column_raises_value_error():
    """symbol_info without 'sector' column must raise ValueError."""
    bad_info = pd.DataFrame({"symbol": ["SYM1"], "industry": ["Tech"]})

    with pytest.raises(ValueError, match="sector"):
        compute_sector_ranks({"SYM1": 80}, bad_info)


# ---------------------------------------------------------------------------
# Additional: tied sectors get the same rank
# ---------------------------------------------------------------------------


def test_tied_sectors_get_same_rank():
    """Two sectors with equal median RS rating should share the same rank."""
    symbol_info = _make_symbol_info(
        [
            ("SYM_A", "Alpha"),
            ("SYM_B", "Beta"),
            ("SYM_C", "Gamma"),
        ]
    )
    # Alpha and Beta both have median = 80, Gamma = 50
    ratings = {
        "SYM_A": 80,   # Alpha median = 80
        "SYM_B": 80,   # Beta  median = 80
        "SYM_C": 50,   # Gamma median = 50
    }

    ranks = compute_sector_ranks(ratings, symbol_info)

    assert ranks["Alpha"] == ranks["Beta"], (
        f"Tied sectors should share the same rank: Alpha={ranks['Alpha']}, Beta={ranks['Beta']}"
    )
    assert ranks["Gamma"] > ranks["Alpha"], (
        "Lower-median sector (Gamma) should have a worse (higher) rank number"
    )
