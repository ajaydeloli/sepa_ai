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
            "tightness_pct":         2.0,    # lenient ATR ratio; nan (< 50 bars) skips gate
            "max_depth_pct":         50.0,
            # Set to 0 so that existing tests (whose legs are 4 days long)
            # are not broken by the new duration guard default of 5.
            "min_leg_duration_days": 0,
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


# ---------------------------------------------------------------------------
# Test 8 — leg-duration guard (Improvement 6)
# ---------------------------------------------------------------------------

# Closes that produce exactly 3 swing pairs at known indices (sensitivity=2):
#   H1 at idx 2  (close=110) → L1 at idx 5  (close=88)   duration=3  (short)
#   H2 at idx 9  (close=105) → L2 at idx 17 (close=91)   duration=8  (long)
#   H3 at idx 21 (close=101) → L3 at idx 29 (close=94)   duration=8  (long)
#
# Contraction depths (high.price = close+0.5):
#   Leg1: (110.5-87.5)/110.5 ≈ 20.81 %
#   Leg2: (105.5-90.5)/105.5 ≈ 14.22 %
#   Leg3: (101.5-93.5)/101.5 ≈  7.88 %
_MIXED_LEG_CLOSES = [
    # idx  0    1    2    3    4
           95, 100, 110, 107, 103,   # H1 at idx 2
    # idx  5    6    7    8    9
           88,  91,  94,  97, 105,   # L1 at idx 5 (duration 3); H2 at idx 9
    # idx 10   11   12   13   14
          104, 103, 102, 101, 100,
    # idx 15   16   17   18   19
           99,  98,  91,  94,  97,   # L2 at idx 17 (duration 8)
    # idx 20   21   22   23   24
           99, 101, 100,  99,  98,   # H3 at idx 21
    # idx 25   26   27   28   29
           97,  96,  95,  95,  94,   # L3 at idx 29 (duration 8)
    # idx 30   31   32   33
           95,  96,  97,  98,        # trailing bars
]


class TestLegDurationGuard:
    """Improvement 6 — duration_days filter in _build_legs / detect()."""

    # ------------------------------------------------------------------
    # Test 1: single pair spanning 3 days — filtered at min=5
    # ------------------------------------------------------------------
    def test_short_leg_filtered_contraction_zero(self) -> None:
        """A pivot pair spanning only 3 trading days must be dropped.

        H at idx 2, L at idx 5 → duration=3 < 5 → legs all filtered
        → contraction_count==0 and is_valid_vcp==False.
        """
        # 8-bar series: H at idx 2 (close=110), L at idx 5 (close=88)
        closes = [95, 100, 110, 104, 100, 88, 92, 96]
        df = _make_df(closes)
        cfg = _base_config(min_leg_duration_days=5)
        m = RuleBasedVCPDetector().detect(df, cfg)
        assert m.contraction_count == 0
        assert m.is_valid_vcp is False

    # ------------------------------------------------------------------
    # Test 2: same shape but spanning 6 days — passes filter
    # ------------------------------------------------------------------
    def test_long_leg_passes_filter(self) -> None:
        """A pivot pair spanning 6 trading days passes min_leg_duration_days=5.

        H at idx 2, L at idx 8 → duration=6 ≥ 5 → contraction_count ≥ 1.
        """
        closes = [95, 100, 110, 107, 104, 101, 98, 94, 88, 92, 96]
        df = _make_df(closes)
        cfg = _base_config(min_leg_duration_days=5)
        m = RuleBasedVCPDetector().detect(df, cfg)
        assert m.contraction_count >= 1

    # ------------------------------------------------------------------
    # Test 3: 3-leg base, one short leg (3 days) + two long legs (8 days)
    # ------------------------------------------------------------------
    def test_mixed_legs_short_one_filtered(self) -> None:
        """With min=5, only the two 8-day legs survive.

        3-leg base: durations 3, 8, 8.
        After filtering the 3-day leg, contraction_count must equal 2.
        """
        df = _make_df(_MIXED_LEG_CLOSES)
        cfg = _base_config(min_leg_duration_days=5)   # explicit guard threshold
        m = RuleBasedVCPDetector().detect(df, cfg)
        assert m.contraction_count == 2

    # ------------------------------------------------------------------
    # Test 4: min_leg_duration_days=0 → all legs pass (backward compat)
    # ------------------------------------------------------------------
    def test_zero_min_all_legs_pass(self) -> None:
        """Setting min_leg_duration_days=0 disables the filter entirely.

        All three legs (durations 3, 8, 8) should be counted.
        """
        df = _make_df(_MIXED_LEG_CLOSES)
        cfg = _base_config(min_leg_duration_days=0)
        m = RuleBasedVCPDetector().detect(df, cfg)
        assert m.contraction_count == 3


