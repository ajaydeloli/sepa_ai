"""
tests/unit/test_vcp.py
----------------------
Unit tests for features/vcp.py — Volatility Contraction Pattern detection.

All tests are self-contained; no external fixtures or I/O required.
DataFrames are built from hand-crafted price/volume series with known,
deterministic pivot positions so every assertion is exact.

Sensitivity=2 is used throughout to keep DataFrames short while still
satisfying the algorithm's look-left / look-right requirements.

VCP data layout (25 bars, sensitivity=2)
-----------------------------------------
idx:  0    1    2    3    4    5    6    7    8    9  10  11  12  13  14  15  16  17  18  19  20  21  22  23  24
      <    <    H1   >    >    >    L1   <    <    <  H2  >   >   >   L2  <   <   <  H3   >   >   >   L3   <   <

Confirmed pivots (sensitivity=2, high = close+0.5, low = close-0.5):
  swing_highs: (2, 110.5), (10, 105.5), (18, 102.5)
  swing_lows:  (6, 87.5),  (14, 89.5),  (22, 93.5)

Contraction legs (swing_high → next swing_low):
  Leg 1: H1 → L1 : depth = (110.5-87.5)/110.5 × 100 ≈ 20.81 %
  Leg 2: H2 → L2 : depth = (105.5-89.5)/105.5 × 100 ≈ 15.17 %
  Leg 3: H3 → L3 : depth = (102.5-93.5)/102.5 × 100 ≈  8.78 %
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from features import vcp
from features.vcp import (
    RuleBasedVCPDetector,
    VCPMetrics,
    _VCP_COLUMNS,
    compute,
    get_detector,
)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_SENSITIVITY = 2          # small value → short test DataFrames
_DATE_START  = "2024-01-01"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_df(
    closes: list[float],
    volumes: list[float] | None = None,
    start: str = _DATE_START,
) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame.

    high = close + 0.5  and  low = close - 0.5  so pivot detection on
    the high / low series mirrors the shape of the close series.
    If *volumes* is omitted every bar gets 1 000 000.
    """
    c  = np.array(closes, dtype=float)
    h  = c + 0.5
    lo = c - 0.5
    v  = np.array(volumes, dtype=float) if volumes else np.ones(len(c)) * 1_000_000
    return pd.DataFrame(
        {"open": c, "high": h, "low": lo, "close": c, "volume": v},
        index=pd.bdate_range(start, periods=len(c)),
    )


def _base_config(**overrides) -> dict:
    """Return a minimal VCP config with optional field overrides."""
    cfg: dict = {
        "vcp": {
            "detector":              "rule_based",
            "pivot_sensitivity":     _SENSITIVITY,
            "min_contractions":      2,
            "max_contractions":      5,
            "require_vol_contraction": True,
            "min_weeks":             3,
            "max_weeks":             52,
            "tightness_pct":         20.0,   # generous: last-3-wk range < 20 %
            "max_depth_pct":         50.0,
        }
    }
    cfg["vcp"].update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# Synthetic 3-leg VCP base (shared across tests 1, 4, 5)
# ---------------------------------------------------------------------------

#: Closes that produce 3 contracting swing pairs at known indices.
_3LEG_CLOSES = [
    100, 104, 110, 104, 100,   # H1 at idx 2 (high=110.5)
     94,  88,  94, 100, 102,   # L1 at idx 6 (low=87.5)
    105, 102,  98,  93,  90,   # H2 at idx 10 (high=105.5); L2 at idx 14 (low=89.5)
     93,  97, 100, 102,  99,   # H3 at idx 18 (high=102.5)
     96,  95,  94,  95,  96,   # L3 at idx 22 (low=93.5)
]

#: Strictly declining volume — last-leg avg < first-leg avg.
_3LEG_VOLUMES = [
    2000, 1925, 1850, 1775, 1700,
    1625, 1550, 1475, 1400, 1325,
    1250, 1175, 1100, 1025,  950,
     875,  800,  725,  650,  575,
     500,  425,  350,  275,  200,
]


# ---------------------------------------------------------------------------
# Test 1 — synthetic 3-leg VCP: counts, depths, validity
# ---------------------------------------------------------------------------


