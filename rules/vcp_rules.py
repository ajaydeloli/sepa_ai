"""
rules/vcp_rules.py
------------------
VCP qualification gate for the Minervini SEPA rule engine.

Applies every VCP rule independently to a VCPMetrics object and returns
a pass/fail verdict plus a per-rule details dict for transparency.

Design constraints:
  - Pure function: no I/O, no side effects, no global state.
  - All thresholds are read from config so nothing is hardcoded here.
  - Returns (False, details) immediately when metrics.is_valid_vcp is False
    (the VCP detector already ran _apply_vcp_rules; trust its verdict).
"""

from __future__ import annotations

import math

from features.vcp import VCPMetrics
from utils.logger import get_logger

log = get_logger(__name__)


def qualify_vcp(metrics: VCPMetrics, config: dict) -> tuple[bool, dict]:
    """Apply VCP qualification rules to a VCPMetrics object.

    Parameters
    ----------
    metrics:
        VCPMetrics dataclass produced by features/vcp.py.
    config:
        Project config dict. Relevant sub-dict: config["vcp"].

    Returns
    -------
    tuple[bool, dict]
        ``(qualified, details)`` where *details* maps each rule name to a
        bool indicating whether that individual rule passed.

    Rule evaluation is short-circuited on ``metrics.is_valid_vcp == False``:
    the VCP detector already evaluated the same rules; we trust its verdict
    and return immediately with all detail flags set to False.
    """
    vcp_cfg: dict = config.get("vcp", {})

    min_contractions: int   = vcp_cfg.get("min_contractions", 2)
    max_contractions: int   = vcp_cfg.get("max_contractions", 5)
    require_vol: bool       = vcp_cfg.get("require_vol_contraction", True)
    min_weeks: int          = vcp_cfg.get("min_weeks", 3)
    max_weeks: int          = vcp_cfg.get("max_weeks", 52)
    tightness_pct: float    = vcp_cfg.get("tightness_pct", 10.0)
    max_depth_pct_abs: float = vcp_cfg.get("max_depth_pct", 50.0)

    # ------------------------------------------------------------------
    # Fast-path: if the detector already invalidated the VCP, bail out.
    # ------------------------------------------------------------------
    if not metrics.is_valid_vcp:
        details: dict[str, bool] = {
            "contraction_count_min": False,
            "contraction_count_max": False,
            "declining_depth":       False,
            "vol_contraction":       False,
            "base_length_min":       False,
            "base_length_max":       False,
            "tightness_score":       False,
            "max_depth_abs":         False,
        }
        log.debug("qualify_vcp: is_valid_vcp=False — early exit")
        return False, details

    # ------------------------------------------------------------------
    # Evaluate each rule independently (all must pass).
    # ------------------------------------------------------------------
    r_count_min  = metrics.contraction_count >= min_contractions
    r_count_max  = metrics.contraction_count <= max_contractions
    r_declining  = metrics.final_depth_pct < metrics.max_depth_pct
    r_vol        = (not require_vol) or (metrics.vol_contraction_ratio < 1.0)
    r_len_min    = metrics.base_length_weeks >= min_weeks
    r_len_max    = metrics.base_length_weeks <= max_weeks
    r_tight      = (
        not math.isnan(metrics.tightness_score)
        and metrics.tightness_score < tightness_pct
    )
    r_depth_abs  = metrics.max_depth_pct <= max_depth_pct_abs

    details = {
        "contraction_count_min": r_count_min,
        "contraction_count_max": r_count_max,
        "declining_depth":       r_declining,
        "vol_contraction":       r_vol,
        "base_length_min":       r_len_min,
        "base_length_max":       r_len_max,
        "tightness_score":       r_tight,
        "max_depth_abs":         r_depth_abs,
    }

    qualified = all(details.values())

    log.debug(
        "qualify_vcp: qualified=%s contractions=%d depth=%.1f%% tight=%.1f%%",
        qualified,
        metrics.contraction_count,
        metrics.max_depth_pct,
        metrics.tightness_score,
    )
    return qualified, details
