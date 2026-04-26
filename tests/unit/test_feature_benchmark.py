"""
tests/unit/test_feature_benchmark.py
--------------------------------------
Performance benchmarks for the SEPA feature pipeline.

All tests are marked ``@pytest.mark.benchmark``.  They use wall-clock timing
(``time.perf_counter``) rather than pytest-benchmark so they run with a plain
``pytest`` invocation and have zero extra dependencies.

Thresholds
----------
* ``test_single_symbol_bootstrap_under_2s`` — 300-row bootstrap < 2 s
* ``test_single_symbol_update_under_50ms``  — 300-row update < 50 ms
* ``test_pre_filter_1000_symbols_under_100ms`` — pre_filter on 1 000 symbols < 100 ms

Run only these tests
--------------------
    pytest tests/unit/test_feature_benchmark.py -v -m benchmark
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from features.feature_store import bootstrap, update
from screener.pre_filter import pre_filter
from storage.parquet_store import write_parquet

# ---------------------------------------------------------------------------
# Pytest mark registration (avoids "Unknown mark" warning)
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.benchmark


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_N_ROWS: int = 300  # row count used across all benchmarks


def _make_ohlcv(n: int, start: str = "2019-01-01") -> pd.DataFrame:
    """Deterministic synthetic OHLCV with a gentle upward drift."""
    rng = np.random.default_rng(7)
    close = 200.0 + np.cumsum(rng.normal(0.4, 1.0, n))
    spread = rng.uniform(0.5, 2.5, n)
    high = close + spread
    low = np.maximum(close - spread, 0.5)
    open_ = close + rng.uniform(-0.3, 0.3, n)
    volume = rng.integers(1_000_000, 10_000_000, n).astype(float)
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _make_config(tmp_path: Path) -> dict:
    """Minimal config that points all data dirs at *tmp_path*."""
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
        "pre_filter": {
            "min_close_pct_of_52w_high": 0.70,
            "min_rs_rating": 50,
        },
    }


def _write_processed(config: dict, symbol: str, df: pd.DataFrame) -> None:
    processed_dir = Path(config["data"]["processed_dir"])
    processed_dir.mkdir(parents=True, exist_ok=True)
    write_parquet(processed_dir / f"{symbol}.parquet", df)


# ---------------------------------------------------------------------------
# Benchmark 1 — bootstrap under 2 s
# ---------------------------------------------------------------------------

@pytest.mark.benchmark
def test_single_symbol_bootstrap_under_2s(tmp_path: Path) -> None:
    """Bootstrap a 300-row symbol must complete in under 2 seconds."""
    config = _make_config(tmp_path)
    df = _make_ohlcv(_N_ROWS)
    _write_processed(config, "BENCH1", df)

    t0 = time.perf_counter()
    bootstrap("BENCH1", config)
    elapsed = time.perf_counter() - t0

    assert elapsed < 2.0, (
        f"bootstrap() took {elapsed:.3f}s — exceeds 2.0s threshold. "
        "This indicates a regression in feature computation speed."
    )


# ---------------------------------------------------------------------------
# Benchmark 2 — update under 50 ms
# ---------------------------------------------------------------------------

@pytest.mark.benchmark
def test_single_symbol_update_under_50ms(tmp_path: Path) -> None:
    """Daily update (300-row window → 1 appended row) must complete in under 50ms."""
    config = _make_config(tmp_path)
    df = _make_ohlcv(_N_ROWS)
    _write_processed(config, "BENCH2", df)

    # Bootstrap first so feature file exists
    bootstrap("BENCH2", config)

    # Append one new business-day row to processed data
    last_ts = df.index[-1]
    new_ts = last_ts + pd.offsets.BDay(1)
    rng = np.random.default_rng(13)
    new_close = float(df["close"].iloc[-1]) + rng.normal(0.5, 0.1)
    new_row = pd.DataFrame(
        {
            "open":   [new_close + rng.uniform(-0.2, 0.2)],
            "high":   [new_close + rng.uniform(0.5, 1.5)],
            "low":    [new_close - rng.uniform(0.5, 1.5)],
            "close":  [new_close],
            "volume": [float(rng.integers(1_000_000, 10_000_000))],
        },
        index=pd.DatetimeIndex([new_ts]),
    )
    extended = pd.concat([df, new_row])
    _write_processed(config, "BENCH2", extended)

    t0 = time.perf_counter()
    update("BENCH2", new_ts.date(), config)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.050, (
        f"update() took {elapsed * 1000:.1f}ms — exceeds 50ms threshold. "
        "The daily fast path is too slow."
    )


# ---------------------------------------------------------------------------
# Benchmark 3 — pre_filter on 1 000 symbols under 100 ms
# ---------------------------------------------------------------------------

@pytest.mark.benchmark
def test_pre_filter_1000_symbols_under_100ms() -> None:
    """pre_filter() on a 1 000-symbol features_index dict must run in under 100ms."""
    rng = np.random.default_rng(42)

    # Build 1 000 symbol entries; roughly half will pass each criterion
    features_index: dict[str, dict] = {}
    for i in range(1000):
        close = float(rng.uniform(100, 500))
        high_52w = close * float(rng.uniform(1.0, 1.5))   # always >= close
        sma_200 = close * float(rng.uniform(0.85, 1.15))  # straddles close
        rs_rating = float(rng.uniform(20, 100))
        features_index[f"SYM{i:04d}"] = {
            "close":     close,
            "high_52w":  high_52w,
            "sma_200":   sma_200,
            "rs_rating": rs_rating,
        }

    config = {
        "pre_filter": {
            "min_close_pct_of_52w_high": 0.70,
            "min_rs_rating": 50,
        }
    }

    t0 = time.perf_counter()
    passed = pre_filter(features_index, config)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.100, (
        f"pre_filter(1000 symbols) took {elapsed * 1000:.1f}ms — exceeds 100ms threshold."
    )
    # Sanity: at least some symbols pass
    assert isinstance(passed, list)
    assert len(passed) > 0
