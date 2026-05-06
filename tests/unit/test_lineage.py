"""
tests/unit/test_lineage.py
--------------------------
Unit tests for data lineage tracking (run_history) and the _config_hash helper.

Coverage
--------
1. run_daily completion  → run_history table has exactly 1 row with the correct run_date
2. run_daily failure     → run_history row has status="error" and error_msg set
3. _config_hash          → identical configs produce the same hash
4. _config_hash          → any config change produces a different hash
5. Append-only guarantee → multiple run_daily calls accumulate rows; none are deleted

Design notes
------------
* Tests 1, 2, and 5 use a *real* SQLiteStore (not a MagicMock) so we can inspect
  the database after the run.  All other I/O (source, screener, alerts, reports,
  feature store, …) is mocked via _StandardPatches from test_runner.py helpers
  re-implemented inline here for isolation.
* The success write in Step 14 is guarded by ``not ctx.dry_run``, so tests that
  verify success rows must use dry_run=False.
* The failure write in the except block is *not* guarded by dry_run, so test 2
  works with either setting.
* The status written on failure is ``"error"`` (matching the runner implementation).
"""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from pipeline.context import RunContext
from pipeline.runner import _config_hash, run_daily
from ingestion.universe_loader import RunSymbols
from storage.sqlite_store import SQLiteStore

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_RUN_DATE = date(2025, 3, 10)
_SYMBOLS = ["RELIANCE", "TCS"]

_MINIMAL_CONFIG: dict = {
    "universe": {"source": "yfinance", "index": "nifty500"},
    "data": {
        "raw_dir": "data/raw",
        "processed_dir": "data/processed",
        "features_dir": "data/features",
        "fundamentals_dir": "data/fundamentals",
    },
    "watchlist": {"persist_path": "data/sepa_ai.db"},
    "alerts": {
        "dedup_days": 3,
        "dedup_score_jump": 10,
        "telegram": {"enabled": False},
    },
    "scoring": {
        "setup_quality_thresholds": {"a_plus": 85, "a": 70, "b": 55, "c": 40}
    },
}


def _make_ctx(tmp_path: Path, *, dry_run: bool = False) -> RunContext:
    config = dict(_MINIMAL_CONFIG)
    config["data"] = {
        "raw_dir": str(tmp_path / "raw"),
        "processed_dir": str(tmp_path / "processed"),
        "features_dir": str(tmp_path / "features"),
        "fundamentals_dir": str(tmp_path / "fundamentals"),
    }
    config["watchlist"] = {"persist_path": str(tmp_path / "test.db")}
    return RunContext(
        run_date=_RUN_DATE,
        mode="daily",
        config=config,
        scope="all",
        dry_run=dry_run,
    )


def _make_ohlcv() -> pd.DataFrame:
    idx = pd.bdate_range("2025-03-04", periods=5)
    return pd.DataFrame(
        {"open": [100.0] * 5, "high": [105.0] * 5,
         "low": [98.0] * 5, "close": [102.0] * 5,
         "volume": [1_000_000.0] * 5},
        index=idx,
    )


def _mock_run_symbols() -> RunSymbols:
    return RunSymbols(
        watchlist=["RELIANCE"], universe=_SYMBOLS, all=_SYMBOLS, scope="all"
    )


# ---------------------------------------------------------------------------
# Patch context manager (mirrors _StandardPatches in test_runner.py)
# but accepts an external real_db so we can inspect DB state post-run.
# ---------------------------------------------------------------------------

