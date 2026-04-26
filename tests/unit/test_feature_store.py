"""
tests/unit/test_feature_store.py
---------------------------------
Unit tests for features/feature_store.py — the feature store orchestrator.

All tests are fully isolated using tmp_path (pytest fixture) as the data root.
Synthetic OHLCV data is generated in-memory; no real market files are needed.

Test index
----------
1. needs_bootstrap() returns True for missing file
2. needs_bootstrap() returns False after bootstrap() runs
3. bootstrap() creates the feature Parquet file with expected columns
4. update() appends exactly 1 new row to the feature file
5. update() is idempotent — calling it twice for same date does not raise
6. After bootstrap + update, feature file has len(original_processed) + 1 rows
7. InsufficientDataError propagates from bootstrap when processed data < 200 rows
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from features.feature_store import bootstrap, needs_bootstrap, update
from storage.parquet_store import read_parquet, write_parquet
from utils.exceptions import InsufficientDataError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_N_LARGE: int = 320   # > 300 rows required by update(); comfortably above 200 for bootstrap
_N_SMALL: int = 150   # < 200 rows — guaranteed InsufficientDataError from SMA-200


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_ohlcv(n: int, start: str = "2018-01-01") -> pd.DataFrame:
    """Return a synthetic OHLCV DataFrame with *n* business-day rows.

    Uses a fixed seed so results are deterministic across test runs.
    Slight upward drift keeps close prices positive throughout.
    """
    rng = np.random.default_rng(42)
    close = 100.0 + np.cumsum(rng.normal(0.3, 1.0, n))
    spread = rng.uniform(0.5, 2.0, n)
    high = close + spread
    low = np.maximum(close - spread, 0.5)   # clamp to avoid zero/negative lows
    open_ = close + rng.uniform(-0.3, 0.3, n)
    volume = rng.integers(500_000, 5_000_000, n).astype(float)
    index = pd.bdate_range(start, periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )


def _make_config(tmp_path: Path) -> dict:
    """Return a minimal config dict that points data dirs at *tmp_path*."""
    return {
        "data": {
            "processed_dir": str(tmp_path / "processed"),
            "features_dir":  str(tmp_path / "features"),
        },
        "stage": {
            "ma200_slope_lookback": 20,
            "ma50_slope_lookback": 10,
        },
        "atr": {"period": 14},
        "volume": {"avg_period": 50, "lookback_days": 20},
        "vcp": {
            "detector": "rule_based",
            "pivot_sensitivity": 5,
            "min_contractions": 2,
            "max_contractions": 5,
            "require_declining_depth": True,
            "require_vol_contraction": True,
            "min_weeks": 3,
            "max_weeks": 52,
            "tightness_pct": 10.0,
            "max_depth_pct": 50.0,
        },
    }


def _write_processed(config: dict, symbol: str, df: pd.DataFrame) -> Path:
    """Atomically write *df* to the processed Parquet file for *symbol*."""
    processed_dir = Path(config["data"]["processed_dir"])
    processed_dir.mkdir(parents=True, exist_ok=True)
    path = processed_dir / f"{symbol}.parquet"
    write_parquet(path, df)
    return path


def _features_path(config: dict, symbol: str) -> Path:
    return Path(config["data"]["features_dir"]) / f"{symbol}.parquet"


def _extend_processed(config: dict, symbol: str, base_df: pd.DataFrame) -> pd.Timestamp:
    """Append one new business-day row to the processed file.

    Returns the new row's Timestamp so callers can pass it to update().
    """
    last_ts = base_df.index[-1]
    new_ts = last_ts + pd.offsets.BDay(1)
    # Build a single-row OHLCV using a different seed to avoid clashes
    rng = np.random.default_rng(99)
    new_close = float(base_df["close"].iloc[-1]) + rng.normal(0.5, 0.1)
    new_row = pd.DataFrame(
        {
            "open":   [new_close + rng.uniform(-0.2, 0.2)],
            "high":   [new_close + rng.uniform(0.5, 1.5)],
            "low":    [new_close - rng.uniform(0.5, 1.5)],
            "close":  [new_close],
            "volume": [float(rng.integers(500_000, 5_000_000))],
        },
        index=pd.DatetimeIndex([new_ts]),
    )
    extended = pd.concat([base_df, new_row])
    _write_processed(config, symbol, extended)
    return new_ts



# ---------------------------------------------------------------------------
# Test 1 & 2: needs_bootstrap()
# ---------------------------------------------------------------------------


class TestNeedsBootstrap:
    """needs_bootstrap() inspects file existence and row count only."""

    def test_returns_true_when_feature_file_missing(self, tmp_path: Path) -> None:
        """Test 1: feature file has never been created → must bootstrap."""
        config = _make_config(tmp_path)
        assert needs_bootstrap("RELIANCE", config) is True

    def test_returns_false_after_bootstrap(self, tmp_path: Path) -> None:
        """Test 2: after a successful bootstrap, file exists with rows → no bootstrap needed."""
        config = _make_config(tmp_path)
        _write_processed(config, "RELIANCE", _make_ohlcv(_N_LARGE))
        bootstrap("RELIANCE", config)
        assert needs_bootstrap("RELIANCE", config) is False

    def test_returns_true_for_empty_feature_file(self, tmp_path: Path) -> None:
        """Edge case: an empty feature file (0 rows) still requires bootstrap."""
        config = _make_config(tmp_path)
        fp = _features_path(config, "RELIANCE")
        fp.parent.mkdir(parents=True, exist_ok=True)
        write_parquet(fp, pd.DataFrame())  # deliberately empty
        assert needs_bootstrap("RELIANCE", config) is True


# ---------------------------------------------------------------------------
# Test 3 & 7: bootstrap()
# ---------------------------------------------------------------------------


class TestBootstrap:
    """bootstrap() does full-history feature computation and writes Parquet."""

    def test_creates_feature_file_with_expected_columns(self, tmp_path: Path) -> None:
        """Test 3: output file exists, has correct row count, and key columns present."""
        config = _make_config(tmp_path)
        df = _make_ohlcv(_N_LARGE)
        _write_processed(config, "TCS", df)

        bootstrap("TCS", config)

        fp = _features_path(config, "TCS")
        assert fp.exists(), "Feature Parquet file was not created"

        feature_df = read_parquet(fp)
        assert len(feature_df) == _N_LARGE, (
            f"Expected {_N_LARGE} rows, got {len(feature_df)}"
        )

        # Spot-check columns from each feature module
        expected_cols = [
            # moving_averages
            "sma_10", "sma_50", "sma_150", "sma_200", "ema_21",
            "ma_slope_50", "ma_slope_200",
            # atr
            "atr_14", "atr_pct",
            # volume
            "vol_50d_avg", "vol_ratio", "acc_dist_score",
            # pivot
            "pivot_high", "pivot_low",
            # vcp
            "vcp_valid", "vcp_contraction_count", "vcp_tightness_score",
        ]
        missing = [c for c in expected_cols if c not in feature_df.columns]
        assert not missing, f"Missing columns after bootstrap: {missing}"

    def test_insufficient_data_propagates(self, tmp_path: Path) -> None:
        """Test 7: < 200 rows in processed → InsufficientDataError must propagate."""
        config = _make_config(tmp_path)
        _write_processed(config, "INFY", _make_ohlcv(_N_SMALL))  # 150 rows

        with pytest.raises(InsufficientDataError) as exc_info:
            bootstrap("INFY", config)

        err = exc_info.value
        assert err.required == 200, f"Expected required=200, got {err.required}"
        assert err.available == _N_SMALL

    def test_feature_file_not_created_on_error(self, tmp_path: Path) -> None:
        """If bootstrap raises, the features file must NOT be written."""
        config = _make_config(tmp_path)
        _write_processed(config, "WIPRO", _make_ohlcv(_N_SMALL))

        with pytest.raises(InsufficientDataError):
            bootstrap("WIPRO", config)

        assert not _features_path(config, "WIPRO").exists()



# ---------------------------------------------------------------------------
# Tests 4, 5, 6: update()
# ---------------------------------------------------------------------------


class TestUpdate:
    """Tests for the incremental daily update (fast path)."""

    # -----------------------------------------------------------------------
    # Shared setup: build a bootstrapped feature store, then extend processed
    # -----------------------------------------------------------------------

    @staticmethod
    def _bootstrap_symbol(tmp_path: Path, symbol: str = "HDFC") -> tuple[dict, pd.DataFrame]:
        """Write _N_LARGE processed rows and run bootstrap. Returns (config, base_df)."""
        config = _make_config(tmp_path)
        base_df = _make_ohlcv(_N_LARGE)
        _write_processed(config, symbol, base_df)
        bootstrap(symbol, config)
        return config, base_df

    # -----------------------------------------------------------------------
    # Test 4
    # -----------------------------------------------------------------------

    def test_appends_exactly_one_new_row(self, tmp_path: Path) -> None:
        """Test 4: update() for a brand-new date appends exactly 1 row."""
        config, base_df = self._bootstrap_symbol(tmp_path)

        n_before = len(read_parquet(_features_path(config, "HDFC")))

        new_ts = _extend_processed(config, "HDFC", base_df)
        update("HDFC", new_ts.date(), config)

        feature_df = read_parquet(_features_path(config, "HDFC"))
        assert len(feature_df) == n_before + 1, (
            f"Expected {n_before + 1} rows after update, got {len(feature_df)}"
        )

    # -----------------------------------------------------------------------
    # Test 5
    # -----------------------------------------------------------------------

    def test_idempotent_second_call_does_not_raise(self, tmp_path: Path) -> None:
        """Test 5: calling update() twice for the same date logs a warning but never raises."""
        config, base_df = self._bootstrap_symbol(tmp_path, symbol="AXIS")

        new_ts = _extend_processed(config, "AXIS", base_df)
        run_date = new_ts.date()

        update("AXIS", run_date, config)   # first call — appends
        update("AXIS", run_date, config)   # second call — should be a silent no-op

        feature_df = read_parquet(_features_path(config, "AXIS"))
        assert len(feature_df) == _N_LARGE + 1, (
            f"Idempotent second call must not add a duplicate row. "
            f"Expected {_N_LARGE + 1}, got {len(feature_df)}"
        )

    # -----------------------------------------------------------------------
    # Test 6
    # -----------------------------------------------------------------------

    def test_bootstrap_plus_update_row_count(self, tmp_path: Path) -> None:
        """Test 6: after bootstrap + update, feature file has original_rows + 1 rows."""
        config, base_df = self._bootstrap_symbol(tmp_path, symbol="KOTAK")

        # Sanity: bootstrap should have produced _N_LARGE feature rows
        assert len(read_parquet(_features_path(config, "KOTAK"))) == _N_LARGE

        # Add one trading day to processed and run update
        new_ts = _extend_processed(config, "KOTAK", base_df)
        update("KOTAK", new_ts.date(), config)

        feature_df = read_parquet(_features_path(config, "KOTAK"))
        assert len(feature_df) == _N_LARGE + 1, (
            f"Expected {_N_LARGE + 1} rows after bootstrap + update, "
            f"got {len(feature_df)}"
        )

    # -----------------------------------------------------------------------
    # Additional guard: update raises InsufficientDataError when processed < 300
    # -----------------------------------------------------------------------

    def test_raises_when_processed_has_fewer_than_300_rows(self, tmp_path: Path) -> None:
        """update() with < 300 processed rows must raise InsufficientDataError."""
        config = _make_config(tmp_path)
        # Write 250 rows — enough for bootstrap (200) but not for update (300)
        df_250 = _make_ohlcv(250)
        _write_processed(config, "BAJAJ", df_250)

        from datetime import date as date_cls
        with pytest.raises(InsufficientDataError) as exc_info:
            update("BAJAJ", date_cls.today(), config)

        err = exc_info.value
        assert err.required == 300
        assert err.available == 250

