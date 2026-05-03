"""
tests/unit/test_runner.py
--------------------------
Unit tests for pipeline/runner.py.

All external I/O is mocked:
  * ingestion.source_factory.get_source  → MockSource
  * ingestion.universe_loader.resolve_symbols → RunSymbols with 2 symbols
  * features.feature_store.needs_bootstrap / bootstrap / update
  * screener.pipeline.run_screen             → []
  * screener.results.persist_results
  * reports.daily_watchlist.generate_csv_report / generate_html_report
  * reports.chart_generator.generate_batch_charts
  * alerts.alert_deduplicator.should_alert / record_alert
  * alerts.telegram_alert.send_daily_watchlist / send_error_alert
  * storage.sqlite_store.SQLiteStore         → MagicMock (via _get_db patch)
  * storage.parquet_store.append_row
  * utils.trading_calendar.trading_days

Tests
-----
1. run_daily with a 2-symbol universe completes without error and returns a valid summary dict
2. Symbols with needs_bootstrap=True trigger bootstrap(), not update()
3. Individual symbol OHLCV append failure logs a warning and does not abort the run
4. Reports and alert failures (non-critical steps) do not abort the run
5. run_historical over a 3-trading-day range calls run_daily exactly 3 times
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from pipeline.context import RunContext
from pipeline.runner import run_daily, run_historical
from ingestion.universe_loader import RunSymbols

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_RUN_DATE = date(2025, 1, 15)

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
    "scheduler": {"run_time": "15:35", "timezone": "Asia/Kolkata"},
    "alerts": {
        "dedup_days": 3,
        "dedup_score_jump": 10,
        "telegram": {"enabled": False},
    },
    "scoring": {
        "setup_quality_thresholds": {"a_plus": 85, "a": 70, "b": 55, "c": 40}
    },
}


def _make_ctx(tmp_path: Path) -> RunContext:
    """Return a RunContext pointing at tmp_path for all data dirs."""
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
        dry_run=True,       # skip all writes by default for unit tests
    )


def _make_ohlcv(symbol: str) -> pd.DataFrame:
    """Return a minimal 5-row OHLCV DataFrame for a symbol."""
    idx = pd.bdate_range("2025-01-09", periods=5)
    return pd.DataFrame(
        {
            "open": [100.0] * 5,
            "high": [105.0] * 5,
            "low": [98.0] * 5,
            "close": [102.0] * 5,
            "volume": [1_000_000.0] * 5,
        },
        index=idx,
    )


def _mock_run_symbols() -> RunSymbols:
    return RunSymbols(
        watchlist=["RELIANCE"],
        universe=_SYMBOLS,
        all=_SYMBOLS,
        scope="all",
    )


# ---------------------------------------------------------------------------
# Shared patcher context manager (keeps individual tests lean)
# ---------------------------------------------------------------------------

class _StandardPatches:
    """
    Context manager that applies the full set of mocks needed by most tests.

    Usage::

        with _StandardPatches() as p:
            p.mock_source.fetch_universe_batch.return_value = {...}
            result = run_daily(ctx)
            assert p.mock_run_screen.called
    """

    def __init__(self) -> None:
        self._patchers: list = []
        self.mock_db: MagicMock = MagicMock()
        self.mock_source: MagicMock = MagicMock()
        self.mock_run_screen: MagicMock = MagicMock(return_value=[])
        self.mock_persist: MagicMock = MagicMock()
        self.mock_csv: MagicMock = MagicMock(return_value="/tmp/watchlist.csv")
        self.mock_html: MagicMock = MagicMock(return_value="/tmp/watchlist.html")
        self.mock_charts: MagicMock = MagicMock(return_value={})
        self.mock_should_alert: MagicMock = MagicMock(return_value=False)
        self.mock_record_alert: MagicMock = MagicMock()
        self.mock_send_wl: MagicMock = MagicMock(return_value=0)
        self.mock_send_err: MagicMock = MagicMock()
        self.mock_needs_bootstrap: MagicMock = MagicMock(return_value=False)
        self.mock_bootstrap: MagicMock = MagicMock()
        self.mock_update: MagicMock = MagicMock()
        self.mock_append_row: MagicMock = MagicMock()
        self.mock_resolve: MagicMock = MagicMock(return_value=_mock_run_symbols())
        self.mock_benchmark: MagicMock = MagicMock(return_value=pd.DataFrame())
        self.mock_symbol_info: MagicMock = MagicMock(
            return_value=pd.DataFrame({"symbol": _SYMBOLS, "sector": ["IT", "IT"]})
        )

    def __enter__(self) -> "_StandardPatches":
        _patch = lambda target, **kw: patch(target, **kw)  # noqa: E731

        pairs = [
            ("pipeline.runner._get_db",           self.mock_db,             True),
            ("pipeline.runner.source_factory.get_source", lambda _: self.mock_source, False),
            ("pipeline.runner.run_screen",         self.mock_run_screen,     False),
            ("pipeline.runner.persist_results",    self.mock_persist,        False),
            ("pipeline.runner.generate_csv_report", self.mock_csv,           False),
            ("pipeline.runner.generate_html_report", self.mock_html,         False),
            ("pipeline.runner.generate_batch_charts", self.mock_charts,      False),
            ("pipeline.runner.should_alert",       self.mock_should_alert,   False),
            ("pipeline.runner.record_alert",       self.mock_record_alert,   False),
            ("pipeline.runner.send_daily_watchlist", self.mock_send_wl,      False),
            ("pipeline.runner.send_error_alert",   self.mock_send_err,       False),
            ("pipeline.runner.needs_bootstrap",    self.mock_needs_bootstrap, False),
            ("pipeline.runner.bootstrap",          self.mock_bootstrap,      False),
            ("pipeline.runner.update",             self.mock_update,         False),
            ("pipeline.runner.append_row",         self.mock_append_row,     False),
            ("pipeline.runner.resolve_symbols",    self.mock_resolve,        False),
            ("pipeline.runner._load_benchmark",    self.mock_benchmark,      False),
            ("pipeline.runner._load_symbol_info",  self.mock_symbol_info,    False),
        ]

        for target, replacement, is_return_value in pairs:
            if is_return_value:
                # _get_db → return the mock_db MagicMock directly
                p = patch(target, return_value=replacement)
            else:
                p = patch(target, replacement)
            self._patchers.append(p)
            p.start()

        # Default: fetch_universe_batch returns OHLCV for both symbols
        self.mock_source.fetch_universe_batch.return_value = {
            sym: _make_ohlcv(sym) for sym in _SYMBOLS
        }

        return self

    def __exit__(self, *args: Any) -> None:
        for p in self._patchers:
            p.stop()


# ---------------------------------------------------------------------------
# Test 1: Happy-path — 2-symbol universe completes without error
# ---------------------------------------------------------------------------

def test_run_daily_happy_path_returns_summary(tmp_path: Path) -> None:
    """run_daily with a 2-symbol universe completes and returns a valid summary dict."""
    ctx = _make_ctx(tmp_path)

    with _StandardPatches() as p:
        result = run_daily(ctx)

    # Must always return a dict, never None
    assert isinstance(result, dict)

    # Required summary keys
    required_keys = {
        "run_date", "duration_sec", "universe_size",
        "passed_stage2", "a_plus", "a",
        "report_csv", "report_html", "alerts_sent",
    }
    assert required_keys.issubset(result.keys()), (
        f"Missing keys: {required_keys - result.keys()}"
    )

    assert result["run_date"] == str(_RUN_DATE)
    assert result["universe_size"] == len(_SYMBOLS)
    assert isinstance(result["duration_sec"], float)
    assert result["duration_sec"] >= 0

    # Screener was called once with the resolved universe
    p.mock_run_screen.assert_called_once()
    call_kwargs = p.mock_run_screen.call_args
    assert list(call_kwargs.kwargs.get("universe", call_kwargs.args[0] if call_kwargs.args else [])) == _SYMBOLS


# ---------------------------------------------------------------------------
# Test 2: Bootstrap vs update path based on needs_bootstrap()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bootstrap_flags, expected_bootstrap, expected_update", [
    # Both symbols need bootstrap
    ([True, True], 2, 0),
    # Both symbols use incremental update
    ([False, False], 0, 2),
    # Mixed: RELIANCE bootstraps, TCS updates
    ([True, False], 1, 1),
])
def test_bootstrap_vs_update_routing(
    tmp_path: Path,
    bootstrap_flags: list[bool],
    expected_bootstrap: int,
    expected_update: int,
) -> None:
    """
    When needs_bootstrap() returns True for a symbol, bootstrap() must be called
    (not update()). When it returns False, update() is called instead.
    """
    ctx = _make_ctx(tmp_path)
    # dry_run=True skips actual writes — flip to False so the feature path executes
    from dataclasses import replace
    ctx = replace(ctx, dry_run=False)

    with _StandardPatches() as p:
        # needs_bootstrap is called once per symbol; side_effect iterates the list
        p.mock_needs_bootstrap.side_effect = bootstrap_flags

        run_daily(ctx)

    assert p.mock_bootstrap.call_count == expected_bootstrap, (
        f"Expected bootstrap() called {expected_bootstrap}x, got {p.mock_bootstrap.call_count}"
    )
    assert p.mock_update.call_count == expected_update, (
        f"Expected update() called {expected_update}x, got {p.mock_update.call_count}"
    )

    # Verify the other function was NOT called for symbols that went the other way
    if expected_bootstrap == 2:
        p.mock_update.assert_not_called()
    if expected_update == 2:
        p.mock_bootstrap.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: Per-symbol OHLCV append failure is skipped, run continues
# ---------------------------------------------------------------------------

def test_individual_symbol_ohlcv_failure_continues(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    When append_row() raises for one symbol the runner logs a WARNING and
    continues processing the remaining symbols — it must not abort the run.
    """
    import logging

    ctx = _make_ctx(tmp_path)
    from dataclasses import replace
    ctx = replace(ctx, dry_run=False)

    with caplog.at_level(logging.WARNING, logger="pipeline.runner"):
        with _StandardPatches() as p:
            # First symbol (RELIANCE) raises; second (TCS) succeeds
            p.mock_append_row.side_effect = [RuntimeError("disk full"), None]
            result = run_daily(ctx)

    # Run must complete and return a summary — not raise
    assert isinstance(result, dict)
    assert result["run_date"] == str(_RUN_DATE)

    # A warning must have been logged for the failing symbol
    assert any(
        "RELIANCE" in r.message or "append_row" in r.message
        for r in caplog.records
        if r.levelno == logging.WARNING
    ), f"Expected a WARNING mentioning RELIANCE. Records: {[r.message for r in caplog.records]}"

    # The screener still ran (with whatever symbols were available)
    p.mock_run_screen.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: Non-critical steps (reports + alerts) failures do NOT abort the run