class TestThreeLegVCP:
    """The canonical 3-leg contracting base must be fully detected and valid."""

    @pytest.fixture
    def metrics(self) -> VCPMetrics:
        df  = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        cfg = _base_config()
        return RuleBasedVCPDetector().detect(df, cfg)

    def test_contraction_count_is_three(self, metrics: VCPMetrics) -> None:
        assert metrics.contraction_count == 3

    def test_final_depth_less_than_max_depth(self, metrics: VCPMetrics) -> None:
        """The most recent correction must be shallower than the deepest one."""
        assert metrics.final_depth_pct < metrics.max_depth_pct

    def test_max_depth_is_first_leg(self, metrics: VCPMetrics) -> None:
        """The deepest leg should be the first leg (~20.81 %)."""
        assert metrics.max_depth_pct == pytest.approx(
            (110.5 - 87.5) / 110.5 * 100, abs=0.1
        )

    def test_final_depth_is_last_leg(self, metrics: VCPMetrics) -> None:
        """The shallowest leg should be the last leg (~8.78 %)."""
        assert metrics.final_depth_pct == pytest.approx(
            (102.5 - 93.5) / 102.5 * 100, abs=0.1
        )

    def test_is_valid_vcp_true(self, metrics: VCPMetrics) -> None:
        assert metrics.is_valid_vcp is True

    def test_base_low_within_base(self, metrics: VCPMetrics) -> None:
        """base_low must equal the lowest low in the base range."""
        # Lowest close in base = 88 (idx 6) → low = 87.5
        assert metrics.base_low == pytest.approx(87.5, abs=0.01)

    def test_base_length_weeks_at_least_min(self, metrics: VCPMetrics) -> None:
        assert metrics.base_length_weeks >= 3


# ---------------------------------------------------------------------------
# Test 2 — single-leg base: is_valid_vcp must be False
# ---------------------------------------------------------------------------


class TestSingleLegBase:
    """A base with only 1 contraction cannot satisfy min_contractions=2."""

    def test_single_contraction_not_valid(self) -> None:
        # idx: 0   1    2    3    4    5    6    7    8
        closes = [100, 104, 110, 104, 100, 96, 90, 93, 96]
        df  = _make_df(closes)
        cfg = _base_config()  # min_contractions=2
        m   = RuleBasedVCPDetector().detect(df, cfg)
        assert m.contraction_count == 1
        assert m.is_valid_vcp is False



# ---------------------------------------------------------------------------
# Test 3 — non-contracting base: second leg deeper → is_valid_vcp False
# ---------------------------------------------------------------------------


class TestNonContractingBase:
    """When the second leg is deeper than the first the VCP rule fails."""

    def test_expanding_depth_not_valid(self) -> None:
        # Leg 1: H1 (110.5) → L1 (97.5)  depth ≈ 11.76 %
        # Leg 2: H2 (105.5) → L2 (82.5)  depth ≈ 21.80 %   ← DEEPER (bad)
        # final_depth_pct == max_depth_pct → rule "final < max" fails
        closes = [
            100, 104, 110, 104, 100,   # H1 at idx 2 (high=110.5)
             99,  98,  99, 100, 102,   # L1 at idx 6 (low=97.5)
            105, 102,  98,  84,  83,   # H2 at idx 10 (high=105.5); L2 at idx 14 (low=82.5)
             84,  88,
        ]
        df  = _make_df(closes)
        cfg = _base_config(min_weeks=0)   # relax week constraint for short frame
        m   = RuleBasedVCPDetector().detect(df, cfg)

        assert m.contraction_count == 2
        # final leg IS the deepest ⇒ final == max ⇒ rule fails
        assert m.final_depth_pct >= m.max_depth_pct - 0.01
        assert m.is_valid_vcp is False


# ---------------------------------------------------------------------------
# Test 4 — volume dry-up: vol_contraction_ratio < 1.0
# ---------------------------------------------------------------------------