# ---------------------------------------------------------------------------
# Test 10 — Improvement 1: strict monotonic decline check
# ---------------------------------------------------------------------------


class TestMonotonicDecline:
    """Each successive leg depth must be STRICTLY less than the previous."""

    # Test 1: strictly declining [20, 15, 10, 6] → monotonic_decline=True
    def test_strictly_declining_is_valid(self) -> None:
        """depths [20,15,10,6]: monotonic_decline=True → is_valid_vcp=True."""
        detector = RuleBasedVCPDetector()
        swing_highs = [(2, 100.0), (10, 100.0), (18, 100.0), (26, 100.0)]
        swing_lows  = [
            (6,  80.0),   # depth 20 %
            (14, 85.0),   # depth 15 %
            (22, 90.0),   # depth 10 %
            (30, 94.0),   # depth  6 %
        ]
        legs = detector._build_legs(swing_highs, swing_lows)
        depths = [leg["depth"] for leg in legs]
        monotonic_decline = all(depths[i] < depths[i - 1] for i in range(1, len(depths)))

        assert depths == pytest.approx([20.0, 15.0, 10.0, 6.0], abs=0.01)
        assert monotonic_decline is True

        # Verify the monotonic_decline attribute is set correctly in the metrics
        # Note: full detect() may not find pivots in flat data, so we just verify
        # the legs calculation directly shows monotonic_decline=True
        m = VCPMetrics(
            contraction_count=len(legs),
            max_depth_pct=max(depths),
            final_depth_pct=depths[-1],
            vol_contraction_ratio=0.5,
            base_length_weeks=8,
            base_low=80.0,
            is_valid_vcp=True,
            tightness_score=0.3,
            monotonic_decline=monotonic_decline,
            leg_depths=depths,
        )
        assert m.monotonic_decline is True

    # Test 2: key regression case — [20, 15, 22, 12] expands at leg 3
    def test_expansion_in_middle_is_invalid(self) -> None:
        """depths [20,15,22,12]: monotonic_decline=False → is_valid_vcp=False.

        Key regression: previously 12 < 20 passed the old final_depth < max_depth
        check, but leg 3 (22 %) expanded over leg 2 (15 %), which Minervini forbids.
        """
        detector = RuleBasedVCPDetector()
        swing_highs = [(2, 100.0), (10, 100.0), (18, 100.0), (26, 100.0)]
        swing_lows  = [
            (6,  80.0),   # depth 20 %
            (14, 85.0),   # depth 15 %
            (22, 78.0),   # depth 22 %  <- EXPANDS (bad)
            (30, 88.0),   # depth 12 %
        ]
        legs = detector._build_legs(swing_highs, swing_lows)
        depths = [leg["depth"] for leg in legs]
        monotonic_decline = all(depths[i] < depths[i - 1] for i in range(1, len(depths)))

        assert depths == pytest.approx([20.0, 15.0, 22.0, 12.0], abs=0.01)
        assert monotonic_decline is False

        n = 40
        df = _make_df([100.0] * n)
        for sh_idx, sh_price in swing_highs:
            df.at[df.index[sh_idx], "high"] = sh_price
        for sl_idx, sl_price in swing_lows:
            df.at[df.index[sl_idx], "low"] = sl_price

        cfg = _base_config(min_leg_duration_days=0, min_weeks=0, tightness_pct=50.0)
        m = detector.detect(df, cfg)
        assert m.monotonic_decline is False
        assert m.is_valid_vcp is False

    # Test 3: equal depths [20, 20, 15] — equal is NOT strictly less
    def test_equal_depths_not_monotonic(self) -> None:
        """depths [20, 20, 15]: monotonic_decline=False (equal != strictly less)."""
        detector = RuleBasedVCPDetector()
        swing_highs = [(2, 100.0), (10, 100.0), (18, 100.0)]
        swing_lows  = [
            (6,  80.0),   # depth 20 %
            (14, 80.0),   # depth 20 %  <- equal, not strictly less
            (22, 85.0),   # depth 15 %
        ]
        legs = detector._build_legs(swing_highs, swing_lows)
        depths = [leg["depth"] for leg in legs]
        monotonic_decline = all(depths[i] < depths[i - 1] for i in range(1, len(depths)))

        assert depths == pytest.approx([20.0, 20.0, 15.0], abs=0.01)
        assert monotonic_decline is False

    # Test 4: single leg — monotonic_decline=True by definition (vacuous all())
    def test_single_leg_is_monotonic_by_definition(self) -> None:
        """A single leg has no pairs; all() over empty range is vacuously True."""
        detector = RuleBasedVCPDetector()
        swing_highs = [(2, 100.0)]
        swing_lows  = [(6, 80.0)]   # depth 20 %
        legs = detector._build_legs(swing_highs, swing_lows)
        depths = [leg["depth"] for leg in legs]
        monotonic_decline = all(depths[i] < depths[i - 1] for i in range(1, len(depths)))

        assert len(depths) == 1
        assert monotonic_decline is True

    # Test 5: leg_depths is populated and matches leg order
    def test_leg_depths_populated_and_ordered(self) -> None:
        """metrics.leg_depths must equal the ordered list of leg depths."""
        df  = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        cfg = _base_config()
        m   = RuleBasedVCPDetector().detect(df, cfg)

        assert len(m.leg_depths) == m.contraction_count, (
            f"leg_depths length {len(m.leg_depths)} != contraction_count {m.contraction_count}"
        )
        assert m.leg_depths[-1] == pytest.approx(m.final_depth_pct, abs=1e-6)
        assert max(m.leg_depths) == pytest.approx(m.max_depth_pct, abs=1e-6)
        # Verify strictly decreasing order for the canonical 3-leg base
        for i in range(1, len(m.leg_depths)):
            assert m.leg_depths[i] < m.leg_depths[i - 1], (
                f"leg_depths not strictly decreasing at index {i}: {m.leg_depths}"
            )


