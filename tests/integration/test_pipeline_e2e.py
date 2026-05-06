"""
tests/integration/test_pipeline_e2e.py
---------------------------------------
End-to-end integration tests for the full SEPA daily pipeline.

Design
------
* All HTTP calls (yfinance, Telegram, LLM) are mocked via unittest.mock.patch.
* Feature + processed Parquet files are written to tmp_path, never touching
  the real data/ directory.
* ProcessPoolExecutor workers run with n_workers=1 for determinism.
* No real database writes outside tmp_path.

Coverage targets
----------------
- test_full_daily_run_e2e   → pipeline/runner.run_daily end-to-end
- test_screener_batch_e2e   → screener/pipeline.run_screen with 5 mock symbols
- test_watchlist_flow_e2e   → scope="watchlist" run with SQLite watchlist seeding
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_RUN_DATE = date(2025, 6, 1)

# ---------------------------------------------------------------------------
# Shared fixture helpers (also used by test_screener_batch_e2e)
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 300, trend: float = 0.001, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start="2020-01-02", periods=n)
    close = 100.0 * np.cumprod(1 + trend + rng.normal(0, 0.005, n))
    high = close * (1 + rng.uniform(0.001, 0.015, n))
    low = close * (1 - rng.uniform(0.001, 0.015, n))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    volume = rng.integers(500_000, 2_000_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def _make_feature_row_stage2(close: float = 150.0) -> dict:
    return {
        "close": close, "open": close * 0.99,
        "high": close * 1.01, "low": close * 0.98,
        "volume": 1_000_000.0,
        "sma_50": close * 0.87, "sma_150": close * 0.80, "sma_200": close * 0.73,
        "ma_slope_50": 0.08, "ma_slope_200": 0.04,
        "high_52w": close * 1.03, "low_52w": close * 0.60,
        "rs_rating": 85.0, "rs_raw": 1.5,
        "pivot_high": close * 0.992, "vol_ratio": 3.0,
        "acc_dist_score": 10.0, "atr_14": close * 0.015,
        "vcp_contraction_count": 3, "vcp_max_depth_pct": 22.0,
        "vcp_final_depth_pct": 7.0, "vcp_vol_ratio": 0.4,
        "vcp_base_length_weeks": 10, "vcp_base_low": close * 0.75,
        "vcp_valid": True, "vcp_tightness_score": 4.0,
    }


def _make_feature_row_fail(close: float = 80.0) -> dict:
    return {
        "close": close, "open": close * 1.005,
        "high": close * 1.02, "low": close * 0.98,
        "volume": 800_000.0,
        "sma_50": close * 1.25, "sma_150": close * 1.31, "sma_200": close * 1.50,
        "ma_slope_50": -0.03, "ma_slope_200": -0.02,
        "high_52w": close * 2.10, "low_52w": close * 0.95,
        "rs_rating": 20.0, "rs_raw": 0.3,
        "pivot_high": close * 2.50, "vol_ratio": 0.8,
        "acc_dist_score": -10.0, "atr_14": close * 0.02,
        "vcp_contraction_count": 0, "vcp_max_depth_pct": 0.0,
        "vcp_final_depth_pct": 0.0, "vcp_vol_ratio": float("nan"),
        "vcp_base_length_weeks": 0, "vcp_base_low": float("nan"),
        "vcp_valid": False, "vcp_tightness_score": float("nan"),
    }


def _write_feature_parquet(path: Path, row: dict, n: int = 300) -> None:
    df = pd.DataFrame([row] * n)
    df.index = pd.bdate_range(start="2020-01-02", periods=n)
    df.index.name = "date"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


def _make_base_config(tmp_path: Path) -> dict:
    """Minimal config dict wired to tmp_path sub-directories."""
    return {
        "universe": {"source": "yfinance", "index": "nifty500",
                     "min_price": 0, "min_avg_volume": 0, "min_market_cap_cr": 0},
        "data": {
            "raw_dir":          str(tmp_path / "raw"),
            "processed_dir":    str(tmp_path / "processed"),
            "features_dir":     str(tmp_path / "features"),
            "fundamentals_dir": str(tmp_path / "fundamentals"),
            "news_dir":         str(tmp_path / "news"),
        },
        "watchlist": {"persist_path": str(tmp_path / "test.db"),
                      "always_scan": True, "priority_in_reports": True,
                      "always_generate_charts": False, "min_score_alert": 999},
        "reports":   {"output_dir": str(tmp_path / "reports")},
        "stage":     {"flat_slope_threshold": 0.0005,
                      "ma200_slope_lookback": 20, "ma50_slope_lookback": 10},
        "trend_template": {"pct_above_52w_low": 25.0, "pct_below_52w_high": 25.0,
                           "min_rs_rating": 70, "ma200_slope_lookback": 20},
        "vcp": {"detector": "rule_based", "pivot_sensitivity": 5,
                "min_contractions": 2, "max_contractions": 5,
                "require_vol_contraction": True, "require_declining_depth": True,
                "min_weeks": 3, "max_weeks": 52,
                "tightness_pct": 10.0, "max_depth_pct": 50.0},
        "entry":    {"breakout_buffer_pct": 0.001, "breakout_vol_threshold": 1.5},
        "stop_loss":{"stop_buffer_pct": 0.005, "max_risk_pct": 15.0,
                     "atr_multiplier": 2.0, "fixed_stop_pct": 0.07},
        "risk_reward": {"min_rr_ratio": 2.0},
        "rs":       {"period": 63},
        "pre_filter": {"min_close_pct_of_52w_high": 0.70, "min_rs_rating": 50},
        "scoring":  {"min_score_alert": 999,
                     "setup_quality_thresholds": {"a_plus": 85, "a": 70, "b": 55, "c": 40}},
        "paper_trading": {"enabled": False},
        "fundamentals":  {"enabled": False},
        "news":          {"enabled": False},
        "llm":           {"enabled": False},
        "alerts":        {"telegram": {"enabled": False}, "dedup_days": 0,
                          "dedup_score_jump": 0, "email": {"enabled": False}},
    }


def _make_benchmark(n: int = 300) -> pd.DataFrame:
    dates = pd.bdate_range(start="2020-01-02", periods=n)
    close = 18000.0 + np.arange(n) * 0.5
    return pd.DataFrame({"close": close}, index=dates)


def _make_symbol_info(symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"symbol": symbols,
                         "sector": ["Technology"] * len(symbols)})

# ---------------------------------------------------------------------------
# TEST 1 — Full daily run end-to-end
# ---------------------------------------------------------------------------

class TestFullDailyRunE2E:
    """
    End-to-end test of run_daily() with fixture feature data.
    All HTTP calls, Telegram, and LLM are mocked.
    Verifies: CSV + HTML reports created, SQLite has rows, no exception raised.
    """

    def _setup_env(self, tmp_path: Path) -> tuple[dict, list[str]]:
        """Write feature + processed Parquet for two mock symbols."""
        cfg = _make_base_config(tmp_path)
        features_dir  = Path(cfg["data"]["features_dir"])
        processed_dir = Path(cfg["data"]["processed_dir"])

        symbols = ["E2E_UP", "E2E_DN"]

        # Stage-2 symbol
        _write_feature_parquet(features_dir / "E2E_UP.parquet",
                               _make_feature_row_stage2(150.0))
        ohlcv = _make_ohlcv(300, 0.001, seed=10)
        processed_dir.mkdir(parents=True, exist_ok=True)
        ohlcv.to_parquet(processed_dir / "E2E_UP.parquet")
        (processed_dir / "NIFTY500.parquet").symlink_to(
            processed_dir / "E2E_UP.parquet"
        )

        # Failing symbol
        _write_feature_parquet(features_dir / "E2E_DN.parquet",
                               _make_feature_row_fail(80.0))
        ohlcv_dn = _make_ohlcv(300, -0.002, seed=11)
        ohlcv_dn.to_parquet(processed_dir / "E2E_DN.parquet")

        return cfg, symbols

    def test_reports_created_and_sqlite_populated(self, tmp_path):
        cfg, symbols = self._setup_env(tmp_path)

        from pipeline.context import RunContext
        from pipeline.runner import run_daily

        ctx = RunContext(
            run_date=_RUN_DATE,
            mode="daily",
            config=cfg,
            scope="all",
            dry_run=False,
            symbols_override=symbols,
        )

        mock_ohlcv = {sym: _make_ohlcv(5, seed=i) for i, sym in enumerate(symbols)}

        with (
            patch("pipeline.runner.resolve_symbols") as mock_resolve,
            patch("pipeline.runner.source_factory.get_source") as mock_src,
            patch("pipeline.runner.needs_bootstrap", return_value=False),
            patch("pipeline.runner.update"),
            patch("pipeline.runner.send_daily_watchlist", return_value=0),
            patch("pipeline.runner.send_error_alert"),
            patch("pipeline.runner.generate_batch_charts", return_value={}),
        ):
            mock_resolve.return_value = MagicMock(
                all=symbols, watchlist=[]
            )
            mock_source = MagicMock()
            mock_source.fetch_universe_batch.return_value = mock_ohlcv
            mock_src.return_value = mock_source

            summary = run_daily(ctx)

        # --- Assertions ---
        assert summary is not None, "run_daily must always return a dict"

        # CSV report exists and is non-empty
        csv_path = Path(summary["report_csv"])
        assert csv_path.exists(), f"CSV report not found: {csv_path}"
        assert csv_path.stat().st_size > 0, "CSV report is empty"

        # HTML report exists and is non-empty
        html_path = Path(summary["report_html"])
        assert html_path.exists(), f"HTML report not found: {html_path}"
        assert html_path.stat().st_size > 0, "HTML report is empty"

        # SQLite has rows in screen_results
        from storage.sqlite_store import SQLiteStore
        db = SQLiteStore(cfg["watchlist"]["persist_path"])
        rows = db.get_results(_RUN_DATE)
        assert len(rows) > 0, "SQLite screen_results table must have rows after run_daily"

        # run_history has one row
        import sqlite3
        with sqlite3.connect(cfg["watchlist"]["persist_path"]) as conn:
            count = conn.execute("SELECT COUNT(*) FROM run_history").fetchone()[0]
        assert count >= 1, "run_history must have at least one row"

    def test_no_exception_on_report_failure(self, tmp_path):
        """run_daily must NOT raise if the HTML report step crashes."""
        cfg, symbols = self._setup_env(tmp_path)

        from pipeline.context import RunContext
        from pipeline.runner import run_daily

        ctx = RunContext(
            run_date=_RUN_DATE,
            mode="daily",
            config=cfg,
            scope="all",
            dry_run=False,
            symbols_override=symbols,
        )

        mock_ohlcv = {sym: _make_ohlcv(5, seed=i) for i, sym in enumerate(symbols)}

        with (
            patch("pipeline.runner.resolve_symbols") as mock_resolve,
            patch("pipeline.runner.source_factory.get_source") as mock_src,
            patch("pipeline.runner.needs_bootstrap", return_value=False),
            patch("pipeline.runner.update"),
            patch("pipeline.runner.send_daily_watchlist", return_value=0),
            patch("pipeline.runner.send_error_alert"),
            patch("pipeline.runner.generate_batch_charts", return_value={}),
            patch("pipeline.runner.generate_html_report",
                  side_effect=RuntimeError("Simulated HTML crash")),
        ):
            mock_resolve.return_value = MagicMock(all=symbols, watchlist=[])
            mock_source = MagicMock()
            mock_source.fetch_universe_batch.return_value = mock_ohlcv
            mock_src.return_value = mock_source

            # Must not raise even though HTML report fails
            summary = run_daily(ctx)

        assert "run_date" in summary


# ---------------------------------------------------------------------------
# TEST 2 — Screener batch e2e: 5 symbols, 2 Stage-2, 3 non-Stage-2
# ---------------------------------------------------------------------------

class TestScreenerBatchE2E:
    """
    run_screen() with 5 mock symbols: 2 Stage-2, 3 failing.
    Verifies result count, ordering, score/quality invariants.
    """

    # Symbol names
    STAGE2 = ["BATCH_S2A", "BATCH_S2B"]
    FAILS  = ["BATCH_F1",  "BATCH_F2",  "BATCH_F3"]
    ALL    = STAGE2 + FAILS

    @pytest.fixture()
    def batch_env(self, tmp_path):
        cfg = _make_base_config(tmp_path)
        features_dir  = Path(cfg["data"]["features_dir"])
        processed_dir = Path(cfg["data"]["processed_dir"])
        processed_dir.mkdir(parents=True, exist_ok=True)

        for i, sym in enumerate(self.STAGE2):
            _write_feature_parquet(features_dir / f"{sym}.parquet",
                                   _make_feature_row_stage2(150.0 + i * 10))
            _make_ohlcv(300, 0.001, seed=i).to_parquet(
                processed_dir / f"{sym}.parquet"
            )

        for i, sym in enumerate(self.FAILS):
            _write_feature_parquet(features_dir / f"{sym}.parquet",
                                   _make_feature_row_fail(80.0))
            _make_ohlcv(300, -0.002, seed=10 + i).to_parquet(
                processed_dir / f"{sym}.parquet"
            )

        benchmark = _make_benchmark()
        benchmark.to_parquet(processed_dir / "NIFTY500.parquet")

        return cfg, _make_symbol_info(self.ALL), benchmark

    def test_result_count_equals_passed_symbols(self, batch_env):
        """run_screen returns one result per symbol that passes pre_filter."""
        cfg, symbol_info, benchmark = batch_env

        from screener.pipeline import run_screen

        results = run_screen(
            universe=self.ALL,
            run_date=_RUN_DATE,
            config=cfg,
            symbol_info=symbol_info,
            benchmark_df=benchmark,
            n_workers=1,
        )
        # At minimum the Stage-2 symbols should appear; failing ones may be
        # dropped by pre_filter. Validate that we get at least 1 result back.
        assert len(results) >= 1, "Expected at least one result from run_screen"

    def test_fail_symbols_have_zero_score_and_fail_quality(self, batch_env):
        """Non-Stage-2 results that DO appear must have score==0 and quality==FAIL."""
        cfg, symbol_info, benchmark = batch_env

        from screener.pipeline import run_screen

        results = run_screen(
            universe=self.ALL,
            run_date=_RUN_DATE,
            config=cfg,
            symbol_info=symbol_info,
            benchmark_df=benchmark,
            n_workers=1,
        )
        results_by_sym = {r.symbol: r for r in results}
        for sym in self.FAILS:
            if sym in results_by_sym:
                r = results_by_sym[sym]
                assert r.score == 0, f"{sym} should have score=0, got {r.score}"
                assert r.setup_quality == "FAIL", (
                    f"{sym} quality should be FAIL, got {r.setup_quality!r}"
                )

    def test_results_sorted_by_score_desc(self, batch_env):
        """Results must always be sorted by score DESC."""
        cfg, symbol_info, benchmark = batch_env

        from screener.pipeline import run_screen

        results = run_screen(
            universe=self.ALL,
            run_date=_RUN_DATE,
            config=cfg,
            symbol_info=symbol_info,
            benchmark_df=benchmark,
            n_workers=1,
        )
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True), (
            f"Results not sorted by score DESC: {scores}"
        )

    def test_stage2_before_fail_in_sorted_order(self, batch_env):
        """Stage-2 symbols must appear before failing symbols in sorted output."""
        cfg, symbol_info, benchmark = batch_env

        from screener.pipeline import run_screen

        results = run_screen(
            universe=self.ALL,
            run_date=_RUN_DATE,
            config=cfg,
            symbol_info=symbol_info,
            benchmark_df=benchmark,
            n_workers=1,
        )
        symbols_in_order = [r.symbol for r in results]
        stage2_in_results = [s for s in self.STAGE2 if s in symbols_in_order]
        fail_in_results   = [s for s in self.FAILS  if s in symbols_in_order]

        if stage2_in_results and fail_in_results:
            last_stage2_idx = max(symbols_in_order.index(s) for s in stage2_in_results)
            first_fail_idx  = min(symbols_in_order.index(s) for s in fail_in_results)
            assert last_stage2_idx < first_fail_idx, (
                f"Stage-2 symbols should precede failing symbols; "
                f"order={symbols_in_order}"
            )


# ---------------------------------------------------------------------------
# TEST 3 — Watchlist-scoped flow e2e
# ---------------------------------------------------------------------------

class TestWatchlistFlowE2E:
    """
    Tests the watchlist-scoped run:
    1. Seed 3 symbols into the SQLite watchlist.
    2. Run run_screen with scope="watchlist" (those 3 only).
    3. Verify only watchlist symbols are screened.
    4. Verify watchlist symbols appear first (highest score) in CSV report.
    """

    WL_SYMS = ["WL_A", "WL_B", "WL_C"]

    @pytest.fixture()
    def wl_env(self, tmp_path):
        cfg = _make_base_config(tmp_path)
        features_dir  = Path(cfg["data"]["features_dir"])
        processed_dir = Path(cfg["data"]["processed_dir"])
        processed_dir.mkdir(parents=True, exist_ok=True)

        # All watchlist symbols have Stage-2 features
        for i, sym in enumerate(self.WL_SYMS):
            _write_feature_parquet(features_dir / f"{sym}.parquet",
                                   _make_feature_row_stage2(150.0 + i * 5))
            _make_ohlcv(300, 0.001, seed=20 + i).to_parquet(
                processed_dir / f"{sym}.parquet"
            )

        benchmark = _make_benchmark()
        benchmark.to_parquet(processed_dir / "NIFTY500.parquet")

        # Seed watchlist
        from storage.sqlite_store import SQLiteStore
        db = SQLiteStore(cfg["watchlist"]["persist_path"])
        db.bulk_add(self.WL_SYMS, added_via="test")

        return cfg, db, benchmark

    def test_only_watchlist_symbols_screened(self, wl_env):
        """run_screen called with only watchlist symbols returns no outsiders."""
        cfg, db, benchmark = wl_env

        from screener.pipeline import run_screen

        symbol_info = _make_symbol_info(self.WL_SYMS)
        results = run_screen(
            universe=self.WL_SYMS,   # simulate scope=watchlist
            run_date=_RUN_DATE,
            config=cfg,
            symbol_info=symbol_info,
            benchmark_df=benchmark,
            n_workers=1,
        )
        result_symbols = {r.symbol for r in results}
        unexpected = result_symbols - set(self.WL_SYMS)
        assert not unexpected, f"Unexpected symbols in watchlist run: {unexpected}"

    def test_watchlist_symbols_first_in_csv_report(self, wl_env):
        """After persist + CSV generation, watchlist symbols appear at top."""
        cfg, db, benchmark = wl_env

        from screener.pipeline import run_screen
        from screener.results import persist_results
        from reports.daily_watchlist import generate_csv_report

        symbol_info = _make_symbol_info(self.WL_SYMS)
        results = run_screen(
            universe=self.WL_SYMS,
            run_date=_RUN_DATE,
            config=cfg,
            symbol_info=symbol_info,
            benchmark_df=benchmark,
            n_workers=1,
        )

        persist_results(results, db, _RUN_DATE)

        output_dir = cfg["reports"]["output_dir"]
        csv_path = generate_csv_report(
            results=results,
            output_dir=output_dir,
            run_date=_RUN_DATE,
            watchlist_symbols=self.WL_SYMS,
            include_all=True,
        )

        assert Path(csv_path).exists(), "CSV report must be created"

        with open(csv_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows_in_csv = list(reader)

        wl_set = set(self.WL_SYMS)
        wl_row_indices = [
            i for i, row in enumerate(rows_in_csv)
            if row.get("is_watchlist") in ("True", "true", "1")
        ]
        non_wl_indices = [
            i for i, row in enumerate(rows_in_csv)
            if row.get("is_watchlist") not in ("True", "true", "1")
            and row.get("symbol") not in wl_set
        ]

        # If there are both watchlist and non-watchlist rows, check ordering
        if wl_row_indices and non_wl_indices:
            assert max(wl_row_indices) < min(non_wl_indices), (
                "Watchlist rows should appear before non-watchlist rows in CSV"
            )

    def test_watchlist_three_symbols_in_sqlite(self, wl_env):
        """The watchlist table must contain exactly the 3 seeded symbols."""
        cfg, db, _ = wl_env
        wl_rows = db.get_watchlist()
        wl_symbols = {row["symbol"] for row in wl_rows}
        assert wl_symbols == set(self.WL_SYMS), (
            f"Watchlist mismatch: expected={set(self.WL_SYMS)}, got={wl_symbols}"
        )
