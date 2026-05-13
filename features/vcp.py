"""
features/vcp.py
---------------
Volatility Contraction Pattern (VCP) detection for the Minervini SEPA screener.

A VCP is a base pattern where price contracts in successively smaller waves
(tighter swings, lower volume) before a breakout.  The rule engine uses
VCPMetrics to decide if a setup qualifies.

Architecture
------------
* VCPDetector (ABC) — defines the detection interface.
* RuleBasedVCPDetector — default implementation using find_all_pivots().
* DETECTORS registry + get_detector() factory — allows strategy injection.
* compute() — top-level feature-pipeline entry point.

Implements the standard compute interface contract:
  - Pure function: no I/O, no side effects, no global state.
  - Appends indicator columns to the input DataFrame and returns it.
  - Gracefully fills NaN / False on detection failure.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from features.pivot import find_all_pivots
from utils.exceptions import ConfigurationError
from utils.logger import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------


@dataclass
class VCPMetrics:
    """All scalar measurements describing a VCP base.

    Every field is broadcast to every row of the output DataFrame so that
    downstream consumers (rule engine, screener) can filter on any field
    without special-casing scalars vs. series.
    """

    contraction_count: int          # number of swing-to-swing contractions detected
    max_depth_pct: float            # deepest correction in the base (%)
    final_depth_pct: float          # shallowest / most recent correction (%)
    vol_contraction_ratio: float    # volume in last leg / first leg (< 1 = drying up)
    base_length_weeks: int          # total width of the base in calendar weeks
    base_low: float                 # lowest low in the entire base (stop-loss floor)
    is_valid_vcp: bool              # True when ALL qualification rules pass
    tightness_score: float          # ATR₁₀/ATR₅₀ compression ratio (< 0.75 to qualify; lower = tighter)
    monotonic_decline: bool = False # True when each leg depth < previous leg depth
    leg_depths: list = field(default_factory=list)  # ordered list of all leg depths
    vol_slope: float = float('nan')  # linear regression slope across all leg avg volumes
    climax_days_in_base: int = 0    # days inside the base with volume > threshold × 50d avg


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------


class VCPDetector(ABC):
    """Interface every VCP detector must implement."""

    @abstractmethod
    def detect(self, df: pd.DataFrame, config: dict) -> VCPMetrics:
        """Analyse *df* and return a fully-populated VCPMetrics instance.

        Parameters
        ----------
        df:
            Cleaned OHLCV DataFrame with a DatetimeIndex and columns:
            ``open``, ``high``, ``low``, ``close``, ``volume``.
        config:
            Screening configuration dict (the vcp section is consumed here).

        Returns
        -------
        VCPMetrics
        """


# ---------------------------------------------------------------------------
# VCP qualification rule engine
# ---------------------------------------------------------------------------


def _apply_vcp_rules(metrics: VCPMetrics, config: dict) -> bool:
    """Return True when *metrics* satisfies all VCP qualification rules.

    All thresholds are read from *config* so nothing is hardcoded here.
    """
    vcp = config.get("vcp", {})

    min_contractions: int   = vcp.get("min_contractions", 2)
    max_contractions: int   = vcp.get("max_contractions", 5)
    require_vol: bool        = vcp.get("require_vol_contraction", True)
    min_weeks: int           = vcp.get("min_weeks", 3)
    max_weeks: int           = vcp.get("max_weeks", 52)
    atr_threshold: float     = vcp.get("tightness_pct", 0.75)
    max_depth_pct: float     = vcp.get("max_depth_pct", 50.0)

    if not (min_contractions <= metrics.contraction_count <= max_contractions):
        return False
    if not metrics.monotonic_decline:                                     # each leg shallower
        return False
    if require_vol and metrics.vol_contraction_ratio >= 1.0:          # volume drying up
        return False
    if not (min_weeks <= metrics.base_length_weeks <= max_weeks):
        return False
    # nan means insufficient data (< 50 bars) — skip the gate rather than failing.
    # Only reject when we have enough data and compression is not present.
    if not math.isnan(metrics.tightness_score) and metrics.tightness_score >= atr_threshold:
        return False
    if metrics.max_depth_pct > max_depth_pct:
        return False
    max_climax_days: int = vcp.get('max_climax_days_in_base', 2)
    if metrics.climax_days_in_base > max_climax_days:
        return False
    return True


# ---------------------------------------------------------------------------
# Default implementation
# ---------------------------------------------------------------------------


class RuleBasedVCPDetector(VCPDetector):
    """Default VCP detector.

    Uses find_all_pivots() from features/pivot.py — never inlines pivot logic.

    Algorithm (10 steps as documented in the module-level docstring):
      1.  Call find_all_pivots() → (swing_highs, swing_lows)
      2.  Pair each swing high with the first subsequent swing low → legs
      3.  contraction_count = len(legs)
      4.  max_depth_pct     = deepest leg depth (%)
      5.  final_depth_pct   = last leg depth (%)
      6.  vol_contraction_ratio = avg volume of last leg / avg volume of first leg
      7.  base_length_weeks = (last pivot date - first pivot date).days // 7
      8.  base_low          = min(df["low"]) over the base date range
      9.  tightness_score   = ATR₁₀ / ATR₅₀  (ATR compression ratio; NaN if < 50 bars)
      10. is_valid_vcp      = _apply_vcp_rules(metrics, config)
    """

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def detect(self, df: pd.DataFrame, config: dict) -> VCPMetrics:
        vcp_cfg = config.get("vcp", {})
        sensitivity: int = vcp_cfg.get("pivot_sensitivity", 5)

        swing_highs, swing_lows = find_all_pivots(df, sensitivity=sensitivity)

        min_atr_multiplier: float = vcp_cfg.get('min_leg_atr_multiplier', 1.0)
        atr_series = df['atr_14'] if 'atr_14' in df.columns else None
        legs_raw = self._build_legs(
            swing_highs,
            swing_lows,
            atr_series=atr_series,
            min_atr_multiplier=min_atr_multiplier,
        )
        min_leg_days = vcp_cfg.get('min_leg_duration_days', 5)
        legs = [leg for leg in legs_raw if leg['duration_days'] >= min_leg_days]
        log.debug(
            "RuleBasedVCPDetector: leg filter raw=%d kept=%d min_leg_days=%d",
            len(legs_raw), len(legs), min_leg_days,
        )
        contraction_count = len(legs)

        log.debug(
            "RuleBasedVCPDetector: rows=%d sensitivity=%d legs=%d",
            len(df), sensitivity, contraction_count,
        )

        if contraction_count == 0:
            return self._empty_metrics(df)

        depths = [leg["depth"] for leg in legs]
        max_depth_pct   = max(depths)
        final_depth_pct = depths[-1]
        monotonic_decline = all(depths[i] < depths[i-1] for i in range(1, len(depths)))

        vol_contraction_ratio, vol_slope = self._vol_stats(df, legs)
        # Use leg-bounded indices for base measurements so that noise pivots
        # filtered out by the ATR/duration guards cannot inflate the base.
        base_start, base_end  = self._leg_index_range(legs)
        base_length_weeks     = self._base_weeks(df, base_start, base_end)
        base_low              = self._base_low(df, base_start, base_end)
        tightness_score       = self._tightness(df)
        climax_threshold = vcp_cfg.get('climax_vol_threshold', 2.5)
        climax_days = self._climax_days(df, base_start, base_end, threshold=climax_threshold)

        metrics = VCPMetrics(
            contraction_count     = contraction_count,
            max_depth_pct         = max_depth_pct,
            final_depth_pct       = final_depth_pct,
            vol_contraction_ratio = vol_contraction_ratio,
            base_length_weeks     = base_length_weeks,
            base_low              = base_low,
            is_valid_vcp          = False,   # filled below
            tightness_score       = tightness_score,
            monotonic_decline     = monotonic_decline,
            leg_depths            = depths,
            vol_slope             = vol_slope,
            climax_days_in_base   = climax_days,
        )
        metrics.is_valid_vcp = _apply_vcp_rules(metrics, config)
        return metrics


    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_legs(
        swing_highs: list[tuple[int, float]],
        swing_lows:  list[tuple[int, float]],
        atr_series: pd.Series | None = None,
        min_atr_multiplier: float = 0.0,
    ) -> list[dict]:
        """Pair each swing high with the next swing low to form contraction legs.

        A leg captures: start index, end index, high price, low price,
        depth percentage correction, and duration_days (sl_idx - sh_idx).

        When *atr_series* is provided and *min_atr_multiplier* > 0.0, legs
        whose price displacement (sh_price - sl_price) is smaller than
        ``min_atr_multiplier × ATR_at_swing_high`` are discarded.  This
        removes noise legs that span enough calendar days but barely move
        in price.  The defaults keep the filter off — fully backward-compatible.
        """
        legs: list[dict] = []
        # Track which swing-low indices have already been claimed by a high so that
        # each low can only anchor ONE leg.  This prevents two highs from sharing
        # the same low when a noise micro-pullback is skipped via ATR filtering.
        used_low_indices: set[int] = set()

        for sh_idx, sh_price in swing_highs:
            # Search for the earliest unclaimed swing low strictly after this high.
            # When ATR filtering is active we skip (continue past) noise lows whose
            # price displacement is too small — instead of aborting the whole search
            # for this high (old `break`), we look for the next meaningful low.
            for sl_idx, sl_price in swing_lows:
                if sl_idx <= sh_idx:
                    continue  # low is before this high — keep scanning
                if sl_idx in used_low_indices:
                    continue  # low already claimed by a preceding high

                # ATR displacement guard — only active when both parameters are set
                if atr_series is not None and min_atr_multiplier > 0.0:
                    displacement = sh_price - sl_price
                    atr_at_high = float(atr_series.iloc[sh_idx])
                    if (
                        not math.isnan(atr_at_high)
                        and atr_at_high > 0
                        and displacement < min_atr_multiplier * atr_at_high
                    ):
                        # Noise micro-pullback: skip this low and try the next one.
                        # (Previously `break` here made us abandon the entire high,
                        # causing valid deeper lows to be missed entirely.)
                        continue

                depth = (sh_price - sl_price) / sh_price * 100.0
                used_low_indices.add(sl_idx)
                legs.append(
                    dict(
                        start_idx     = sh_idx,
                        end_idx       = sl_idx,
                        high_price    = sh_price,
                        low_price     = sl_price,
                        depth         = depth,
                        duration_days = sl_idx - sh_idx,
                    )
                )
                break  # found valid low for this high; advance to next high
        return legs

    @staticmethod
    def _vol_stats(df: pd.DataFrame, legs: list[dict]) -> tuple[float, float]:
        """Returns (vol_contraction_ratio, vol_slope).
        ratio: last_avg / first_avg (kept for backward compat, no longer scored).
        slope: linear regression coefficient of leg_index vs normalised avg volume.
               Negative = progressive dry-up. Normalised by first-leg avg.
        """
        import numpy as np
        volume = df['volume'].to_numpy(dtype=float)
        leg_avgs = []
        for leg in legs:
            seg = volume[leg['start_idx']: leg['end_idx'] + 1]
            leg_avgs.append(float(seg.mean()) if len(seg) > 0 else float('nan'))
        valid = [(i, v) for i, v in enumerate(leg_avgs) if not math.isnan(v)]
        if len(valid) < 2:
            ratio = leg_avgs[-1] / leg_avgs[0] if (len(leg_avgs) >= 2 and leg_avgs[0] > 0) else float('nan')
            return ratio, float('nan')
        ratio = leg_avgs[-1] / leg_avgs[0] if leg_avgs[0] > 0 else float('nan')
        # Require at least 3 data points for a meaningful regression line.
        # With exactly 2 points polyfit produces a perfect-fit line (zero residual)
        # where a single volume spike fully determines the slope — not robust.
        if len(valid) < 3:
            return ratio, float('nan')
        xs = np.array([i for i, _ in valid], dtype=float)
        ys = np.array([v for _, v in valid], dtype=float)
        baseline = ys[0] if ys[0] > 0 else 1.0
        ys_norm = ys / baseline
        slope = float(np.polyfit(xs, ys_norm, 1)[0])
        return ratio, slope

    @staticmethod
    def _all_pivot_indices(
        swing_highs: list[tuple[int, float]],
        swing_lows:  list[tuple[int, float]],
    ) -> tuple[int, int]:
        """Return (first_pivot_row_idx, last_pivot_row_idx).

        .. deprecated::
            Prefer :meth:`_leg_index_range` which uses only the pivots that
            are part of validated contraction legs and avoids base inflation
            from noise pivots that were filtered out.
        """
        all_idx = [i for i, _ in swing_highs] + [i for i, _ in swing_lows]
        if not all_idx:
            raise ValueError("_all_pivot_indices called with empty pivot lists")
        return min(all_idx), max(all_idx)

    @staticmethod
    def _leg_index_range(legs: list[dict]) -> tuple[int, int]:
        """Return (first_idx, last_idx) bounded strictly to validated leg pivots.

        Using leg pivots (not the full pivot list from find_all_pivots) prevents
        noise pivots that were ATR-filtered or duration-filtered from inflating
        the base length, distorting base_low, and injecting false climax days.
        """
        if not legs:
            raise ValueError("_leg_index_range called with no legs")
        return legs[0]["start_idx"], legs[-1]["end_idx"]

    @staticmethod
    def _climax_days(
        df: pd.DataFrame,
        first_idx: int,
        last_idx: int,
        threshold: float = 2.5,
    ) -> int:
        """Count days inside the base where volume > threshold × 50d average.

        Parameters
        ----------
        first_idx / last_idx:
            Row indices bounding the base (from :meth:`_leg_index_range`).

        Returns 0 when insufficient data.
        """
        base_df = df.iloc[first_idx: last_idx + 1]
        if len(base_df) < 10:
            return 0
        volume = base_df['volume']
        # Compute 50d rolling average from the full df (not just base_df)
        full_vol_50 = df['volume'].rolling(50, min_periods=20).mean()
        # Use the value at first_idx as baseline if base is too short for its own rolling
        baseline_series = full_vol_50.iloc[first_idx: last_idx + 1]
        if baseline_series.isna().all():
            scalar_baseline = float(full_vol_50.iloc[first_idx])
            if math.isnan(scalar_baseline) or scalar_baseline <= 0:
                return 0
            climax_mask = volume > (threshold * scalar_baseline)
        else:
            climax_mask = volume > (threshold * baseline_series)
        return int(climax_mask.sum())

    @staticmethod
    def _base_weeks(
        df: pd.DataFrame,
        first_idx: int,
        last_idx: int,
    ) -> int:
        first_date = df.index[first_idx]
        last_date  = df.index[last_idx]
        return int((last_date - first_date).days // 7)

    @staticmethod
    def _base_low(
        df: pd.DataFrame,
        first_idx: int,
        last_idx: int,
    ) -> float:
        return float(df["low"].iloc[first_idx: last_idx + 1].min())

    @staticmethod
    def _tightness(df: pd.DataFrame) -> float:
        """Volatility compression ratio: avg_TR_10 / avg_TR_50.

        Uses a *simple moving average* of True Range (not Wilder's smoothed ATR).
        The ratio is dimensionally consistent — units cancel — so the simpler
        formula is equivalent for measuring relative compression.

        < 0.5 = strong compression. < 0.75 = acceptable. >= 1.0 = no compression.
        Returns float('nan') if insufficient data (< 50 bars).
        """
        if len(df) < 50:
            return float('nan')
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift(1)).abs(),
            (df['low']  - df['close'].shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr_10 = float(tr.iloc[-10:].mean())
        atr_50 = float(tr.iloc[-50:].mean())
        if atr_50 <= 0.0 or math.isnan(atr_50) or math.isnan(atr_10):
            return float('nan')
        return atr_10 / atr_50

    @staticmethod
    def _empty_metrics(df: pd.DataFrame) -> VCPMetrics:
        """Degenerate metrics returned when no contraction legs are found."""
        return VCPMetrics(
            contraction_count     = 0,
            max_depth_pct         = 0.0,
            final_depth_pct       = 0.0,
            vol_contraction_ratio = float("nan"),
            base_length_weeks     = 0,
            base_low              = float(df["low"].min()) if not df.empty else float("nan"),
            is_valid_vcp          = False,
            tightness_score       = float("nan"),
            monotonic_decline     = False,
            leg_depths            = [],
            vol_slope             = float("nan"),
            climax_days_in_base   = 0,
        )


# ---------------------------------------------------------------------------
# Detector registry + factory
# ---------------------------------------------------------------------------

DETECTORS: dict[str, type[VCPDetector]] = {
    "rule_based": RuleBasedVCPDetector,
}


def get_detector(config: dict) -> VCPDetector:
    """Return an instantiated VCPDetector chosen by *config*.

    The detector name is read from ``config["vcp"]["detector"]``
    (default ``"rule_based"``).

    Raises
    ------
    ConfigurationError
        When the requested detector name is not registered in DETECTORS.
    """
    name = config.get("vcp", {}).get("detector", "rule_based")
    cls  = DETECTORS.get(name)
    if cls is None:
        raise ConfigurationError(f"Unknown VCP detector: {name!r}")
    return cls()


# ---------------------------------------------------------------------------
# Feature-pipeline entry point
# ---------------------------------------------------------------------------

#: Column names appended by compute() — in declaration order.
_VCP_COLUMNS: list[str] = [
    "vcp_contraction_count",
    "vcp_max_depth_pct",
    "vcp_final_depth_pct",
    "vcp_vol_ratio",
    "vcp_base_length_weeks",
    "vcp_base_low",
    "vcp_valid",
    "vcp_tightness_score",
]


def compute(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Run VCP detection and append result columns to *df*.

    Columns appended (all scalar — same value in every row):

    ========================  =================================================
    ``vcp_contraction_count`` number of swing-to-swing contractions detected
    ``vcp_max_depth_pct``     deepest correction in the base (%)
    ``vcp_final_depth_pct``   shallowest / most recent correction (%)
    ``vcp_vol_ratio``         last-leg avg volume / first-leg avg volume
    ``vcp_base_length_weeks`` total base width in calendar weeks
    ``vcp_base_low``          lowest low in the entire base
    ``vcp_valid``             True when all VCP qualification rules pass
    ``vcp_tightness_score``   ATR₁₀/ATR₅₀ compression ratio (NaN if < 50 bars)
    ========================  =================================================

    On any detection failure the numeric columns are set to ``NaN`` and
    ``vcp_valid`` is set to ``False`` — the function **never raises**.

    Parameters
    ----------
    df:
        Cleaned OHLCV DataFrame with a DatetimeIndex.
    config:
        Screening configuration dict.

    Returns
    -------
    pd.DataFrame
        *df* with the eight vcp_* columns appended.
    """
    try:
        detector = get_detector(config)
        metrics  = detector.detect(df, config)
    except Exception as exc:           # noqa: BLE001
        log.warning("vcp.compute: detection failed (%s); filling NaN", exc)
        metrics = None

    if metrics is not None:
        df["vcp_contraction_count"] = metrics.contraction_count
        df["vcp_max_depth_pct"]     = metrics.max_depth_pct
        df["vcp_final_depth_pct"]   = metrics.final_depth_pct
        df["vcp_vol_ratio"]         = metrics.vol_contraction_ratio
        df["vcp_base_length_weeks"] = metrics.base_length_weeks
        df["vcp_base_low"]          = metrics.base_low
        df["vcp_valid"]             = metrics.is_valid_vcp
        df["vcp_tightness_score"]   = metrics.tightness_score
    else:
        for col in _VCP_COLUMNS:
            df[col] = False if col == "vcp_valid" else np.nan

    log.debug(
        "vcp.compute finished: shape=%s valid=%s contractions=%s",
        df.shape,
        df["vcp_valid"].iloc[-1] if not df.empty else "n/a",
        df["vcp_contraction_count"].iloc[-1] if not df.empty else "n/a",
    )
    return df
