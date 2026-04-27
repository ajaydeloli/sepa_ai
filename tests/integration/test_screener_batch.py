"""
tests/integration/test_screener_batch.py
-----------------------------------------
Integration tests for screener/pipeline.py and screener/results.py.

Coverage
--------
1. run_screen returns sorted list; MOCKUP (Stage 2) appears before MOCKDN (Stage 4).
2. pre_filter eliminates MOCKDN before the rule engine runs.
3. persist_results + load_results round-trip preserves score ordering.
4. get_top_candidates(min_quality="A") returns only A/A+ results.
5. run_screen is idempotent — running twice overwrites, not duplicates.

Design choices
--------------
* Feature Parquet files are written to a tmp_path fixture so no real data is needed.
* ProcessPoolExecutor is used for real (spawn) — tests are end-to-end, not unit mocks.
* benchmark_df is a minimal synthetic series long enough for RS computation (>=65 rows).
"""

from __future__ import annotations

import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Config shared by all tests
# ---------------------------------------------------------------------------

_RUN_DATE = date(2025, 6, 1)

_CFG_BASE: dict = {
    "stage": {"flat_slope_threshold": 0.0005},
    "trend_template": {
        "pct_above_52w_low":  25.0,
        "pct_below_52w_high": 25.0,
        "min_rs_rating":      70,
    },
    "vcp": {
        "detector":              "rule_based",
        "pivot_sensitivity":     5,
        "min_contractions":      2,
        "max_contractions":      5,
        "require_vol_contraction": True,
        "min_weeks":             3,
        "max_weeks":             52,
        "tightness_pct":         10.0,
        "max_depth_pct":         50.0,
    },
    "entry": {
        "breakout_buffer_pct":    0.001,
        "breakout_vol_threshold": 1.5,
    },
    "stop_loss": {
        "stop_buffer_pct": 0.005,
        "max_risk_pct":    15.0,
        "atr_multiplier":  2.0,
        "fixed_stop_pct":  0.07,
    },
    "risk_reward": {"min_rr_ratio": 2.0},
    "rs": {"period": 63},
    "pre_filter": {
        "min_close_pct_of_52w_high": 0.70,
        "min_rs_rating":             50,
    },
}

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_ohlcv_series(
    n: int = 300,
    start_price: float = 100.0,
    trend: float = 0.001,          # daily drift for close
    seed: int = 42,
) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame with DatetimeIndex."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start="2020-01-02", periods=n)
    close = start_price * np.cumprod(1 + trend + rng.normal(0, 0.005, n))
    high   = close * (1 + rng.uniform(0.001, 0.015, n))
    low    = close * (1 - rng.uniform(0.001, 0.015, n))
    open_  = close * (1 + rng.normal(0, 0.003, n))
    volume = rng.integers(500_000, 2_000_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def _make_feature_row_stage2(close: float = 150.0) -> dict:
    """Return a dict of feature values representing a clean Stage 2 setup."""
    return {
        "close":          close,
        "open":           close * 0.99,
        "high":           close * 1.01,
        "low":            close * 0.98,
        "volume":         1_000_000.0,
        "sma_50":         close * 0.87,
        "sma_150":        close * 0.80,
        "sma_200":        close * 0.73,
        "ma_slope_50":    0.08,
        "ma_slope_200":   0.04,
        "high_52w":       close * 1.03,
        "low_52w":        close * 0.60,
        "rs_rating":      85.0,
        "rs_raw":         1.5,
        "pivot_high":     close * 0.992,  # breakout: close > pivot * 1.001
        "vol_ratio":      3.0,
        "acc_dist_score": 10.0,
        "atr_14":         close * 0.015,
        "vcp_contraction_count": 3,
        "vcp_max_depth_pct":     22.0,
        "vcp_final_depth_pct":   7.0,
        "vcp_vol_ratio":         0.4,
        "vcp_base_length_weeks": 10,
        "vcp_base_low":          close * 0.75,
        "vcp_valid":             True,
        "vcp_tightness_score":   4.0,
    }


def _make_feature_row_stage4(close: float = 80.0) -> dict:
    """Feature row for a Stage 4 (declining) stock."""
    return {
        "close":          close,
        "open":           close * 1.005,
        "high":           close * 1.02,
        "low":            close * 0.98,
        "volume":         800_000.0,
        "sma_50":         close * 1.25,   # price well below SMA50
        "sma_150":        close * 1.31,
        "sma_200":        close * 1.50,   # price below SMA200 → fails pre_filter c3
        "ma_slope_50":    -0.03,
        "ma_slope_200":   -0.02,
        "high_52w":       close * 2.10,
        "low_52w":        close * 0.95,
        "rs_rating":      20.0,            # also fails pre_filter RS gate
        "rs_raw":         0.3,
        "pivot_high":     close * 2.50,
        "vol_ratio":      0.8,
        "acc_dist_score": -10.0,
        "atr_14":         close * 0.02,
        "vcp_contraction_count": 0,
        "vcp_max_depth_pct":     0.0,
        "vcp_final_depth_pct":   0.0,
        "vcp_vol_ratio":         float("nan"),
        "vcp_base_length_weeks": 0,
        "vcp_base_low":          float("nan"),
        "vcp_valid":             False,
        "vcp_tightness_score":   float("nan"),
    }

def _write_feature_parquet(path: Path, row_dict: dict, n_rows: int = 300) -> None:
    """Write a feature Parquet file with *n_rows* identical rows."""
    df = pd.DataFrame([row_dict] * n_rows)
    # Give it a DatetimeIndex matching business days.
    # Use start= not end= so the count is always exactly n_rows regardless
    # of whether the end date falls on a weekend.
    df.index = pd.bdate_range(start="2020-01-02", periods=n_rows)
    df.index.name = "date"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)