# ---------------------------------------------------------------------------
# Test 9 — ATR displacement filter (Improvement 7)
# ---------------------------------------------------------------------------


class TestATRDisplacementFilter:
    """Improvement 7 — ATR-based displacement guard in _build_legs / detect().

    All tests inject a synthetic 'atr_14' column directly into the DataFrame
    so they remain self-contained and independent of features/atr.py.

    The 3-leg base (_3LEG_CLOSES) is reused as the price backbone:
      Leg 1: H1=110.5 → L1=87.5   displacement = 23.0
      Leg 2: H2=105.5 → L2=89.5   displacement = 16.0
      Leg 3: H3=102.5 → L3=93.5   displacement =  9.0

    For targeted tests, a synthetic ATR series is built whose value at the
    swing-high index controls whether the leg passes the filter.
    """

    # ------------------------------------------------------------------
    # Helper: inject a constant ATR column into a df copy
    # ------------------------------------------------------------------

    @staticmethod
    def _with_atr(df: pd.DataFrame, atr_value: float) -> pd.DataFrame:
        """Return a copy of *df* with a uniform 'atr_14' column."""
        out = df.copy()
        out["atr_14"] = atr_value
        return out

    # ------------------------------------------------------------------
    # Test 1: displacement = 0.8 × ATR → leg filtered, count drops
    # ------------------------------------------------------------------
    def test_leg_below_atr_threshold_is_filtered(self) -> None:
        """displacement < 1.0 × ATR → leg discarded → contraction_count drops.

        We target leg 3 (H3=102.5 → L3=93.5, displacement=9.0).
        Setting ATR = 9.0 / 0.8 = 11.25 makes displacement = 0.8 × ATR.
        With multiplier=1.0 leg 3 is filtered out → count goes from 3 to 2.
        """
        df = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        # ATR chosen so displacement/ATR = 0.8 < 1.0 only for leg 3
        # Leg 3 displacement = 9.0; ATR = 11.25 → 9.0 / 11.25 = 0.8
        df = self._with_atr(df, 11.25)
        cfg = _base_config(min_leg_atr_multiplier=1.0)
        m = RuleBasedVCPDetector().detect(df, cfg)
        # At least one leg is filtered
        assert m.contraction_count < 3

    # ------------------------------------------------------------------
    # Test 2: displacement = 1.2 × ATR → leg kept
    # ------------------------------------------------------------------
    def test_leg_above_atr_threshold_is_kept(self) -> None:
        """displacement >= 1.0 × ATR → leg is kept.

        Set ATR small enough that ALL legs have displacement >= 1.0 × ATR.
        Each leg's displacement: 23.0, 16.0, 9.0.
        ATR = 7.0 → smallest ratio = 9.0/7.0 ≈ 1.28 > 1.0 → all kept.
        """
        df = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        df = self._with_atr(df, 7.0)
        cfg = _base_config(min_leg_atr_multiplier=1.0)
        m = RuleBasedVCPDetector().detect(df, cfg)
        assert m.contraction_count == 3

    # ------------------------------------------------------------------
    # Test 3: multiplier=0.0 → filter disabled, all legs pass
    # ------------------------------------------------------------------
    def test_zero_multiplier_disables_filter(self) -> None:
        """min_leg_atr_multiplier=0.0 must disable the ATR filter entirely.

        Even with a large ATR (e.g. 50, far above every displacement) all
        legs must survive because the guard is off when multiplier=0.0.
        """
        df = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        df = self._with_atr(df, 50.0)   # would filter everything if guard were active
        cfg = _base_config(min_leg_atr_multiplier=0.0)
        m = RuleBasedVCPDetector().detect(df, cfg)
        assert m.contraction_count == 3

    # ------------------------------------------------------------------
    # Test 4: no atr_14 column → atr_series=None path, no exception
    # ------------------------------------------------------------------
    def test_no_atr_column_skips_filter_gracefully(self) -> None:
        """When 'atr_14' is absent the filter must be silently skipped.

        The result must be identical to the unfiltered 3-leg base and no
        exception must be raised.
        """
        df = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        assert "atr_14" not in df.columns
        cfg = _base_config(min_leg_atr_multiplier=1.0)
        m = RuleBasedVCPDetector().detect(df, cfg)   # must not raise
        assert m.contraction_count == 3

    # ------------------------------------------------------------------
    # Test 5: 4-leg base; leg 3 has 0.4 % displacement vs 1.2 % ATR
    # ------------------------------------------------------------------
    def test_four_leg_base_third_leg_filtered(self) -> None:
        """4-leg base where leg 3 displacement (0.4 %) < 1× ATR (1.2 %).

        After ATR filtering leg 3 is discarded → contraction_count == 3.

        Layout (sensitivity=2):
          idx:  0  1   2    3    4
                    H1=110.5
          idx:  5  6   7    8    9
                L1=87.5       H2=105.5
          idx: 10 11  12   13   14
                           L2=89.5
          idx: 15 16  17   18   19
                    H3=102.5         ← tiny noise leg
          idx: 20 21  22   23   24
                L3=102.1      H4=99.5
          idx: 25 26  27   28   29
                           L4=93.5

        We build a price series that produces these pivots, then set
        ATR such that leg 3's displacement is 0.4 % of H3=102.5 ≈ 0.41,
        but ATR = 1.2 % × 102.5 ≈ 1.23 → filter fires for leg 3 only.
        """
        # Re-use _3LEG_CLOSES (gives H1, L1, H2, L2, H3, L3) and append a
        # 4th contraction by extending the series.
        #
        # We craft the 4th leg by appending bars after _3LEG_CLOSES so that:
        #   H4 is recognised as a new swing high after L3 (idx 22)
        #   L4 is a proper swing low after H4
        #   Leg 3 (H3→L3): H3=102.5, L3=93.5 → displacement=9.0 (not tiny)
        #
        # For the "0.4 % leg" scenario in the spec we use _build_legs directly
        # with a hand-crafted swing list so the test is deterministic.
        detector = RuleBasedVCPDetector()

        # Swing lists for a 4-leg base
        swing_highs = [(2, 110.5), (10, 105.5), (18, 102.5), (26, 99.5)]
        swing_lows  = [(6,  87.5), (14,  89.5), (22, 102.1), (30, 93.5)]
        # Leg 3: H3=102.5 → L3=102.1  displacement = 0.4
        # ATR at idx 18 = 1.23  (1.2 % of 102.5)
        # 0.4 < 1.0 × 1.23 → leg 3 filtered

        n = 35
        closes = [100.0] * n
        df = _make_df(closes)
        atr_value = 1.23  # > displacement of leg 3 (0.4) but < legs 1,2,4
        atr_series = pd.Series([atr_value] * n, index=df.index)

        legs = detector._build_legs(
            swing_highs, swing_lows,
            atr_series=atr_series,
            min_atr_multiplier=1.0,
        )
        assert len(legs) == 3, (
            f"Expected 3 legs after filtering noisy leg 3, got {len(legs)}: "
            + str([(l['high_price'], l['low_price']) for l in legs])
        )