# ---------------------------------------------------------------------------

def test_non_critical_failures_do_not_abort(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Failures in steps 8–13 (CSV report, HTML report, charts, alert filter,
    Telegram send, record_alert) must NOT abort run_daily().  The summary
    dict must be returned and the screener must have been called.
    """
    import logging

    ctx = _make_ctx(tmp_path)
    from dataclasses import replace
    ctx = replace(ctx, dry_run=False)

    with caplog.at_level(logging.ERROR, logger="pipeline.runner"):
        with _StandardPatches() as p:
            # Blow up every non-critical step
            p.mock_csv.side_effect = OSError("disk full")
            p.mock_html.side_effect = OSError("disk full")
            p.mock_charts.side_effect = RuntimeError("matplotlib crash")
            p.mock_should_alert.side_effect = RuntimeError("DB gone")
            p.mock_send_wl.side_effect = ConnectionError("no internet")
            p.mock_record_alert.side_effect = RuntimeError("sqlite locked")

            result = run_daily(ctx)

    # Run must complete — never raise
    assert isinstance(result, dict)
    assert result["run_date"] == str(_RUN_DATE)

    # Critical steps still executed
    p.mock_run_screen.assert_called_once()
    p.mock_persist.assert_called_once()

    # Each non-critical failure must have produced an ERROR log record
    error_messages = " ".join(r.message for r in caplog.records if r.levelno == logging.ERROR)
    assert "Step 8" in error_messages or "CSV" in error_messages
    assert "Step 9" in error_messages or "HTML" in error_messages
    assert "Step 10" in error_messages or "chart" in error_messages.lower()


# ---------------------------------------------------------------------------
# Test 5: run_historical iterates over every trading day in the range
# ---------------------------------------------------------------------------

def test_run_historical_calls_run_daily_per_trading_day(tmp_path: Path) -> None:
    """
    run_historical over a 3-trading-day range must call run_daily exactly 3 times
    (assuming the mock trading_calendar returns 3 trading days).
    """
    from datetime import timedelta

    start = date(2025, 1, 13)   # Monday
    end   = date(2025, 1, 15)   # Wednesday → 3 trading days

    ctx = _make_ctx(tmp_path)

    # Build a 3-element DatetimeIndex mimicking get_trading_days return value
    mock_td = pd.DatetimeIndex([
        pd.Timestamp("2025-01-13"),
        pd.Timestamp("2025-01-14"),
        pd.Timestamp("2025-01-15"),
    ])

    with _StandardPatches():
        with patch("pipeline.runner.get_trading_days", return_value=mock_td) as mock_cal:
            with patch("pipeline.runner.run_daily", return_value={
                "run_date": "...",
                "duration_sec": 0.1,
                "universe_size": 2,
                "passed_stage2": 0,
                "a_plus": 0,
                "a": 0,
                "report_csv": "",
                "report_html": "",
                "alerts_sent": 0,
            }) as mock_rd:
                summaries = run_historical(ctx, start, end)

    # Trading calendar called with correct date strings
    mock_cal.assert_called_once_with("2025-01-13", "2025-01-15")

    # run_daily called once per trading day
    assert mock_rd.call_count == 3, (
        f"Expected run_daily called 3 times, got {mock_rd.call_count}"
    )

    # One summary per day
    assert len(summaries) == 3


def test_run_historical_continues_after_individual_day_failure(tmp_path: Path) -> None:
    """
    If run_daily raises for one day, run_historical must log the error,
    append an error-summary dict, and continue processing the remaining days.
    """
    import pandas as pd

    start = date(2025, 1, 13)
    end   = date(2025, 1, 15)

    ctx = _make_ctx(tmp_path)

    mock_td = pd.DatetimeIndex([
        pd.Timestamp("2025-01-13"),
        pd.Timestamp("2025-01-14"),
        pd.Timestamp("2025-01-15"),
    ])

    success_summary = {
        "run_date": "...", "duration_sec": 0.1,
        "universe_size": 2, "passed_stage2": 0,
        "a_plus": 0, "a": 0,
        "report_csv": "", "report_html": "", "alerts_sent": 0,
    }

    call_count = 0

    def _flaky_run_daily(day_ctx: RunContext) -> dict:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated critical failure on day 2")
        return {**success_summary, "run_date": str(day_ctx.run_date)}

    with _StandardPatches():
        with patch("pipeline.runner.get_trading_days", return_value=mock_td):
            with patch("pipeline.runner.run_daily", side_effect=_flaky_run_daily):
                summaries = run_historical(ctx, start, end)

    # All 3 days produced a summary dict
    assert len(summaries) == 3

    # The failed day has an "error" key
    failed = [s for s in summaries if "error" in s]
    assert len(failed) == 1
    assert "simulated critical failure" in failed[0]["error"]


# ---------------------------------------------------------------------------
# Test 6: run_daily with no symbols (empty universe) returns valid summary
# ---------------------------------------------------------------------------

def test_run_daily_empty_universe(tmp_path: Path) -> None:
    """An empty universe should produce a valid summary with universe_size=0."""
    ctx = _make_ctx(tmp_path)

    with _StandardPatches() as p:
        p.mock_resolve.return_value = RunSymbols(
            watchlist=[], universe=[], all=[], scope="all"
        )
        p.mock_source.fetch_universe_batch.return_value = {}

        result = run_daily(ctx)

    assert result["universe_size"] == 0
    assert result["passed_stage2"] == 0
    assert isinstance(result["duration_sec"], float)