class TestVolumeDryUp:
    """Declining volume across the base must yield vol_contraction_ratio < 1.0."""

    def test_declining_volume_ratio_below_one(self) -> None:
        df = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        cfg = _base_config()
        m   = RuleBasedVCPDetector().detect(df, cfg)

        assert not math.isnan(m.vol_contraction_ratio), (
            "vol_contraction_ratio must not be NaN for valid leg data"
        )
        assert m.vol_contraction_ratio < 1.0, (
            f"Expected ratio < 1.0 for declining volume, got {m.vol_contraction_ratio:.4f}"
        )

    def test_flat_volume_ratio_near_one(self) -> None:
        """Flat volume should produce a ratio of exactly 1.0."""
        df = _make_df(_3LEG_CLOSES)   # uniform volume = 1 000 000
        cfg = _base_config()
        m   = RuleBasedVCPDetector().detect(df, cfg)

        assert m.vol_contraction_ratio == pytest.approx(1.0, abs=1e-6)



# ---------------------------------------------------------------------------
# Test 5 — compute() appends all 8 vcp_* columns to the DataFrame
# ---------------------------------------------------------------------------


class TestComputeColumns:
    """compute() must append exactly the eight documented vcp_* columns."""

    def test_all_eight_columns_appended(self) -> None:
        df     = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        cfg    = _base_config()
        result = compute(df, cfg)

        for col in _VCP_COLUMNS:
            assert col in result.columns, f"Column '{col}' missing from output"

    def test_original_columns_preserved(self) -> None:
        df  = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        cfg = _base_config()
        originals = {c: df[c].tolist() for c in ["open", "high", "low", "close", "volume"]}
        result = compute(df, cfg)
        for col, vals in originals.items():
            assert result[col].tolist() == vals, f"Column '{col}' was mutated"

    def test_scalar_columns_same_in_every_row(self) -> None:
        """All vcp_* columns are broadcast scalars — every row must be equal."""
        df     = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        cfg    = _base_config()
        result = compute(df, cfg)

        for col in _VCP_COLUMNS:
            unique_vals = result[col].dropna().unique()
            assert len(unique_vals) <= 1, (
                f"Column '{col}' has more than one distinct value: {unique_vals}"
            )

    def test_vcp_valid_is_bool_dtype(self) -> None:
        df     = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        result = compute(df, _base_config())
        assert result["vcp_valid"].dtype == bool or result["vcp_valid"].iloc[-1] in (True, False)


# ---------------------------------------------------------------------------
# Test 6 — compute() never raises; fills NaN/False on failure
# ---------------------------------------------------------------------------


class TestComputeGraceful:
    """compute() must absorb all exceptions from the detector."""

    def test_no_exception_when_no_pivots(self) -> None:
        """Flat price series → no pivots → graceful degenerate metrics."""
        df  = _make_df([100.0] * 20)
        cfg = _base_config()
        result = compute(df, cfg)   # must not raise

        assert "vcp_valid" in result.columns
        assert result["vcp_valid"].iloc[-1] is False or result["vcp_valid"].iloc[-1] == False

    def test_all_columns_present_on_no_pivots(self) -> None:
        df  = _make_df([100.0] * 20)
        cfg = _base_config()
        result = compute(df, cfg)
        for col in _VCP_COLUMNS:
            assert col in result.columns, f"Column '{col}' missing on graceful failure"

    def test_nan_fill_on_bad_detector_name(self) -> None:
        """An unknown detector must be caught; numeric columns filled with NaN."""
        df  = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        cfg = _base_config()
        cfg["vcp"]["detector"] = "does_not_exist"

        result = compute(df, cfg)   # must not raise

        assert result["vcp_valid"].iloc[-1] == False
        assert pd.isna(result["vcp_contraction_count"].iloc[-1])

    def test_returns_dataframe_instance(self) -> None:
        df     = _make_df([100.0] * 20)
        result = compute(df, _base_config())
        assert isinstance(result, pd.DataFrame)



# ---------------------------------------------------------------------------
# Test 7 — factory: get_detector() registry
# ---------------------------------------------------------------------------


class TestGetDetector:
    """get_detector() must return the right class and raise on unknown names."""

    def test_returns_rule_based_by_default(self) -> None:
        detector = get_detector({"vcp": {}})
        assert isinstance(detector, RuleBasedVCPDetector)

    def test_returns_rule_based_when_explicit(self) -> None:
        cfg      = _base_config()
        detector = get_detector(cfg)
        assert isinstance(detector, RuleBasedVCPDetector)

    def test_raises_on_unknown_detector(self) -> None:
        from utils.exceptions import ConfigurationError
        with pytest.raises(ConfigurationError, match="Unknown VCP detector"):
            get_detector({"vcp": {"detector": "banana"}})
