"""
tests/unit/test_alert_deduplicator.py
--------------------------------------
Unit tests for alerts/alert_deduplicator.py.

All tests use an isolated SQLiteStore backed by a pytest tmp_path so every
test starts with a clean, empty database.  No network calls, no external state.

Tests cover:
  1. No prior alert → should_alert returns True (condition 1)
  2. Alert from yesterday, dedup_days=3 → returns False (within window, no improvement)
  3. Alert from 5 days ago, dedup_days=3 → returns True (condition 2: outside window)
  4. Score jumped 12 points within window → returns True (condition 3)
  5. Quality improved A → A+ within window → returns True (condition 4)
  6. Breakout newly triggered within window → returns True (condition 5)
  7. record_alert persists a row; second call on same date does not duplicate
  8. Dedup state is read from SQLite, not an in-memory set (survives new store instance)
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from alerts.alert_deduplicator import QUALITY_RANK, record_alert, should_alert
from rules.scorer import SEPAResult
from storage.sqlite_store import SQLiteStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONFIG = {"alerts": {"dedup_days": 3, "dedup_score_jump": 10}}

_RUN_DATE = date(2024, 6, 10)


def _make_result(
    symbol: str = "TESTSTOCK",
    run_date: date = _RUN_DATE,
    score: int = 80,
    setup_quality: str = "A",
    breakout_triggered: bool = False,
) -> SEPAResult:
    """Return a minimal SEPAResult for deduplicator testing."""
    return SEPAResult(
        symbol=symbol,
        run_date=run_date,
        stage=2,
        stage_label="Advancing",
        stage_confidence=80,
        trend_template_pass=True,
        trend_template_details={},
        conditions_met=8,
        score=score,
        setup_quality=setup_quality,  # type: ignore[arg-type]
        breakout_triggered=breakout_triggered,
    )


def _seed_alert(
    db: SQLiteStore,
    symbol: str = "TESTSTOCK",
    alerted_date: date = _RUN_DATE - timedelta(days=1),
    score: float = 80.0,
    quality: str = "A",
    breakout_triggered: bool = False,
) -> None:
    """Insert one alert row directly via SQLiteStore."""
    db.save_alert(
        symbol=symbol,
        alerted_date=alerted_date,
        score=score,
        quality=quality,
        breakout_triggered=breakout_triggered,
        channel="test",
    )


@pytest.fixture()
def db(tmp_path: Path) -> SQLiteStore:
    """Fresh SQLiteStore backed by a temp-file DB for each test."""
    return SQLiteStore(tmp_path / "alerts_test.db")


# ---------------------------------------------------------------------------
# Test 1 — no prior alert → should alert
# ---------------------------------------------------------------------------

class TestNoPriorAlert:
    def test_returns_true_when_no_history(self, db: SQLiteStore) -> None:
        result = _make_result()
        assert should_alert(result, db, _CONFIG) is True


# ---------------------------------------------------------------------------
# Test 2 — alerted yesterday, dedup_days=3, no improvement → skip
# ---------------------------------------------------------------------------

class TestWithinDedupWindowNoImprovement:
    def test_returns_false_within_window(self, db: SQLiteStore) -> None:
        yesterday = _RUN_DATE - timedelta(days=1)
        _seed_alert(db, alerted_date=yesterday, score=80.0, quality="A",
                    breakout_triggered=False)
        result = _make_result(score=80, setup_quality="A", breakout_triggered=False)
        assert should_alert(result, db, _CONFIG) is False

    def test_exactly_at_boundary_minus_one_returns_false(self, db: SQLiteStore) -> None:
        """2 days ago is still inside the 3-day window."""
        two_days_ago = _RUN_DATE - timedelta(days=2)
        _seed_alert(db, alerted_date=two_days_ago, score=80.0, quality="A")
        result = _make_result(score=81, setup_quality="A")
        # score delta = 1 (below threshold=10), quality same → still False
        assert should_alert(result, db, _CONFIG) is False


# ---------------------------------------------------------------------------
# Test 3 — alerted 5 days ago, dedup_days=3 → outside window → re-alert
# ---------------------------------------------------------------------------

class TestOutsideDedupWindow:
    def test_returns_true_when_beyond_dedup_days(self, db: SQLiteStore) -> None:
        five_days_ago = _RUN_DATE - timedelta(days=5)
        _seed_alert(db, alerted_date=five_days_ago, score=80.0, quality="A")
        result = _make_result(score=80, setup_quality="A")
        assert should_alert(result, db, _CONFIG) is True

    def test_exactly_at_threshold_returns_true(self, db: SQLiteStore) -> None:
        """Exactly dedup_days (3) days ago → days_since == dedup_days → alert."""
        three_days_ago = _RUN_DATE - timedelta(days=3)
        _seed_alert(db, alerted_date=three_days_ago, score=80.0, quality="A")
        result = _make_result(score=80, setup_quality="A")
        assert should_alert(result, db, _CONFIG) is True


# ---------------------------------------------------------------------------
# Test 4 — score jumped >= 10 within window → re-alert (condition 3)
# ---------------------------------------------------------------------------

class TestScoreJump:
    def test_score_jump_triggers_alert(self, db: SQLiteStore) -> None:
        yesterday = _RUN_DATE - timedelta(days=1)
        _seed_alert(db, alerted_date=yesterday, score=70.0, quality="A")
        result = _make_result(score=82, setup_quality="A")  # delta = 12 ≥ 10
        assert should_alert(result, db, _CONFIG) is True

    def test_score_jump_below_threshold_does_not_trigger(self, db: SQLiteStore) -> None:
        yesterday = _RUN_DATE - timedelta(days=1)
        _seed_alert(db, alerted_date=yesterday, score=74.0, quality="A")
        result = _make_result(score=83, setup_quality="A")  # delta = 9 < 10
        assert should_alert(result, db, _CONFIG) is False

    def test_score_jump_exactly_at_threshold_triggers(self, db: SQLiteStore) -> None:
        yesterday = _RUN_DATE - timedelta(days=1)
        _seed_alert(db, alerted_date=yesterday, score=72.0, quality="A")
        result = _make_result(score=82, setup_quality="A")  # delta = 10 == threshold
        assert should_alert(result, db, _CONFIG) is True


# ---------------------------------------------------------------------------
# Test 5 — quality improved (A → A+) within window → re-alert (condition 4)
# ---------------------------------------------------------------------------

class TestQualityImprovement:
    def test_quality_upgrade_triggers_alert(self, db: SQLiteStore) -> None:
        yesterday = _RUN_DATE - timedelta(days=1)
        _seed_alert(db, alerted_date=yesterday, score=80.0, quality="A")
        result = _make_result(score=86, setup_quality="A+")  # A → A+
        assert should_alert(result, db, _CONFIG) is True

    def test_quality_upgrade_b_to_a_triggers_alert(self, db: SQLiteStore) -> None:
        yesterday = _RUN_DATE - timedelta(days=1)
        _seed_alert(db, alerted_date=yesterday, score=60.0, quality="B")
        result = _make_result(score=71, setup_quality="A")
        assert should_alert(result, db, _CONFIG) is True

    def test_same_quality_does_not_trigger(self, db: SQLiteStore) -> None:
        yesterday = _RUN_DATE - timedelta(days=1)
        _seed_alert(db, alerted_date=yesterday, score=80.0, quality="A+")
        result = _make_result(score=88, setup_quality="A+")  # same grade, delta=8
        assert should_alert(result, db, _CONFIG) is False

    def test_quality_downgrade_does_not_trigger(self, db: SQLiteStore) -> None:
        yesterday = _RUN_DATE - timedelta(days=1)
        _seed_alert(db, alerted_date=yesterday, score=90.0, quality="A+")
        result = _make_result(score=75, setup_quality="A")  # A+ → A (downgrade)
        assert should_alert(result, db, _CONFIG) is False

    def test_quality_rank_ordering(self) -> None:
        """Sanity-check the QUALITY_RANK constant."""
        assert QUALITY_RANK["FAIL"] < QUALITY_RANK["C"] < QUALITY_RANK["B"]
        assert QUALITY_RANK["B"] < QUALITY_RANK["A"] < QUALITY_RANK["A+"]


# ---------------------------------------------------------------------------
# Test 6 — breakout newly triggered within window → re-alert (condition 5)
# ---------------------------------------------------------------------------

class TestBreakoutNewlyTriggered:
    def test_new_breakout_triggers_alert(self, db: SQLiteStore) -> None:
        yesterday = _RUN_DATE - timedelta(days=1)
        _seed_alert(db, alerted_date=yesterday, score=80.0, quality="A",
                    breakout_triggered=False)
        result = _make_result(score=81, setup_quality="A", breakout_triggered=True)
        assert should_alert(result, db, _CONFIG) is True

    def test_breakout_already_true_does_not_trigger(self, db: SQLiteStore) -> None:
        yesterday = _RUN_DATE - timedelta(days=1)
        _seed_alert(db, alerted_date=yesterday, score=80.0, quality="A",
                    breakout_triggered=True)
        result = _make_result(score=81, setup_quality="A", breakout_triggered=True)
        # breakout was already True → condition 5 not met; score delta=1, same quality
        assert should_alert(result, db, _CONFIG) is False

    def test_no_breakout_when_previous_had_breakout(self, db: SQLiteStore) -> None:
        yesterday = _RUN_DATE - timedelta(days=1)
        _seed_alert(db, alerted_date=yesterday, score=80.0, quality="A",
                    breakout_triggered=True)
        result = _make_result(score=81, setup_quality="A", breakout_triggered=False)
        assert should_alert(result, db, _CONFIG) is False


# ---------------------------------------------------------------------------
# Test 7 — record_alert persists; second call same date is idempotent
# ---------------------------------------------------------------------------

class TestRecordAlert:
    def test_record_alert_persists_row(self, db: SQLiteStore) -> None:
        result = _make_result(score=85, setup_quality="A+")
        record_alert(result, db)
        alert = db.get_last_alert("TESTSTOCK")
        assert alert is not None
        assert float(alert["score"]) == 85.0
        assert alert["quality"] == "A+"

    def test_record_alert_idempotent_same_date(self, db: SQLiteStore) -> None:
        """Calling record_alert twice for the same (symbol, run_date) must not
        insert a duplicate row."""
        result = _make_result(score=85, setup_quality="A+")
        record_alert(result, db)
        record_alert(result, db)  # second call — must be silently skipped

        # Count rows directly in the alerts table.
        conn = sqlite3.connect(db._db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE symbol = 'TESTSTOCK'"
        ).fetchone()[0]
        conn.close()
        assert count == 1

    def test_record_alert_different_dates_inserts_both(self, db: SQLiteStore) -> None:
        """Each distinct run_date should produce its own row."""
        result_day1 = _make_result(run_date=date(2024, 6, 9), score=78)
        result_day2 = _make_result(run_date=date(2024, 6, 10), score=82)
        record_alert(result_day1, db)
        record_alert(result_day2, db)

        conn = sqlite3.connect(db._db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE symbol = 'TESTSTOCK'"
        ).fetchone()[0]
        conn.close()
        assert count == 2

    def test_record_alert_stores_breakout_flag(self, db: SQLiteStore) -> None:
        result = _make_result(breakout_triggered=True)
        record_alert(result, db)
        alert = db.get_last_alert("TESTSTOCK")
        assert bool(alert["breakout_triggered"]) is True


# ---------------------------------------------------------------------------
# Test 8 — dedup state is read from SQLite DB (survives new store instances)
# ---------------------------------------------------------------------------

class TestDeduplicationPersistence:
    def test_dedup_state_survives_new_store_instance(self, tmp_path: Path) -> None:
        """Alert recorded via one SQLiteStore instance must be visible to a
        second instance opened against the same file (simulates process restart)."""
        db_path = tmp_path / "persistent.db"

        # First process: record an alert for yesterday.
        db1 = SQLiteStore(db_path)
        yesterday = _RUN_DATE - timedelta(days=1)
        result = _make_result(run_date=_RUN_DATE, score=80)
        db1.save_alert(
            symbol="TESTSTOCK",
            alerted_date=yesterday,
            score=80.0,
            quality="A",
            breakout_triggered=False,
            channel="test",
        )

        # Second process: open a new store against the same file and check dedup.
        db2 = SQLiteStore(db_path)
        # Within dedup window, no improvement → should NOT alert
        assert should_alert(result, db2, _CONFIG) is False

    def test_no_history_in_fresh_db_always_alerts(self, tmp_path: Path) -> None:
        """Separate DB path → no history → should_alert returns True."""
        fresh_db = SQLiteStore(tmp_path / "fresh.db")
        result = _make_result()
        assert should_alert(result, fresh_db, _CONFIG) is True