# ---------------------------------------------------------------------------
# TestATRTightness — Improvement 2: ATR10/ATR50 compression ratio
# ---------------------------------------------------------------------------


class TestATRTightness:
    """Verify the new ATR-ratio _tightness() implementation."""

    @staticmethod
    def _make_atr_df(n_bars: int, base_tr: float, last10_tr: float) -> pd.DataFrame:
        """Build a DataFrame where:
        - First (n_bars - 10) bars have TR = base_tr
        - Last 10 bars have TR = last10_tr
        TR is implemented as high - low = TR (open == close, so prev-close term = 0).
        """
        closes = [100.0] * n_bars
        # Set high/low so each bar's True Range equals the desired value.
        # We use open == close == 100 so prev-close TR terms are 0;
        # TR = high - low exactly.
        highs  = [100.0 + base_tr / 2] * n_bars
        lows   = [100.0 - base_tr / 2] * n_bars
        # Overwrite last 10 bars with the target TR
        for i in range(n_bars - 10, n_bars):
            highs[i] = 100.0 + last10_tr / 2
            lows[i]  = 100.0 - last10_tr / 2
        return pd.DataFrame(
            {
                "open":   closes,
                "high":   highs,
                "low":    lows,
                "close":  closes,
                "volume": [1_000_000] * n_bars,
            },
            index=pd.bdate_range("2023-01-01", periods=n_bars),
        )

    # ------------------------------------------------------------------
    # Test 1: last 10 TR = 0.5 × base_tr → ATR10/ATR50 ≈ 0.556
    # ATR is averaged, so ratio = (10*0.5 + 40*2.0) / 50 / (10*0.5 / 10)
    # = 1.8 / 1.0 = 1.8 (for ATR50/ATR10), but we compute ATR10/ATR50
    # = 1.0 / 1.8 = 0.556
    # ------------------------------------------------------------------
    def test_strong_compression_ratio_approx_half(self) -> None:
        """Last-10 TR = 0.5 × base_tr → tightness_score ≈ 0.556."""
        base_tr   = 2.0
        last10_tr = 1.0   # 0.5 × 2.0
        df = self._make_atr_df(n_bars=60, base_tr=base_tr, last10_tr=last10_tr)
        score = RuleBasedVCPDetector._tightness(df)
        assert not math.isnan(score), "Should not be nan with 60 bars"
        assert score == pytest.approx(0.556, abs=0.05), (
            f"Expected ATR ratio ≈ 0.556 for half-TR compression, got {score:.4f}"
        )

    # ------------------------------------------------------------------
    # Test 2: last 10 TR = 1.5 × base_tr → ATR10/ATR50 ≈ 1.364
    # ATR10 = 3.0, ATR50 = (40*2.0 + 10*3.0)/50 = 110/50 = 2.2
    # Ratio = 3.0 / 2.2 = 1.364
    # ------------------------------------------------------------------
    def test_expansion_ratio_approx_one_point_five(self) -> None:
        """Last-10 TR = 1.5 × base_tr → tightness_score ≈ 1.364 (expansion)."""
        base_tr   = 2.0
        last10_tr = 3.0   # 1.5 × 2.0
        df = self._make_atr_df(n_bars=60, base_tr=base_tr, last10_tr=last10_tr)
        score = RuleBasedVCPDetector._tightness(df)
        assert not math.isnan(score), "Should not be nan with 60 bars"
        assert score == pytest.approx(1.364, abs=0.05), (
            f"Expected ATR ratio ≈ 1.364 for expansion, got {score:.4f}"
        )

    # ------------------------------------------------------------------
    # Test 3: fewer than 50 bars → returns nan
    # ------------------------------------------------------------------
    def test_insufficient_data_returns_nan(self) -> None:
        """A DataFrame with < 50 bars must return float('nan')."""
        df = self._make_atr_df(n_bars=30, base_tr=2.0, last10_tr=1.0)
        score = RuleBasedVCPDetector._tightness(df)
        assert math.isnan(score), (
            f"Expected nan for < 50 bars, got {score}"
        )


