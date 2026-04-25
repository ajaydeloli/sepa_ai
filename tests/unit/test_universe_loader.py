"""
tests/unit/test_universe_loader.py
------------------------------------
Unit tests for ingestion.universe_loader.

Covers:
* load_watchlist_file — CSV, JSON, TXT, XLSX
* load_watchlist_file — invalid-symbol filtering
* load_watchlist_file — empty file raises WatchlistParseError
* validate_symbol — valid and invalid inputs
* resolve_symbols — scope="watchlist" excludes universe
* resolve_symbols — scope="all" deduplicates, watchlist first
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from ingestion.universe_loader import (
    RunSymbols,
    load_watchlist_file,
    resolve_symbols,
    validate_symbol,
)
from utils.exceptions import WatchlistParseError

# ---------------------------------------------------------------------------
# Paths to committed fixtures
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SAMPLE_CSV = FIXTURES / "sample_watchlist.csv"
SAMPLE_JSON = FIXTURES / "sample_watchlist.json"

_EXPECTED_SYMBOLS = {
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "WIPRO", "BAJFINANCE", "ASIANPAINT", "MARUTI", "SBIN",
}

# ===========================================================================
# load_watchlist_file — CSV
# ===========================================================================

class TestLoadWatchlistCSV:
    def test_committed_fixture_csv(self):
        """Reads the committed sample_watchlist.csv correctly."""
        symbols = load_watchlist_file(SAMPLE_CSV)
        assert set(symbols) == _EXPECTED_SYMBOLS

    def test_tmp_csv_with_symbol_column(self, tmp_path):
        """Picks up the 'symbol' column when present."""
        f = tmp_path / "wl.csv"
        f.write_text("symbol,extra\nRELIANCE,foo\nTCS,bar\n")
        result = load_watchlist_file(f)
        assert result == ["RELIANCE", "TCS"]

    def test_tmp_csv_first_column_fallback(self, tmp_path):
        """Falls back to the first column when 'symbol' column is absent."""
        f = tmp_path / "wl.csv"
        f.write_text("ticker,sector\nINFY,IT\nWIPRO,IT\n")
        result = load_watchlist_file(f)
        assert result == ["INFY", "WIPRO"]

    def test_csv_uppercases_symbols(self, tmp_path):
        """Lower-case tickers in the file are uppercased."""
        f = tmp_path / "wl.csv"
        f.write_text("symbol\nreliance\ntcs\n")
        result = load_watchlist_file(f)
        assert result == ["RELIANCE", "TCS"]

    def test_empty_csv_raises(self, tmp_path):
        """Empty CSV (header only) raises WatchlistParseError."""
        f = tmp_path / "empty.csv"
        f.write_text("symbol\n")
        with pytest.raises(WatchlistParseError):
            load_watchlist_file(f)

    def test_completely_empty_file_raises(self, tmp_path):
        """Completely empty CSV raises WatchlistParseError."""
        f = tmp_path / "empty.csv"
        f.write_text("")
        with pytest.raises(WatchlistParseError):
            load_watchlist_file(f)


# ===========================================================================
# load_watchlist_file — JSON
# ===========================================================================

class TestLoadWatchlistJSON:
    def test_committed_fixture_json(self):
        """Reads the committed sample_watchlist.json correctly."""
        symbols = load_watchlist_file(SAMPLE_JSON)
        assert set(symbols) == _EXPECTED_SYMBOLS

    def test_tmp_json_list_format(self, tmp_path):
        """Accepts a bare JSON array."""
        f = tmp_path / "wl.json"
        f.write_text(json.dumps(["RELIANCE", "TCS"]))
        assert load_watchlist_file(f) == ["RELIANCE", "TCS"]

    def test_tmp_json_dict_format(self, tmp_path):
        """Accepts {\"symbols\": [...]} dict format."""
        f = tmp_path / "wl.json"
        f.write_text(json.dumps({"symbols": ["INFY", "HDFCBANK"]}))
        assert load_watchlist_file(f) == ["INFY", "HDFCBANK"]

    def test_json_empty_list_raises(self, tmp_path):
        """Empty JSON array raises WatchlistParseError."""
        f = tmp_path / "wl.json"
        f.write_text("[]")
        with pytest.raises(WatchlistParseError):
            load_watchlist_file(f)

    def test_json_invalid_structure_raises(self, tmp_path):
        """JSON dict without 'symbols' key raises WatchlistParseError."""
        f = tmp_path / "wl.json"
        f.write_text(json.dumps({"tickers": ["RELIANCE"]}))
        with pytest.raises(WatchlistParseError):
            load_watchlist_file(f)


# ===========================================================================
# load_watchlist_file — TXT
# ===========================================================================

class TestLoadWatchlistTXT:
    def test_basic_txt(self, tmp_path):
        """Reads one symbol per line."""
        f = tmp_path / "wl.txt"
        f.write_text("RELIANCE\nTCS\nINFY\n")
        assert load_watchlist_file(f) == ["RELIANCE", "TCS", "INFY"]

    def test_txt_skips_blank_and_comments(self, tmp_path):
        """Blank lines and # comments are skipped."""
        f = tmp_path / "wl.txt"
        f.write_text("RELIANCE\n# comment\n\nTCS\n")
        assert load_watchlist_file(f) == ["RELIANCE", "TCS"]

    def test_txt_strips_whitespace(self, tmp_path):
        """Leading/trailing whitespace is stripped."""
        f = tmp_path / "wl.txt"
        f.write_text("  RELIANCE  \n  TCS\n")
        assert load_watchlist_file(f) == ["RELIANCE", "TCS"]

    def test_empty_txt_raises(self, tmp_path):
        """File with only comments raises WatchlistParseError."""
        f = tmp_path / "wl.txt"
        f.write_text("# just a comment\n\n")
        with pytest.raises(WatchlistParseError):
            load_watchlist_file(f)


# ===========================================================================
# load_watchlist_file — invalid symbols mixed in
# ===========================================================================

class TestInvalidSymbolFiltering:
    def test_skips_symbol_with_space(self, tmp_path):
        """'RELI ANCE' (space) is skipped; valid symbols kept."""
        f = tmp_path / "wl.csv"
        f.write_text("symbol\nRELIANCE\nRELI ANCE\nTCS\n")
        result = load_watchlist_file(f)
        assert "RELIANCE" in result
        assert "TCS" in result
        assert "RELI ANCE" not in result

    def test_skips_pure_digits(self, tmp_path):
        """'123' (only digits but valid format) — actually valid per regex.
        Let's test a symbol that's clearly invalid: empty string."""
        f = tmp_path / "wl.csv"
        # mix of valid, empty-ish, and spacey
        f.write_text("symbol\nRELIANCE\n   \nTCS\nREL!IANCE\n")
        result = load_watchlist_file(f)
        assert set(result) == {"RELIANCE", "TCS"}

    def test_skips_lowercase(self, tmp_path):
        """Lowercase tickers that fail validation after stripping are skipped.

        Note: load_watchlist_file uppercases before validating, so
        'reliance' → 'RELIANCE' (valid).  We test a truly invalid symbol.
        """
        f = tmp_path / "wl.csv"
        f.write_text("symbol\nRELIANCE\nREL-IANCE\nTCS\n")
        result = load_watchlist_file(f)
        # REL-IANCE has a hyphen — invalid per strict [A-Z0-9]{1,20} rule
        assert "RELIANCE" in result
        assert "TCS" in result
        assert "REL-IANCE" not in result

    def test_all_invalid_raises(self, tmp_path):
        """File where every symbol is invalid raises WatchlistParseError."""
        f = tmp_path / "wl.csv"
        f.write_text("symbol\nREL IANCE\nBAD SYM\n")
        with pytest.raises(WatchlistParseError):
            load_watchlist_file(f)

    def test_empty_string_symbols_skipped(self, tmp_path):
        """Empty strings / whitespace-only entries are skipped."""
        f = tmp_path / "wl.json"
        f.write_text(json.dumps(["RELIANCE", "", "   ", "TCS"]))
        result = load_watchlist_file(f)
        assert result == ["RELIANCE", "TCS"]


# ===========================================================================
# validate_symbol
# ===========================================================================

class TestValidateSymbol:
    @pytest.mark.parametrize("sym", ["RELIANCE", "TCS", "INFY", "M2M", "A", "Z" * 20])
    def test_valid_symbols(self, sym):
        assert validate_symbol(sym) is True

    @pytest.mark.parametrize("sym", [
        "reliance",      # lowercase
        "REL IANCE",     # space
        "",              # empty
        "REL-IANCE",     # hyphen
        "REL&M",         # ampersand
        "A" * 21,        # too long (21 chars)
        123,             # not a string
        None,            # None
        "TCS!",          # special char
    ])
    def test_invalid_symbols(self, sym):
        assert validate_symbol(sym) is False


# ===========================================================================
# resolve_symbols
# ===========================================================================

def _make_mock_db(watchlist_symbols: list[str]):
    """Return a minimal mock SQLiteStore with a prepopulated watchlist."""
    db = MagicMock()
    db.get_watchlist.return_value = [{"symbol": s} for s in watchlist_symbols]
    return db


_UNIVERSE = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]
_WATCHLIST_DB = ["RELIANCE", "WIPRO", "BAJFINANCE"]


