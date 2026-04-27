"""
rules/entry_trigger.py
----------------------
Entry trigger detection for the Minervini SEPA rule engine.

Detects whether price has broken above the VCP pivot high on the current
bar, optionally confirmed by elevated volume.

Design constraints:
  - Pure function: no I/O, no side effects, no global state.
  - Operates on a single pd.Series row — no DataFrame loading.
  - Robust to NaN / 0 pivot_high values (never raises).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class EntryTrigger:
    """Result of an entry-trigger evaluation."""

    triggered: bool           # True when close > pivot_high * (1 + buffer_pct)
    entry_price: float | None  # breakout level = pivot_high * (1 + buffer_pct)
    pivot_high: float | None   # the VCP pivot high being broken
    volume_confirmed: bool     # True when vol_ratio >= breakout_vol_threshold
    reason: str               # human-readable explanation


def check_entry_trigger(row: pd.Series, config: dict) -> EntryTrigger:
    """Detect if price has broken above the VCP pivot high with volume confirmation.

    Parameters
    ----------
    row:
        pd.Series with at minimum: ``close``, ``pivot_high``, ``vol_ratio``.
        ``pivot_high`` is produced by ``features/pivot.py``.
        ``vol_ratio`` is produced by ``features/volume.py``.
    config:
        Project config dict. Relevant sub-dict: ``config["entry"]``.

        ``breakout_buffer_pct``     default 0.001 (0.1% above pivot high)
        ``breakout_vol_threshold``  default 1.5   (150% of average volume)

    Returns
    -------
    EntryTrigger
        Dataclass with triggered flag, prices, volume confirmation, and reason.
    """
    entry_cfg: dict = config.get("entry", {})
    buffer_pct: float        = entry_cfg.get("breakout_buffer_pct", 0.001)
    vol_threshold: float     = entry_cfg.get("breakout_vol_threshold", 1.5)

    # ------------------------------------------------------------------
    # Extract row values safely.
    # ------------------------------------------------------------------
    pivot_high_raw = row.get("pivot_high", float("nan"))
    close_raw      = row.get("close", float("nan"))
    vol_ratio_raw  = row.get("vol_ratio", float("nan"))

    pivot_high = float(pivot_high_raw) if pivot_high_raw is not None else float("nan")
    close      = float(close_raw)      if close_raw is not None      else float("nan")
    vol_ratio  = float(vol_ratio_raw)  if vol_ratio_raw is not None  else float("nan")

    # ------------------------------------------------------------------
    # Guard: no usable pivot high.
    # ------------------------------------------------------------------
    if math.isnan(pivot_high) or pivot_high == 0.0:
        log.debug("check_entry_trigger: no pivot high available")
        return EntryTrigger(
            triggered=False,
            entry_price=None,
            pivot_high=None,
            volume_confirmed=False,
            reason="no pivot high available",
        )

    # ------------------------------------------------------------------
    # Breakout condition.
    # ------------------------------------------------------------------
    breakout_level = pivot_high * (1.0 + buffer_pct)
    triggered      = (not math.isnan(close)) and close > breakout_level

    # ------------------------------------------------------------------
    # Volume confirmation (independent of breakout).
    # ------------------------------------------------------------------
    volume_confirmed = (not math.isnan(vol_ratio)) and vol_ratio >= vol_threshold

    # ------------------------------------------------------------------
    # Build human-readable reason.
    # ------------------------------------------------------------------
    if triggered:
        vol_tag = "with vol confirmation" if volume_confirmed else "WITHOUT vol confirmation"
        reason  = (
            f"breakout above pivot {pivot_high:.4f} {vol_tag} "
            f"(close={close:.4f} > level={breakout_level:.4f}, vol_ratio={vol_ratio:.2f})"
        )
    else:
        reason = (
            f"no breakout: close={close:.4f} <= breakout_level={breakout_level:.4f} "
            f"(pivot_high={pivot_high:.4f})"
        )

    log.debug(
        "check_entry_trigger: triggered=%s vol_confirmed=%s pivot=%.4f close=%.4f",
        triggered, volume_confirmed, pivot_high, close,
    )

    return EntryTrigger(
        triggered=triggered,
        entry_price=breakout_level if triggered else None,
        pivot_high=pivot_high,
        volume_confirmed=volume_confirmed,
        reason=reason,
    )