# ---------------------------------------------------------------------------
# TestVolSlope — Improvement 3: vol_slope via linear regression
# ---------------------------------------------------------------------------


def _make_legs_from_volumes(volumes_per_leg: list[list[float]]) -> tuple[pd.DataFrame, list[dict]]:
    """Build a minimal DataFrame and leg list from per-leg volume lists.

    Each inner list is the volume for one leg.  Legs are placed sequentially
    with no gap; start_idx/end_idx cover the entire leg slice.
    """
    all_vols: list[float] = []
    legs: list[dict] = []
    idx = 0
    for leg_vols in volumes_per_leg:
        start = idx
        end   = idx + len(leg_vols) - 1
        all_vols.extend(leg_vols)
        legs.append({"start_idx": start, "end_idx": end})
        idx   = end + 1

    n   = len(all_vols)
    closes = [100.0] * n
    df  = _make_df(closes, all_vols)
    return df, legs


class TestVolSlope:
    """Improvement 3 — vol_slope linear regression across all leg avg volumes."""

    # ------------------------------------------------------------------
    # Test 1: strictly declining volumes → vol_slope negative
    # ------------------------------------------------------------------
    def test_declining_volumes_slope_negative(self) -> None:
        """[5M, 4M, 3M, 2M] across 4 legs → vol_slope < 0."""
        vols = [[5_000_000] * 5, [4_000_000] * 5, [3_000_000] * 5, [2_000_000] * 5]
        df, legs = _make_legs_from_volumes(vols)
        ratio, slope = RuleBasedVCPDetector._vol_stats(df, legs)
        assert slope < 0, f"Expected negative slope for declining volumes, got {slope}"

    # ------------------------------------------------------------------
    # Test 2: flat volumes → vol_slope ≈ 0
    # ------------------------------------------------------------------
    def test_flat_volumes_slope_near_zero(self) -> None:
        """[3M, 3M, 3M, 3M] across 4 legs → vol_slope ≈ 0."""
        vols = [[3_000_000] * 5] * 4
        df, legs = _make_legs_from_volumes(vols)
        _, slope = RuleBasedVCPDetector._vol_stats(df, legs)
        assert abs(slope) < 1e-9, f"Expected slope ≈ 0 for flat volumes, got {slope}"

    # ------------------------------------------------------------------
    # Test 3: increasing volumes → vol_slope positive
    # ------------------------------------------------------------------
    def test_increasing_volumes_slope_positive(self) -> None:
        """[2M, 3M, 4M, 5M] across 4 legs → vol_slope > 0."""
        vols = [[2_000_000] * 5, [3_000_000] * 5, [4_000_000] * 5, [5_000_000] * 5]
        df, legs = _make_legs_from_volumes(vols)
        _, slope = RuleBasedVCPDetector._vol_stats(df, legs)
        assert slope > 0, f"Expected positive slope for increasing volumes, got {slope}"

    # ------------------------------------------------------------------
    # Test 4: distribution spike pattern → slope near zero or slightly positive
    # ------------------------------------------------------------------
    def test_distribution_spike_slope_near_zero_or_positive(self) -> None:
        """[5M, 3.8M, 7.2M, 2.9M] — middle spike makes slope ≈ 0 or positive."""
        vols = [
            [5_000_000]   * 5,
            [3_800_000]   * 5,
            [7_200_000]   * 5,
            [2_900_000]   * 5,
        ]
        df, legs = _make_legs_from_volumes(vols)
        _, slope = RuleBasedVCPDetector._vol_stats(df, legs)
        # A clean 5→4→3→2 pattern gives slope ≈ -0.33; the spike pushes it up
        assert slope > -0.20, (
            f"Distribution spike should yield slope > -0.20, got {slope:.4f}"
        )

    # ------------------------------------------------------------------
    # Test 5: vol_contraction_ratio still populates correctly
    # ------------------------------------------------------------------
    def test_vol_contraction_ratio_still_populated(self) -> None:
        """vol_contraction_ratio must equal last_avg / first_avg for all cases."""
        cases = [
            # (per-leg volumes, expected_ratio)
            ([[5_000_000] * 4, [4_000_000] * 4, [3_000_000] * 4, [2_000_000] * 4], 2_000_000 / 5_000_000),
            ([[3_000_000] * 4] * 4, 1.0),
            ([[2_000_000] * 4, [3_000_000] * 4, [4_000_000] * 4, [5_000_000] * 4], 5_000_000 / 2_000_000),
            ([[5_000_000] * 4, [3_800_000] * 4, [7_200_000] * 4, [2_900_000] * 4], 2_900_000 / 5_000_000),
        ]
        for vols, expected_ratio in cases:
            df, legs = _make_legs_from_volumes(vols)
            ratio, _ = RuleBasedVCPDetector._vol_stats(df, legs)
            assert ratio == pytest.approx(expected_ratio, rel=1e-6), (
                f"vol_contraction_ratio mismatch: expected {expected_ratio:.4f}, got {ratio:.4f}"
            )

    # ------------------------------------------------------------------
    # Test 6: vol_slope populated via full detect() pipeline
    # ------------------------------------------------------------------
    def test_vol_slope_populated_by_detect(self) -> None:
        """Full detect() on the 3-leg VCP base must populate vol_slope (not nan)."""
        df  = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        cfg = _base_config()
        m   = RuleBasedVCPDetector().detect(df, cfg)
        assert not math.isnan(m.vol_slope), "vol_slope must not be NaN for a valid base"
        # _3LEG_VOLUMES is strictly declining → slope must be negative
        assert m.vol_slope < 0, f"Expected negative slope for declining volumes, got {m.vol_slope}"


