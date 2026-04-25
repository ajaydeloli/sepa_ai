"""
tests/unit/test_storage.py
--------------------------
Unit tests for the storage layer (Parquet helpers + SQLiteStore).

All tests are fully isolated: Parquet tests use tmp_path (pytest fixture),
SQLite tests spin up a fresh in-memory / temp-file database per test.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from storage.parquet_store import (
    append_row,
    get_last_date,
    read_last_n_rows,
    read_parquet,
    write_parquet,
)
from storage.sqlite_store import SQLiteStore
from utils.exceptions import FeatureStoreOutOfSyncError


# ===========================================================================
# Helpers
# ===========================================================================


def _make_row(d: date, value: float = 1.0) -> pd.DataFrame:
    """Return a one-row DataFrame indexed by *d*."""
    return pd.DataFrame({"value": [value]}, index=pd.Index([d], name="date"))


# ===========================================================================
# Parquet helpers
# ===========================================================================


class TestReadParquet:
    def test_returns_empty_df_when_missing(self, tmp_path: Path) -> None:
        result = read_parquet(tmp_path / "nonexistent.parquet")
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_round_trip(self, tmp_path: Path) -> None:
        p = tmp_path / "test.parquet"
        df = _make_row(date(2024, 1, 15))
        write_parquet(p, df)
        loaded = read_parquet(p)
        assert list(loaded["value"]) == [1.0]


class TestWriteParquet:
    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        p = tmp_path / "a" / "b" / "c.parquet"
        write_parquet(p, _make_row(date(2024, 1, 1)))
        assert p.exists()

    def test_atomic_replace(self, tmp_path: Path) -> None:
        """A second write should overwrite the first without error."""
        p = tmp_path / "data.parquet"
        write_parquet(p, _make_row(date(2024, 1, 1), value=10.0))
        write_parquet(p, _make_row(date(2024, 1, 2), value=20.0))
        df = read_parquet(p)
        assert len(df) == 1
        assert list(df["value"]) == [20.0]


class TestAppendRow:
    def test_creates_file_when_missing(self, tmp_path: Path) -> None:
        p = tmp_path / "features.parquet"
        assert not p.exists()
        append_row(p, _make_row(date(2024, 3, 1)))
        assert p.exists()
        df = read_parquet(p)
        assert len(df) == 1

    def test_appends_to_existing(self, tmp_path: Path) -> None:
        p = tmp_path / "features.parquet"
        append_row(p, _make_row(date(2024, 3, 1)))
        append_row(p, _make_row(date(2024, 3, 2)))
        df = read_parquet(p)
        assert len(df) == 2

    def test_raises_on_duplicate_date(self, tmp_path: Path) -> None:
        p = tmp_path / "features.parquet"
        d = date(2024, 3, 1)
        append_row(p, _make_row(d))
        with pytest.raises(FeatureStoreOutOfSyncError):
            append_row(p, _make_row(d))

    def test_duplicate_does_not_corrupt_file(self, tmp_path: Path) -> None:
        """Original data must survive a rejected duplicate write."""
        p = tmp_path / "features.parquet"
        d = date(2024, 3, 1)
        append_row(p, _make_row(d, value=99.0))
        with pytest.raises(FeatureStoreOutOfSyncError):
            append_row(p, _make_row(d, value=0.0))
        df = read_parquet(p)
        assert list(df["value"]) == [99.0]


class TestReadLastNRows:
    def test_returns_empty_when_missing(self, tmp_path: Path) -> None:
        result = read_last_n_rows(tmp_path / "missing.parquet", n=5)
        assert result.empty

    def test_returns_all_rows_when_fewer_than_n(self, tmp_path: Path) -> None:
        p = tmp_path / "small.parquet"
        df = pd.concat([_make_row(date(2024, 1, i)) for i in range(1, 4)])
        write_parquet(p, df)
        result = read_last_n_rows(p, n=10)
        assert len(result) == 3

    def test_returns_exactly_n_rows(self, tmp_path: Path) -> None:
        p = tmp_path / "data.parquet"
        df = pd.concat([_make_row(date(2024, 1, i)) for i in range(1, 11)])
        write_parquet(p, df)
        result = read_last_n_rows(p, n=3)
        assert len(result) == 3


class TestGetLastDate:
    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        assert get_last_date(tmp_path / "missing.parquet") is None

    def test_returns_latest_date(self, tmp_path: Path) -> None:
        p = tmp_path / "data.parquet"
        dates = [date(2024, 1, 1), date(2024, 1, 5), date(2024, 1, 3)]
        df = pd.concat([_make_row(d) for d in dates])
        # Sort so last written == last index
        df = df.sort_index()
        write_parquet(p, df)
        result = get_last_date(p)
        assert result == date(2024, 1, 5)


# ===========================================================================
# SQLiteStore
# ===========================================================================


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteStore:
    """Fresh SQLiteStore backed by a temp file for each test."""
    return SQLiteStore(tmp_path / "test.db")


class TestSQLiteStoreSchema:
    def test_tables_created_on_init(self, store: SQLiteStore) -> None:
        conn = sqlite3.connect(store._db_path)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert {"watchlist", "run_history", "screen_results", "alerts"} <= tables


class TestWatchlist:
    def test_add_and_get_round_trip(self, store: SQLiteStore) -> None:
        store.add_symbol("RELIANCE", note="flagship", added_via="cli")
        wl = store.get_watchlist()
        assert len(wl) == 1
        assert wl[0]["symbol"] == "RELIANCE"
        assert wl[0]["note"] == "flagship"

    def test_symbol_uppercased(self, store: SQLiteStore) -> None:
        store.add_symbol("infy")
        wl = store.get_watchlist()
        assert wl[0]["symbol"] == "INFY"

    def test_add_duplicate_updates_not_errors(self, store: SQLiteStore) -> None:
        store.add_symbol("TCS", note="first")
        store.add_symbol("TCS", note="updated")
        wl = store.get_watchlist()
        assert len(wl) == 1
        assert wl[0]["note"] == "updated"

    def test_remove_symbol(self, store: SQLiteStore) -> None:
        store.add_symbol("WIPRO")
        store.remove_symbol("WIPRO")
        assert store.get_watchlist() == []

    def test_remove_nonexistent_is_noop(self, store: SQLiteStore) -> None:
        store.remove_symbol("GHOST")  # must not raise

    def test_clear_watchlist(self, store: SQLiteStore) -> None:
        store.add_symbol("A")
        store.add_symbol("B")
        store.clear_watchlist()
        assert store.get_watchlist() == []

    def test_bulk_add(self, store: SQLiteStore) -> None:
        store.bulk_add(["HDFC", "ICICI", "AXIS"], added_via="import")
        wl = store.get_watchlist()
        assert len(wl) == 3

    def test_bulk_add_with_duplicates_does_not_raise(self, store: SQLiteStore) -> None:
        """Inserting the same symbol twice in bulk_add must not raise."""
        store.bulk_add(["TCS", "TCS", "INFY"], added_via="import")
        wl = store.get_watchlist()
        symbols = {r["symbol"] for r in wl}
        assert symbols == {"TCS", "INFY"}

    def test_bulk_add_existing_symbol_silently_skipped(self, store: SQLiteStore) -> None:
        store.add_symbol("TCS", note="original")
        store.bulk_add(["TCS", "INFY"])
        wl = store.get_watchlist()
        tcs_rows = [r for r in wl if r["symbol"] == "TCS"]
        assert tcs_rows[0]["note"] == "original"  # not overwritten


class TestScreenResults:
    def test_save_and_get_results(self, store: SQLiteStore) -> None:
        run_date = date(2024, 5, 1)
        result = {
            "symbol": "RELIANCE",
            "stage": 2,
            "score": 87.5,
            "setup_quality": "A+",
            "trend_template_pass": True,
            "vcp_qualified": True,
            "breakout_triggered": False,
            "rs_rating": 92,
            "entry_price": 2500.0,
            "stop_loss": 2400.0,
            "risk_pct": 4.0,
        }
        store.save_result(run_date, result)
        rows = store.get_results(run_date)
        assert len(rows) == 1
        assert rows[0]["symbol"] == "RELIANCE"
        assert rows[0]["score"] == 87.5

    def test_get_result_single(self, store: SQLiteStore) -> None:
        run_date = date(2024, 5, 1)
        store.save_result(run_date, {"symbol": "TCS", "score": 70.0})
        row = store.get_result("TCS", run_date)
        assert row is not None
        assert row["symbol"] == "TCS"

    def test_get_result_none_for_unknown(self, store: SQLiteStore) -> None:
        assert store.get_result("GHOST", date(2024, 5, 1)) is None

    def test_upsert_updates_existing(self, store: SQLiteStore) -> None:
        run_date = date(2024, 5, 1)
        store.save_result(run_date, {"symbol": "INFY", "score": 60.0})
        store.save_result(run_date, {"symbol": "INFY", "score": 75.0})
        row = store.get_result("INFY", run_date)
        assert row["score"] == 75.0


class TestAlerts:
    def test_get_last_alert_none_for_unknown_symbol(self, store: SQLiteStore) -> None:
        result = store.get_last_alert("UNKNOWN_SYMBOL_XYZ")
        assert result is None

    def test_save_and_retrieve_alert(self, store: SQLiteStore) -> None:
        store.save_alert(
            symbol="RELIANCE",
            alerted_date=date(2024, 4, 10),
            score=88.0,
            quality="A+",
            breakout_triggered=True,
            channel="telegram",
        )
        alert = store.get_last_alert("RELIANCE")
        assert alert is not None
        assert alert["symbol"] == "RELIANCE"
        assert alert["quality"] == "A+"
        assert alert["breakout_triggered"] == 1

    def test_get_last_alert_returns_most_recent(self, store: SQLiteStore) -> None:
        store.save_alert("TCS", date(2024, 1, 1), 70.0, "B", False, "email")
        store.save_alert("TCS", date(2024, 3, 1), 85.0, "A", True, "telegram")
        store.save_alert("TCS", date(2024, 2, 1), 75.0, "A-", False, "email")
        alert = store.get_last_alert("TCS")
        assert alert["alerted_date"] == "2024-03-01"

    def test_alert_symbol_uppercased(self, store: SQLiteStore) -> None:
        store.save_alert("wipro", date(2024, 4, 1), 65.0, "B", False, None)
        alert = store.get_last_alert("WIPRO")
        assert alert is not None


class TestRunHistory:
    def test_get_last_run_date_none_when_empty(self, store: SQLiteStore) -> None:
        assert store.get_last_run_date() is None

    def test_save_and_get_last_run_date(self, store: SQLiteStore) -> None:
        store.save_run(
            {
                "run_date": date(2024, 5, 10),
                "run_mode": "scheduled",
                "status": "success",
                "universe_size": 500,
                "passed_stage2": 45,
                "duration_sec": 12.3,
            }
        )
        last = store.get_last_run_date()
        assert last == date(2024, 5, 10)

    def test_get_last_run_date_returns_latest(self, store: SQLiteStore) -> None:
        for d in [date(2024, 5, 1), date(2024, 5, 10), date(2024, 4, 28)]:
            store.save_run({"run_date": d, "run_mode": "manual", "status": "success"})
        assert store.get_last_run_date() == date(2024, 5, 10)
