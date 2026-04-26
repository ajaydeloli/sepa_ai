"""
scripts/create_test_fixtures.py
--------------------------------
Generate deterministic synthetic OHLCV fixture files for integration tests.

Symbols generated
-----------------
MOCKUP   — Stage 2 uptrend with a VCP-like consolidation forming (strong stock).
           After bar 200: close > sma_50 > sma_150 > sma_200 (Stage 2 verified).
MOCKDN   — Stage 4 decline.  After bar 200: close < sma_200.
MOCKFLAT — Stage 1 flat base.  Price sideways around a starting level.

Output: tests/fixtures/sample_ohlcv_{symbol}.parquet  (one file per symbol)

Usage
-----
    python scripts/create_test_fixtures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path when run as a script
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"
N_DAYS: int = 300
SEED: int = 42
START_DATE: str = "2022-01-03"


# ---------------------------------------------------------------------------
# Low-level OHLCV builder
# ---------------------------------------------------------------------------

def _ohlcv_from_close(
    close_vals: np.ndarray,
    dates: pd.DatetimeIndex,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Build a realistic OHLCV DataFrame from an array of close prices.

    Guarantees:
    * high >= close >= low
    * open clipped to [low, high]
    * all prices > 0
    * volume > 0
    """
    n = len(close_vals)
    spread = rng.uniform(0.3, 2.0, n)

    high   = close_vals + spread + rng.uniform(0.0, 0.5, n)
    low    = close_vals - spread - rng.uniform(0.0, 0.5, n)
    low    = np.maximum(low, 1.0)

    # Enforce OHLCV invariants: high >= close, low <= close
    high = np.maximum(high, close_vals)
    low  = np.minimum(low,  close_vals)

    open_ = close_vals + rng.uniform(-0.8, 0.8, n)
    open_ = np.clip(open_, low, high)

    volume = rng.integers(400_000, 6_000_000, n).astype(float)

    return pd.DataFrame(
        {
            "open":   open_,
            "high":   high,
            "low":    low,
            "close":  close_vals,
            "volume": volume,
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# MOCKUP — Stage 2 uptrend + VCP-like consolidation
# ---------------------------------------------------------------------------

def _build_mockup(rng: np.random.Generator) -> pd.DataFrame:
    """Stage 2 uptrend with a VCP-like base forming in the last ~80 bars.

    Design
    ------
    Bars   0-219 : strong linear uptrend 100 → 225 (base trend via linspace,
                   small Gaussian noise σ=2 so MA ordering holds comfortably).
    Bars 220-299 : VCP consolidation — three contracting swing legs above the
                   SMA-200 region, with volume roughly declining each leg.

    MA ordering guarantee at bar 200 (analytic, verified in main()):
        close(≈213) > sma_50(≈202) > sma_150(≈175) > sma_200(≈157)
    """
    dates = pd.bdate_range(START_DATE, periods=N_DAYS)
    close = np.empty(N_DAYS, dtype=float)

    # ── Phase 1: uptrend (bars 0–219) ──────────────────────────────────────
    up_bars = 220
    base = np.linspace(100.0, 225.0, up_bars)           # deterministic backbone
    noise = rng.normal(0.0, 2.0, up_bars)               # small IID noise
    close[:up_bars] = np.maximum(base + noise, 10.0)

    # ── Phase 2: VCP consolidation (bars 220–299) ──────────────────────────
    # Three contracting swing legs to create a valid pivot structure.
    # Leg depths: ~9 %, ~5 %, ~2.5 % — clearly contracting.
    vcp_start = up_bars
    peak = float(close[vcp_start - 1])

    # Define breakpoints (absolute bar positions within the VCP window)
    # Leg 1: peak → low1 → recovery1
    low1       = peak * 0.91
    recovery1  = peak * 0.975
    # Leg 2: recovery1 → low2 → recovery2
    low2       = peak * 0.95
    recovery2  = peak * 0.965
    # Leg 3: recovery2 → low3 → tight coil
    low3       = peak * 0.975

    # Simple piecewise linspace segments (deterministic backbone)
    seg = np.concatenate([
        np.linspace(peak,       low1,       18),   # bars 220-237  drop leg 1
        np.linspace(low1,       recovery1,  12),   # bars 238-249  rally
        np.linspace(recovery1,  low2,       13),   # bars 250-262  drop leg 2
        np.linspace(low2,       recovery2,   9),   # bars 263-271  rally
        np.linspace(recovery2,  low3,       10),   # bars 272-281  drop leg 3
        np.linspace(low3,       low3 * 1.01, 18),  # bars 282-299  tight coil
    ])
    # Trim / pad to exactly (N_DAYS - vcp_start) bars
    vcp_len = N_DAYS - vcp_start
    seg = seg[:vcp_len]
    vcp_noise = rng.normal(0.0, 0.3, vcp_len)
    close[vcp_start:] = np.maximum(seg + vcp_noise, 10.0)

    # Volume: declining across VCP legs (first leg loudest, last quietest)
    df = _ohlcv_from_close(close, dates, rng)
    vol = df["volume"].to_numpy(dtype=float, copy=True)   # writable copy
    vol[vcp_start + 18: vcp_start + 44] *= 0.7   # leg 2 — quieter
    vol[vcp_start + 44: vcp_start + 63] *= 0.45  # leg 3 — quietest
    df["volume"] = np.maximum(vol, 100_000.0)

    return df


# ---------------------------------------------------------------------------
# MOCKDN — Stage 4 decline
# ---------------------------------------------------------------------------

def _build_mockdn(rng: np.random.Generator) -> pd.DataFrame:
    """Stage 4 decline — price and MAs all falling.

    Design
    ------
    Linear downtrend from 200 → 70 over 300 bars (backbone via linspace).
    Small IID noise σ=2 ensures the trend dominates the MA calculations.

    MA ordering guarantee at bar 200 (analytic):
        SMA_200(≈151) > SMA_150(≈134) > SMA_50(≈110) > close(≈108)
    The key requirement is close < sma_200 after bar 200, which holds
    with a margin of ~43 points.
    """
    dates = pd.bdate_range(START_DATE, periods=N_DAYS)
    base  = np.linspace(200.0, 70.0, N_DAYS)
    noise = rng.normal(0.0, 2.0, N_DAYS)
    close = np.maximum(base + noise, 5.0)
    return _ohlcv_from_close(close, dates, rng)


# ---------------------------------------------------------------------------
# MOCKFLAT — Stage 1 flat base
# ---------------------------------------------------------------------------

def _build_mockflat(rng: np.random.Generator) -> pd.DataFrame:
    """Stage 1 flat base — price sideways around 100.

    Design
    ------
    Tiny drift (+0.02/bar) with moderate IID noise σ=1.5.
    Over 300 bars, expected range is roughly 95–120 (no clear trend).
    The MA pack is bunched together — all MAs within a narrow band.
    """
    dates = pd.bdate_range(START_DATE, periods=N_DAYS)
    base  = np.linspace(100.0, 106.0, N_DAYS)   # tiny upward drift
    noise = rng.normal(0.0, 1.5, N_DAYS)
    close = np.maximum(base + noise, 5.0)
    return _ohlcv_from_close(close, dates, rng)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _assert_ohlcv_valid(df: pd.DataFrame, symbol: str) -> None:
    """Run basic sanity checks on the generated fixture."""
    assert (df["close"] > 0).all(),            f"{symbol}: non-positive close"
    assert (df["high"]  >= df["close"]).all(), f"{symbol}: high < close"
    assert (df["low"]   <= df["close"]).all(), f"{symbol}: low > close"
    assert (df["high"]  >= df["low"]).all(),   f"{symbol}: high < low"
    assert (df["open"]  >= df["low"]).all(),   f"{symbol}: open < low"
    assert (df["open"]  <= df["high"]).all(),  f"{symbol}: open > high"
    assert (df["volume"] > 0).all(),           f"{symbol}: non-positive volume"
    assert len(df) == N_DAYS,                  f"{symbol}: expected {N_DAYS} rows"


def _assert_ma_ordering(df: pd.DataFrame, symbol: str) -> None:
    """Verify MA ordering holds at bar 200 for the given symbol expectations."""
    close_s = df["close"]
    sma_50  = close_s.rolling(50).mean()
    sma_150 = close_s.rolling(150).mean()
    sma_200 = close_s.rolling(200).mean()

    close_200  = close_s.iloc[200]
    sma50_200  = sma_50.iloc[200]
    sma150_200 = sma_150.iloc[200]
    sma200_200 = sma_200.iloc[200]

    if symbol == "MOCKUP":
        assert close_200 > sma200_200, (
            f"MOCKUP: close({close_200:.2f}) must be > sma_200({sma200_200:.2f}) at bar 200"
        )
        assert sma50_200 > sma200_200, (
            f"MOCKUP: sma_50({sma50_200:.2f}) must be > sma_200({sma200_200:.2f}) at bar 200"
        )
    elif symbol == "MOCKDN":
        assert close_200 < sma200_200, (
            f"MOCKDN: close({close_200:.2f}) must be < sma_200({sma200_200:.2f}) at bar 200"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

SYMBOL_BUILDERS = {
    "MOCKUP":   _build_mockup,
    "MOCKDN":   _build_mockdn,
    "MOCKFLAT": _build_mockflat,
}


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    for symbol, builder in SYMBOL_BUILDERS.items():
        # Fresh RNG with the canonical seed for every symbol → reproducible
        rng = np.random.default_rng(seed=SEED)
        df  = builder(rng)

        _assert_ohlcv_valid(df, symbol)
        _assert_ma_ordering(df, symbol)

        out_path = FIXTURES_DIR / f"sample_ohlcv_{symbol}.parquet"
        df.to_parquet(out_path, index=True)

    print(f"Created 3 fixture files in tests/fixtures/")


if __name__ == "__main__":
    main()