class _PatchesWithRealDB:
    """Apply all standard mocks but inject a real SQLiteStore for _get_db."""

    def __init__(self, real_db: SQLiteStore) -> None:
        self._real_db = real_db
        self._patchers: list = []
        self.mock_source = MagicMock()
        self.mock_run_screen = MagicMock(return_value=[])

    def __enter__(self) -> "_PatchesWithRealDB":
        self.mock_source.fetch_universe_batch.return_value = {
            sym: _make_ohlcv() for sym in _SYMBOLS
        }

        replacements = [
            ("pipeline.runner._get_db",              None,                       True),
            ("pipeline.runner.source_factory.get_source", lambda _: self.mock_source, False),
            ("pipeline.runner.run_screen",           self.mock_run_screen,        False),
            ("pipeline.runner.persist_results",      MagicMock(),                False),
            ("pipeline.runner.generate_csv_report",  MagicMock(return_value=""), False),
            ("pipeline.runner.generate_html_report", MagicMock(return_value=""), False),
            ("pipeline.runner.generate_batch_charts",MagicMock(return_value={}), False),
            ("pipeline.runner.should_alert",         MagicMock(return_value=False), False),
            ("pipeline.runner.record_alert",         MagicMock(),                False),
            ("pipeline.runner.send_daily_watchlist", MagicMock(return_value=0),  False),
            ("pipeline.runner.send_error_alert",     MagicMock(),                False),
            ("pipeline.runner.needs_bootstrap",      MagicMock(return_value=False), False),
            ("pipeline.runner.bootstrap",            MagicMock(),                False),
            ("pipeline.runner.update",               MagicMock(),                False),
            ("pipeline.runner.append_row",           MagicMock(),                False),
            ("pipeline.runner.resolve_symbols",      MagicMock(return_value=_mock_run_symbols()), False),
            ("pipeline.runner._load_benchmark",      MagicMock(return_value=pd.DataFrame()), False),
            ("pipeline.runner._load_symbol_info",
             MagicMock(return_value=pd.DataFrame({"symbol": _SYMBOLS, "sector": ["IT", "IT"]})),
             False),
        ]

        for target, replacement, is_return in replacements:
            if is_return and target == "pipeline.runner._get_db":
                p = patch(target, return_value=self._real_db)
            elif is_return:
                p = patch(target, return_value=replacement)
            else:
                p = patch(target, replacement)
            self._patchers.append(p)
            p.start()

        return self

    def __exit__(self, *args: Any) -> None:
        for p in self._patchers:
            p.stop()


def _read_run_history(db: SQLiteStore) -> list[dict]:
    """Return every row in run_history as a list of plain dicts."""
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM run_history ORDER BY id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


# ===========================================================================
# Test 1: Successful run_daily writes one run_history row with correct run_date
# ===========================================================================

def test_run_daily_success_writes_run_history_row(tmp_path: Path) -> None:
    """
    After a successful run_daily() the run_history table must contain exactly
    one row whose run_date matches the RunContext.run_date.
    """
    db = SQLiteStore(tmp_path / "test.db")
    ctx = _make_ctx(tmp_path, dry_run=False)

    with _PatchesWithRealDB(db):
        result = run_daily(ctx)

    assert isinstance(result, dict), "run_daily must return a dict"

    rows = _read_run_history(db)
    assert len(rows) == 1, f"Expected 1 run_history row, got {len(rows)}"

    row = rows[0]
    assert row["run_date"] == str(_RUN_DATE), (
        f"run_date mismatch: {row['run_date']!r} != {str(_RUN_DATE)!r}"
    )
    assert row["status"] == "success"
    assert row["error_msg"] is None
    assert row["run_mode"] == "daily"
    assert isinstance(row["duration_sec"], float)
    assert row["duration_sec"] >= 0


# ===========================================================================
# Test 2: Failed run_daily writes run_history row with status="error"
# ===========================================================================

def test_run_daily_failure_writes_error_row(tmp_path: Path) -> None:
    """
    When run_daily() raises during a critical step the run_history row must
    have status='error' and error_msg containing the exception message.
    The original exception must be re-raised to the caller.
    """
    db = SQLiteStore(tmp_path / "test.db")
    ctx = _make_ctx(tmp_path, dry_run=False)

    boom_message = "simulated screener crash"

    with _PatchesWithRealDB(db) as p:
        p.mock_run_screen.side_effect = RuntimeError(boom_message)
        with pytest.raises(RuntimeError, match=boom_message):
            run_daily(ctx)

    rows = _read_run_history(db)
    assert len(rows) == 1, f"Expected 1 run_history row even on failure, got {len(rows)}"

    row = rows[0]
    assert row["status"] == "error", f"Expected status='error', got {row['status']!r}"
    assert row["error_msg"] is not None, "error_msg must be set on failure"
    assert boom_message in row["error_msg"], (
        f"error_msg {row['error_msg']!r} does not contain {boom_message!r}"
    )