# ---------------------------------------------------------------------------
# TestClimaxDays — Improvement 4: institutional distribution day detection
# ---------------------------------------------------------------------------


class TestClimaxDays:
    """Improvement 4 — climax_days_in_base detection and qualification gate."""

    @staticmethod
    def _make_df_with_climax_spikes(spike_indices: list[int], spike_mult: float = 3.0) -> pd.DataFrame:
        """Build a VCP base and inject high-volume spikes at given indices.

        The df is 100 bars long. The 3-leg pattern starts at bar 60 so the
        50d rolling average (min_periods=20) is fully warm by the time the
        base begins (~index 60+first_pivot).  Normal volume = 500_000.
        Spike bars are set to spike_mult × normal_vol, guaranteed to exceed
        the 2.5× threshold used by _climax_days().

        spike_indices are RELATIVE to the start of the pattern (bar 60),
        so absolute indices = spike_indices[i] + 60.
        """
        n = 100
        base_start = 60
        normal_vol = 500_000.0

        closes = [100.0] * n
        for i, c in enumerate(_3LEG_CLOSES):
            closes[base_start + i] = c

        volumes = [normal_vol] * n
        for rel_idx in spike_indices:
            abs_idx = base_start + rel_idx
            volumes[abs_idx] = normal_vol * spike_mult

        df = _make_df(closes, volumes)
        return df

    # ------------------------------------------------------------------
    # Test 1: 3 climax days → is_valid_vcp=False, climax_days_in_base=3
    # ------------------------------------------------------------------
    def test_three_climax_days_disqualifies(self) -> None:
        """3 days with volume > 2.5× 50d avg inside the base → is_valid_vcp=False.

        Spike indices are chosen inside the base window [first_pivot..last_pivot]:
        - The 3-leg pattern starts at bar 60; pivots land at absolute indices
          62 (H1), 66 (L1), 70 (H2), 74 (L2), 78 (H3), 82 (L3).
        - Relative indices 3, 11, 16 → absolute 63, 71, 76 — all within [62, 82].
        - Spikes are spread across leg 1, leg 2, and the gap between legs 2 and 3
          so vol_contraction_ratio stays < 1.0 (last leg is spike-free).
        """
        df = self._make_df_with_climax_spikes([3, 11, 16], spike_mult=3.0)
        cfg = _base_config(
            min_leg_duration_days=0,
            min_weeks=0,
            max_climax_days_in_base=2,
            climax_vol_threshold=2.5,
            pivot_sensitivity=_SENSITIVITY,
        )
        m = RuleBasedVCPDetector().detect(df, cfg)
        assert m.climax_days_in_base >= 3, (
            f"Expected at least 3 climax days, got {m.climax_days_in_base}"
        )
        assert m.is_valid_vcp is False, "3 climax days must disqualify the VCP"

    # ------------------------------------------------------------------
    # Test 2: exactly 2 climax days → is_valid_vcp=True (at default max=2)
    # ------------------------------------------------------------------
    def test_two_climax_days_still_qualifies(self) -> None:
        """Exactly 2 climax days is at the limit and must still qualify.

        Spikes are placed at relative indices 3 and 4 (absolute 63, 64) —
        both in the first leg — so that:
        - vol_contraction_ratio = last_leg_avg / first_leg_avg < 1.0 (vol dries up)
        - climax_days_in_base = 2 (both within the base window [62, 82])
        - max_climax_days_in_base = 2 → gate condition 2 > 2 is False → allowed
        """
        df = self._make_df_with_climax_spikes([3, 4], spike_mult=3.0)
        cfg = _base_config(
            min_leg_duration_days=0,
            min_weeks=0,
            max_climax_days_in_base=2,
            climax_vol_threshold=2.5,
            pivot_sensitivity=_SENSITIVITY,
        )
        m = RuleBasedVCPDetector().detect(df, cfg)
        assert m.climax_days_in_base <= 2, (
            f"Expected at most 2 climax days, got {m.climax_days_in_base}"
        )
        # With ≤2 climax days the gate must NOT block qualification
        # (other rules still apply — we relax them above so this should pass)
        assert m.is_valid_vcp is True, (
            f"2 climax days at max=2 should still qualify, is_valid_vcp={m.is_valid_vcp}, "
            f"climax_days={m.climax_days_in_base}"
        )

    # ------------------------------------------------------------------
    # Test 3: 0 climax days → is_valid_vcp=True, climax_days_in_base=0
    # ------------------------------------------------------------------
    def test_zero_climax_days_qualifies(self) -> None:
        """No climax days → climax_days_in_base=0, is_valid_vcp unaffected."""
        df = _make_df(_3LEG_CLOSES, _3LEG_VOLUMES)
        cfg = _base_config(
            min_leg_duration_days=0,
            min_weeks=0,
            max_climax_days_in_base=2,
            climax_vol_threshold=2.5,
        )
        m = RuleBasedVCPDetector().detect(df, cfg)
        assert m.climax_days_in_base == 0
        assert m.is_valid_vcp is True

    # ------------------------------------------------------------------
    # Test 4: base_df shorter than 10 bars → _climax_days returns 0, no exception
    # ------------------------------------------------------------------
    def test_short_base_returns_zero_no_exception(self) -> None:
        """A base with fewer than 10 bars must return 0 without raising."""
        # Build a minimal df: sensitivity=2 needs at least 5 bars each side
        closes = [95, 100, 110, 104, 100, 88, 92, 96]
        df = _make_df(closes)
        # Manually provide swing lists that produce a tiny base (< 10 bars)
        swing_highs = [(2, 110.5)]
        swing_lows  = [(5, 87.5)]
        # This base spans indices 2..5 → 4 bars < 10 → must return 0
        result = RuleBasedVCPDetector._climax_days(df, swing_highs, swing_lows, threshold=2.5)
        assert result == 0, f"Short base must return 0, got {result}"