def _write_processed_parquet(path: Path, ohlcv_df: pd.DataFrame) -> None:
    """Write a processed OHLCV Parquet file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ohlcv_df.to_parquet(path)


def _make_config(tmp_path: Path) -> dict:
    """Build a config pointing at tmp_path subdirectories."""
    cfg = dict(_CFG_BASE)
    cfg["data"] = {
        "raw_dir":       str(tmp_path / "raw"),
        "processed_dir": str(tmp_path / "processed"),
        "features_dir":  str(tmp_path / "features"),
    }
    return cfg


def _make_symbol_info() -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": ["MOCKUP", "MOCKDN", "MOCKFLAT"],
        "sector": ["Technology", "Financials", "Technology"],
    })


def _make_benchmark(n: int = 300) -> pd.DataFrame:
    """Synthetic benchmark with enough rows for RS computation."""
    dates = pd.bdate_range(start="2020-01-02", periods=n)
    close = 18000.0 + np.arange(n) * 0.5  # gentle uptrend
    return pd.DataFrame({"close": close}, index=dates)


@pytest.fixture()
def tmp_screener_env(tmp_path):
    """Fixture: writes feature + processed Parquet files for the 3 mock symbols."""
    cfg = _make_config(tmp_path)
    features_dir  = Path(cfg["data"]["features_dir"])
    processed_dir = Path(cfg["data"]["processed_dir"])

    # MOCKUP — Stage 2 setup
    up_row = _make_feature_row_stage2(150.0)
    _write_feature_parquet(features_dir / "MOCKUP.parquet", up_row)
    _write_processed_parquet(processed_dir / "MOCKUP.parquet",
                             _make_ohlcv_series(300, 100.0, 0.001, seed=1))

    # MOCKDN — Stage 4 (will fail pre_filter: rs_rating=20, close < sma_200)
    dn_row = _make_feature_row_stage4(80.0)
    _write_feature_parquet(features_dir / "MOCKDN.parquet", dn_row)
    _write_processed_parquet(processed_dir / "MOCKDN.parquet",
                             _make_ohlcv_series(300, 200.0, -0.002, seed=2))

    # MOCKFLAT — marginal / near-flat; likely Stage 1
    flat_row = _make_feature_row_stage4(100.0)   # reuse Stage4 row as flat
    flat_row.update({
        "sma_50": 100.5, "sma_200": 99.0,
        "ma_slope_50": 0.0001, "ma_slope_200": -0.0001,
        "rs_rating": 45.0,  # below pre_filter RS gate of 50
    })
    _write_feature_parquet(features_dir / "MOCKFLAT.parquet", flat_row)
    _write_processed_parquet(processed_dir / "MOCKFLAT.parquet",
                             _make_ohlcv_series(300, 100.0, 0.0, seed=3))

    return cfg, _make_symbol_info(), _make_benchmark()

# ---------------------------------------------------------------------------
# Test 1 — run_screen returns sorted list; MOCKUP before MOCKDN
# ---------------------------------------------------------------------------

class TestRunScreenSorting:
    """run_screen returns results sorted by score DESC and MOCKUP > MOCKDN."""

    def test_mockup_outscores_mockdn(self, tmp_screener_env):
        cfg, symbol_info, benchmark_df = tmp_screener_env

        from screener.pipeline import run_screen

        results = run_screen(
            universe=["MOCKUP", "MOCKDN", "MOCKFLAT"],
            run_date=_RUN_DATE,
            config=cfg,
            symbol_info=symbol_info,
            benchmark_df=benchmark_df,
            n_workers=1,   # single worker — deterministic in tests
        )

        assert len(results) >= 1
        scores = [r.score for r in results]
        # Results must be sorted descending
        assert scores == sorted(scores, reverse=True), (
            f"Results not sorted by score: {scores}"
        )

        symbols = [r.symbol for r in results]
        # MOCKUP must appear before MOCKDN (higher score)
        if "MOCKUP" in symbols and "MOCKDN" in symbols:
            assert symbols.index("MOCKUP") < symbols.index("MOCKDN"), (
                f"MOCKUP should outrank MOCKDN; got order {symbols}"
            )

    def test_mockup_stage2(self, tmp_screener_env):
        """MOCKUP must reach stage 2 and quality in A+/A/B."""
        cfg, symbol_info, benchmark_df = tmp_screener_env

        from screener.pipeline import run_screen

        results = run_screen(
            universe=["MOCKUP"],
            run_date=_RUN_DATE,
            config=cfg,
            symbol_info=symbol_info,
            benchmark_df=benchmark_df,
            n_workers=1,
        )

        assert results, "Expected at least one result for MOCKUP"
        r = results[0]
        assert r.symbol == "MOCKUP"
        assert r.stage == 2, f"Expected Stage 2 for MOCKUP, got {r.stage}"
        assert r.setup_quality in ("A+", "A", "B"), (
            f"Expected quality A+/A/B, got {r.setup_quality!r}"
        )

    def test_mockdn_score_zero(self, tmp_screener_env):
        """MOCKDN (Stage 4) returns score==0 and quality==FAIL."""
        cfg, symbol_info, benchmark_df = tmp_screener_env

        from screener.pipeline import run_screen

        results = run_screen(
            universe=["MOCKDN"],
            run_date=_RUN_DATE,
            config=cfg,
            symbol_info=symbol_info,
            benchmark_df=benchmark_df,
            n_workers=1,
        )

        # MOCKDN fails pre_filter (rs=20, close < sma_200), so run_screen
        # may return an empty list.  If it does return something it must be FAIL.
        for r in results:
            if r.symbol == "MOCKDN":
                assert r.score == 0
                assert r.setup_quality == "FAIL"

# ---------------------------------------------------------------------------
# Test 2 — pre_filter eliminates MOCKDN
# ---------------------------------------------------------------------------

class TestPreFilterElimination:
    """pre_filter removes Stage 4 / low-RS symbols before the rule engine."""

    def test_mockdn_eliminated_by_pre_filter(self, tmp_screener_env):
        """MOCKDN has rs_rating=20 and close < sma_200 — both fail pre_filter."""
        cfg, _si, _bm = tmp_screener_env

        from screener.pre_filter import build_features_index, pre_filter

        features_index = build_features_index(["MOCKUP", "MOCKDN", "MOCKFLAT"], cfg)
        passed = pre_filter(features_index, cfg)

        assert "MOCKDN" not in passed, (
            f"MOCKDN should be eliminated by pre_filter; passed={passed}"
        )

    def test_mockup_passes_pre_filter(self, tmp_screener_env):
        """MOCKUP (Stage 2, rs=85, close > sma_200) passes pre_filter."""
        cfg, _si, _bm = tmp_screener_env

        from screener.pre_filter import build_features_index, pre_filter

        features_index = build_features_index(["MOCKUP", "MOCKDN"], cfg)
        passed = pre_filter(features_index, cfg)

        assert "MOCKUP" in passed, (
            f"MOCKUP should pass pre_filter; passed={passed}"
        )


# ---------------------------------------------------------------------------
# Test 3 — persist_results + load_results round-trip
# ---------------------------------------------------------------------------

class TestPersistLoadRoundTrip:
    """persist_results → load_results preserves score ordering."""

    def test_round_trip_ordering(self, tmp_path, tmp_screener_env):
        cfg, symbol_info, benchmark_df = tmp_screener_env

        from screener.pipeline import run_screen
        from screener.results import load_results, persist_results
        from storage.sqlite_store import SQLiteStore

        db = SQLiteStore(tmp_path / "test.db")

        results = run_screen(
            universe=["MOCKUP", "MOCKDN", "MOCKFLAT"],
            run_date=_RUN_DATE,
            config=cfg,
            symbol_info=symbol_info,
            benchmark_df=benchmark_df,
            n_workers=1,
        )

        persist_results(results, db, _RUN_DATE)
        loaded = load_results(db, _RUN_DATE)

        assert len(loaded) >= 1

        scores = [r["score"] for r in loaded]
        assert scores == sorted(scores, reverse=True), (
            f"load_results must return rows sorted by score DESC; got {scores}"
        )

    def test_round_trip_fields_present(self, tmp_path, tmp_screener_env):
        cfg, symbol_info, benchmark_df = tmp_screener_env

        from screener.pipeline import run_screen
        from screener.results import load_results, persist_results
        from storage.sqlite_store import SQLiteStore

        db = SQLiteStore(tmp_path / "test_fields.db")
        results = run_screen(
            universe=["MOCKUP"],
            run_date=_RUN_DATE,
            config=cfg,
            symbol_info=symbol_info,
            benchmark_df=benchmark_df,
            n_workers=1,
        )
        persist_results(results, db, _RUN_DATE)
        loaded = load_results(db, _RUN_DATE)

        assert loaded, "Expected at least one row"
        row = loaded[0]
        for col in ("symbol", "score", "setup_quality", "stage"):
            assert col in row, f"Expected column {col!r} in loaded row"

# ---------------------------------------------------------------------------
# Test 4 — get_top_candidates(min_quality="A") returns only A/A+
# ---------------------------------------------------------------------------

class TestGetTopCandidates:
    """get_top_candidates filters correctly on setup quality."""

    def test_only_a_and_a_plus_returned(self, tmp_path, tmp_screener_env):
        from screener.results import get_top_candidates, persist_results
        from storage.sqlite_store import SQLiteStore
        from rules.scorer import SEPAResult

        db = SQLiteStore(tmp_path / "quality.db")

        # Build synthetic results with known qualities
        def _make(sym, score, quality, stage):
            return SEPAResult(
                symbol=sym,
                run_date=_RUN_DATE,
                stage=stage,
                stage_label="",
                stage_confidence=80,
                trend_template_pass=(quality != "FAIL"),
                trend_template_details={},
                conditions_met=8 if quality in ("A+", "A") else 4,
                setup_quality=quality,
                score=score,
                vcp_qualified=(quality == "A+"),
            )

        synthetic_results = [
            _make("SYM_APLUS", 90, "A+", 2),
            _make("SYM_A",     75, "A",  2),
            _make("SYM_B",     60, "B",  2),
            _make("SYM_C",     45, "C",  2),
            _make("SYM_FAIL",   0, "FAIL", 4),
        ]

        persist_results(synthetic_results, db, _RUN_DATE)
        top = get_top_candidates(db, _RUN_DATE, min_quality="A", limit=10)

        qualities = {r["setup_quality"] for r in top}
        assert qualities <= {"A+", "A"}, (
            f"get_top_candidates(min_quality='A') returned unexpected qualities: {qualities}"
        )
        assert len(top) == 2, f"Expected 2 A/A+ results, got {len(top)}"

    def test_limit_respected(self, tmp_path):
        from screener.results import get_top_candidates, persist_results
        from storage.sqlite_store import SQLiteStore
        from rules.scorer import SEPAResult

        db = SQLiteStore(tmp_path / "limit.db")

        results = [
            SEPAResult(
                symbol=f"SYM{i:02d}",
                run_date=_RUN_DATE,
                stage=2,
                stage_label="Stage 2 — Advancing",
                stage_confidence=80,
                trend_template_pass=True,
                trend_template_details={},
                conditions_met=8,
                setup_quality="A",
                score=70 + i,
            )
            for i in range(10)
        ]
        persist_results(results, db, _RUN_DATE)
        top = get_top_candidates(db, _RUN_DATE, min_quality="A", limit=3)
        assert len(top) <= 3


# ---------------------------------------------------------------------------
# Test 5 — Idempotency: running twice produces no duplicate rows
# ---------------------------------------------------------------------------

class TestRunScreenIdempotent:
    """Calling run_screen twice for the same date results in one row per symbol."""

    def test_no_duplicates_on_second_run(self, tmp_path, tmp_screener_env):
        cfg, symbol_info, benchmark_df = tmp_screener_env

        from screener.pipeline import run_screen
        from screener.results import load_results, persist_results
        from storage.sqlite_store import SQLiteStore

        db = SQLiteStore(tmp_path / "idem.db")

        # First run
        results1 = run_screen(
            universe=["MOCKUP"],
            run_date=_RUN_DATE,
            config=cfg,
            symbol_info=symbol_info,
            benchmark_df=benchmark_df,
            n_workers=1,
        )
        persist_results(results1, db, _RUN_DATE)

        # Second run — same date
        results2 = run_screen(
            universe=["MOCKUP"],
            run_date=_RUN_DATE,
            config=cfg,
            symbol_info=symbol_info,
            benchmark_df=benchmark_df,
            n_workers=1,
        )
        persist_results(results2, db, _RUN_DATE)

        loaded = load_results(db, _RUN_DATE)
        symbols = [r["symbol"] for r in loaded]

        # No duplicates
        assert len(symbols) == len(set(symbols)), (
            f"Duplicate symbols found after two runs: {symbols}"
        )