# ===========================================================================
# Tests 3 & 4: _config_hash determinism
# ===========================================================================

class TestConfigHash:
    """Pure-function tests for pipeline.runner._config_hash."""

    _BASE_CONFIG = {
        "universe": {"source": "yfinance", "index": "nifty500"},
        "screener": {"stage2_min_score": 70},
        "alerts": {"dedup_days": 3},
    }

    def test_identical_configs_produce_same_hash(self) -> None:
        """_config_hash must be deterministic: same config → same hash."""
        cfg_a = dict(self._BASE_CONFIG)
        cfg_b = dict(self._BASE_CONFIG)
        assert _config_hash(cfg_a) == _config_hash(cfg_b)

    def test_identical_configs_with_different_key_order(self) -> None:
        """Key insertion order must not affect the hash (sort_keys=True)."""
        cfg_a = {"a": 1, "b": 2, "c": 3}
        cfg_b = {"c": 3, "a": 1, "b": 2}
        assert _config_hash(cfg_a) == _config_hash(cfg_b)

    def test_changed_value_produces_different_hash(self) -> None:
        """Changing any value in the config must produce a different hash."""
        cfg_original = dict(self._BASE_CONFIG)
        cfg_changed = {**self._BASE_CONFIG, "screener": {"stage2_min_score": 99}}
        assert _config_hash(cfg_original) != _config_hash(cfg_changed)

    def test_added_key_produces_different_hash(self) -> None:
        """Adding a new key to the config must produce a different hash."""
        cfg_original = {"a": 1}
        cfg_with_extra = {"a": 1, "b": 2}
        assert _config_hash(cfg_original) != _config_hash(cfg_with_extra)

    def test_removed_key_produces_different_hash(self) -> None:
        """Removing a key from the config must produce a different hash."""
        cfg_full = {"a": 1, "b": 2}
        cfg_reduced = {"a": 1}
        assert _config_hash(cfg_full) != _config_hash(cfg_reduced)

    def test_returns_non_empty_string(self) -> None:
        """_config_hash must always return a non-empty string."""
        result = _config_hash({})
        assert isinstance(result, str) and len(result) > 0


# ===========================================================================
# Test 5: run_history is append-only — rows accumulate, never deleted
# ===========================================================================

def test_run_history_is_append_only(tmp_path: Path) -> None:
    """
    Calling run_daily() multiple times must accumulate rows in run_history.
    No prior rows should ever be removed: the table is a pure append log.
    """
    db = SQLiteStore(tmp_path / "test.db")
    n_runs = 3

    for i in range(n_runs):
        run_date_i = date(2025, 3, 10 + i)
        ctx = replace(_make_ctx(tmp_path, dry_run=False), run_date=run_date_i)
        with _PatchesWithRealDB(db):
            run_daily(ctx)

    rows = _read_run_history(db)

    assert len(rows) == n_runs, (
        f"Expected {n_runs} rows after {n_runs} runs, got {len(rows)}"
    )

    # All three distinct run_dates must be present
    recorded_dates = {r["run_date"] for r in rows}
    expected_dates = {str(date(2025, 3, 10 + i)) for i in range(n_runs)}
    assert recorded_dates == expected_dates, (
        f"Recorded dates {recorded_dates} != expected {expected_dates}"
    )

    # Every row must be a success
    for row in rows:
        assert row["status"] == "success", (
            f"Row for {row['run_date']} has unexpected status {row['status']!r}"
        )
