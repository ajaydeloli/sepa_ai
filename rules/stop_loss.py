"""
rules/stop_loss.py
------------------
Stop-loss computation for the Minervini SEPA rule engine.

Three-priority waterfall:
  1. VCP base_low   — preferred; tight, fundamental stop.
  2. ATR-based      — fallback when VCP stop is unavailable or too wide.
  3. Fixed %        — last-resort fallback.

Design constraints:
  - Pure function: no I/O, no side effects, no global state.
  - Operates on a single pd.Series row — no DataFrame loading.
  - Returns (None, None, "no_data") on any data-quality failure.
"""

from __future__ import annotations

import math

import pandas as pd

from utils.logger import get_logger

log = get_logger(__name__)


def compute_stop_loss(
    row: pd.Series,
    vcp_base_low: float | None,
    config: dict,
) -> tuple[float | None, float | None, str]:
    """Compute the stop-loss price using a three-priority waterfall.

    Parameters
    ----------
    row:
        pd.Series with at minimum: ``close``, ``atr_14``.
    vcp_base_low:
        Lowest low in the VCP base (from ``VCPMetrics.base_low``).
        Pass ``None`` to skip the VCP method.
    config:
        Project config dict. Relevant sub-dict: ``config["stop_loss"]``.

        ``stop_buffer_pct``  default 0.005  — fraction below base_low for the stop.
        ``max_risk_pct``     default 15.0   — maximum allowed risk % from entry.
        ``atr_multiplier``   default 2.0    — ATR multiplier for the ATR method.
        ``fixed_stop_pct``   default 0.07   — fixed % below close for last resort.

    Returns
    -------
    tuple[float | None, float | None, str]
        ``(stop_price, risk_pct, method_used)``

        *stop_price*   — the stop-loss price (or ``None`` on data failure).
        *risk_pct*     — ``(close - stop_price) / close * 100`` (or ``None``).
        *method_used*  — ``"vcp_base_low"`` | ``"atr"`` | ``"pct"`` | ``"no_data"``.
    """
    sl_cfg: dict     = config.get("stop_loss", {})
    stop_buffer_pct: float = sl_cfg.get("stop_buffer_pct", 0.005)
    max_risk_pct: float    = sl_cfg.get("max_risk_pct", 15.0)
    atr_multiplier: float  = sl_cfg.get("atr_multiplier", 2.0)
    fixed_stop_pct: float  = sl_cfg.get("fixed_stop_pct", 0.07)

    # ------------------------------------------------------------------
    # Guard: need a valid close.
    # ------------------------------------------------------------------
    close_raw = row.get("close", float("nan"))
    close     = float(close_raw) if close_raw is not None else float("nan")

    if math.isnan(close) or close == 0.0:
        log.debug("compute_stop_loss: close is NaN/0 — no_data")
        return None, None, "no_data"

    atr_raw = row.get("atr_14", float("nan"))
    atr_14  = float(atr_raw) if atr_raw is not None else float("nan")

    def _risk(stop: float) -> float:
        return (close - stop) / close * 100.0

    # ------------------------------------------------------------------
    # Method 1: VCP base_low.
    # ------------------------------------------------------------------
    if vcp_base_low is not None and not math.isnan(vcp_base_low) and vcp_base_low > 0.0:
        stop_vcp  = vcp_base_low * (1.0 - stop_buffer_pct)
        risk_vcp  = _risk(stop_vcp)
        if risk_vcp <= max_risk_pct:
            log.debug(
                "compute_stop_loss: vcp_base_low method — stop=%.4f risk=%.2f%%",
                stop_vcp, risk_vcp,
            )
            return stop_vcp, risk_vcp, "vcp_base_low"
        log.debug(
            "compute_stop_loss: vcp_base_low risk=%.2f%% > max=%.1f%% — fallback to ATR",
            risk_vcp, max_risk_pct,
        )

    # ------------------------------------------------------------------
    # Method 2: ATR fallback.
    # ------------------------------------------------------------------
    if not math.isnan(atr_14) and atr_14 > 0.0:
        stop_atr = close - (atr_14 * atr_multiplier)
        risk_atr = _risk(stop_atr)
        log.debug(
            "compute_stop_loss: atr method — stop=%.4f risk=%.2f%%",
            stop_atr, risk_atr,
        )
        return stop_atr, risk_atr, "atr"

    # ------------------------------------------------------------------
    # Method 3: Fixed % last resort.
    # ------------------------------------------------------------------
    stop_pct = close * (1.0 - fixed_stop_pct)
    risk_pct = _risk(stop_pct)
    log.debug(
        "compute_stop_loss: fixed pct method — stop=%.4f risk=%.2f%%",
        stop_pct, risk_pct,
    )
    return stop_pct, risk_pct, "pct"