@pytest.fixture()
def mock_db():
    return _make_mock_db(_WATCHLIST_DB)


@pytest.fixture()
def config_dict():
    return {"universe": {"index": "nifty500"}}


class TestResolveSymbols:
    def _patch_universe(self, monkeypatch):
        monkeypatch.setattr(
            "ingestion.universe_loader.get_universe",
            lambda index="nifty500": _UNIVERSE,
            raising=False,
        )
        # Also patch via the module import path used inside resolve_symbols
        import ingestion.universe_loader as ul
        monkeypatch.setattr(ul, "_universe_for_test", _UNIVERSE, raising=False)

    # ── scope="watchlist" ────────────────────────────────────────────────

    def test_scope_watchlist_all_field_is_watchlist_only(
        self, mock_db, config_dict, monkeypatch
    ):
        """scope='watchlist' → .all contains only watchlist symbols."""
        with patch(
            "ingestion.universe_loader.get_universe", return_value=_UNIVERSE
        ):
            result = resolve_symbols(config_dict, mock_db, scope="watchlist")

        assert result.scope == "watchlist"
        assert set(result.all) == set(_WATCHLIST_DB)
        # Universe symbols not in DB watchlist must not appear in .all
        for sym in set(_UNIVERSE) - set(_WATCHLIST_DB):
            assert sym not in result.all

    def test_scope_watchlist_watchlist_field_populated(
        self, mock_db, config_dict
    ):
        with patch(
            "ingestion.universe_loader.get_universe", return_value=_UNIVERSE
        ):
            result = resolve_symbols(config_dict, mock_db, scope="watchlist")
        assert set(result.watchlist) == set(_WATCHLIST_DB)

    # ── scope="all" deduplication ────────────────────────────────────────

    def test_scope_all_no_duplicates(self, mock_db, config_dict):
        """scope='all' → .all has no duplicates."""
        with patch(
            "ingestion.universe_loader.get_universe", return_value=_UNIVERSE
        ):
            result = resolve_symbols(config_dict, mock_db, scope="all")

        assert len(result.all) == len(set(result.all)), "Duplicates found in .all"

    def test_scope_all_watchlist_symbols_first(self, mock_db, config_dict):
        """scope='all' → watchlist symbols appear before universe-only symbols."""
        with patch(
            "ingestion.universe_loader.get_universe", return_value=_UNIVERSE
        ):
            result = resolve_symbols(config_dict, mock_db, scope="all")

        # Every watchlist symbol must appear in .all
        for sym in _WATCHLIST_DB:
            assert sym in result.all

        # Watchlist symbols must come before the first universe-only symbol
        wl_set = set(_WATCHLIST_DB)
        universe_only = [s for s in _UNIVERSE if s not in wl_set]

        if universe_only:
            # The last watchlist symbol's index should be less than
            # any universe-only symbol's index.
            wl_indices = [result.all.index(s) for s in _WATCHLIST_DB if s in result.all]
            uni_indices = [result.all.index(s) for s in universe_only if s in result.all]
            assert max(wl_indices) < min(uni_indices)

    def test_scope_all_union_size(self, mock_db, config_dict):
        """scope='all' → .all size equals the union of watchlist ∪ universe."""
        with patch(
            "ingestion.universe_loader.get_universe", return_value=_UNIVERSE
        ):
            result = resolve_symbols(config_dict, mock_db, scope="all")

        expected_size = len(set(_WATCHLIST_DB) | set(_UNIVERSE))
        assert len(result.all) == expected_size

    # ── CLI overrides ────────────────────────────────────────────────────

    def test_cli_symbols_added_to_watchlist(self, mock_db, config_dict):
        """CLI symbols are merged into .watchlist."""
        with patch(
            "ingestion.universe_loader.get_universe", return_value=_UNIVERSE
        ):
            result = resolve_symbols(
                config_dict, mock_db,
                cli_symbols=["MARUTI", "ASIANPAINT"],
                scope="watchlist",
            )
        assert "MARUTI" in result.watchlist
        assert "ASIANPAINT" in result.watchlist

    def test_cli_file_symbols_loaded(self, mock_db, config_dict):
        """Symbols from a CLI watchlist file are merged into .watchlist."""
        with patch(
            "ingestion.universe_loader.get_universe", return_value=_UNIVERSE
        ):
            result = resolve_symbols(
                config_dict, mock_db,
                cli_watchlist_file=SAMPLE_JSON,
                scope="watchlist",
            )
        assert "TCS" in result.watchlist
        assert "HDFCBANK" in result.watchlist

    # ── scope="universe" ────────────────────────────────────────────────

    def test_scope_universe_excludes_watchlist_only_symbols(
        self, mock_db, config_dict
    ):
        """scope='universe' → .all contains only universe symbols."""
        with patch(
            "ingestion.universe_loader.get_universe", return_value=_UNIVERSE
        ):
            result = resolve_symbols(config_dict, mock_db, scope="universe")

        watchlist_only = set(_WATCHLIST_DB) - set(_UNIVERSE)
        for sym in watchlist_only:
            assert sym not in result.all
