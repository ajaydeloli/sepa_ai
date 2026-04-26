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
from dataclasses import dataclass

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
    tightness_score: float          # % range of the last 3 weeks (lower = tighter)


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
    tightness_pct: float     = vcp.get("tightness_pct", 10.0)
    max_depth_pct: float     = vcp.get("max_depth_pct", 50.0)

    if not (min_contractions <= metrics.contraction_count <= max_contractions):
        return False
    if not (metrics.final_depth_pct < metrics.max_depth_pct):        # each leg shallower
        return False
    if require_vol and metrics.vol_contraction_ratio >= 1.0:          # volume drying up
        return False
    if not (min_weeks <= metrics.base_length_weeks <= max_weeks):
        return False
    if math.isnan(metrics.tightness_score) or metrics.tightness_score >= tightness_pct:
        return False
    if metrics.max_depth_pct > max_depth_pct:
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
      9.  tightness_score   = range of last 3 calendar weeks as % of min low
      10. is_valid_vcp      = _apply_vcp_rules(metrics, config)
    """

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def detect(self, df: pd.DataFrame, config: dict) -> VCPMetrics:
        vcp_cfg = config.get("vcp", {})
        sensitivity: int = vcp_cfg.get("pivot_sensitivity", 5)

        swing_highs, swing_lows = find_all_pivots(df, sensitivity=sensitivity)

        legs = self._build_legs(swing_highs, swing_lows)
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

        vol_contraction_ratio = self._vol_ratio(df, legs)
        base_length_weeks     = self._base_weeks(df, swing_highs, swing_lows)
        base_low              = self._base_low(df, swing_highs, swing_lows)
        tightness_score       = self._tightness(df)

        metrics = VCPMetrics(
            contraction_count     = contraction_count,
            max_depth_pct         = max_depth_pct,
            final_depth_pct       = final_depth_pct,
            vol_contraction_ratio = vol_contraction_ratio,
            base_length_weeks     = base_length_weeks,
            base_low              = base_low,
            is_valid_vcp          = False,   # filled below
            tightness_score       = tightness_score,
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
    ) -> list[dict]:
        """Pair each swing high with the next swing low to form contraction legs.

        A leg captures: start index, end index, high price, low price,
        and the depth percentage correction.
        """
        legs: list[dict] = []
        for sh_idx, sh_price in swing_highs:
            # First swing low whose row index is strictly after this swing high
            for sl_idx, sl_price in swing_lows:
                if sl_idx > sh_idx:
                    depth = (sh_price - sl_price) / sh_price * 100.0
                    legs.append(
                        dict(
                            start_idx  = sh_idx,
                            end_idx    = sl_idx,
                            high_price = sh_price,
                            low_price  = sl_price,
                            depth      = depth,
                        )
                    )
                    break
        return legs

    @staticmethod
    def _vol_ratio(df: pd.DataFrame, legs: list[dict]) -> float:
        """Compute last-leg average volume / first-leg average volume."""
        volume = df["volume"].to_numpy(dtype=float)
        first, last = legs[0], legs[-1]

        first_avg = float(volume[first["start_idx"]: first["end_idx"] + 1].mean())
        last_avg  = float(volume[last["start_idx"]:  last["end_idx"]  + 1].mean())

        if first_avg <= 0.0 or math.isnan(first_avg):
            return float("nan")
        return last_avg / first_avg

    @staticmethod
    def _all_pivot_indices(
        swing_highs: list[tuple[int, float]],
        swing_lows:  list[tuple[int, float]],
    ) -> tuple[int, int]:
        """Return (first_pivot_row_idx, last_pivot_row_idx)."""
        all_idx = [i for i, _ in swing_highs] + [i for i, _ in swing_lows]
        return min(all_idx), max(all_idx)

    @staticmethod
    def _base_weeks(
        df: pd.DataFrame,
        swing_highs: list[tuple[int, float]],
        swing_lows:  list[tuple[int, float]],
    ) -> int:
        first_idx, last_idx = RuleBasedVCPDetector._all_pivot_indices(swing_highs, swing_lows)
        first_date = df.index[first_idx]
        last_date  = df.index[last_idx]
        return int((last_date - first_date).days // 7)

    @staticmethod
    def _base_low(
        df: pd.DataFrame,
        swing_highs: list[tuple[int, float]],
        swing_lows:  list[tuple[int, float]],
    ) -> float:
        first_idx, last_idx = RuleBasedVCPDetector._all_pivot_indices(swing_highs, swing_lows)
        return float(df["low"].iloc[first_idx: last_idx + 1].min())

    @staticmethod
    def _tightness(df: pd.DataFrame) -> float:
        """% range of the last 3 calendar weeks of data."""
        if df.empty:
            return float("nan")
        last_date = df.index[-1]
        cutoff    = last_date - pd.Timedelta(days=21)
        sub       = df.loc[df.index >= cutoff]
        if sub.empty:
            return float("nan")
        hi  = float(sub["high"].max())
        lo  = float(sub["low"].min())
        if lo <= 0.0:
            return float("nan")
        return (hi - lo) / lo * 100.0

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
    ``vcp_tightness_score``   % range of the last 3 calendar weeks
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
