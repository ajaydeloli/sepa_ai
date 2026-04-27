"""
rules/risk_reward.py
--------------------
Risk / reward computation for the Minervini SEPA rule engine.

Calculates a price target and the resulting risk-reward ratio for a
trade, given entry price, stop price, and an optional resistance level.

Design constraints:
  - Pure function: no I/O, no side effects, no global state.
  - Returns (0.0, 0.0, 0.0) when entry_price <= stop_price.
"""

from __future__ import annotations

import math

from utils.logger import get_logger

log = get_logger(__name__)


def compute_risk_reward(
    entry_price: float,
    stop_price: float,
    config: dict,
    resistance_price: float | None = None,
) -> tuple[float, float, float]:
    """Compute target price and risk/reward ratio for a trade.

    Target selection:
      - If ``resistance_price`` is provided **and** ``resistance_price > entry_price``,
        the resistance level is used as the target.
      - Otherwise the target is set to ``entry + risk * min_rr_ratio`` where
        ``min_rr_ratio = config["risk_reward"]["min_rr_ratio"]`` (default 2.0).

    Parameters
    ----------
    entry_price:
        Breakout / entry price for the trade.
    stop_price:
        Stop-loss price.  Must be strictly less than *entry_price*.
    config:
        Project config dict. Relevant sub-dict: ``config["risk_reward"]``.
        ``min_rr_ratio``  default 2.0.
    resistance_price:
        Optional price level (e.g. prior highs) used as a natural target.

    Returns
    -------
    tuple[float, float, float]
        ``(target_price, risk_amount, reward_risk_ratio)``

        Returns ``(0.0, 0.0, 0.0)`` when ``entry_price <= stop_price``.
    """
    # ------------------------------------------------------------------
    # Guard: degenerate / invalid trade.
    # ------------------------------------------------------------------
    if entry_price <= stop_price:
        log.debug(
            "compute_risk_reward: entry=%.4f <= stop=%.4f — returning zeros",
            entry_price, stop_price,
        )
        return 0.0, 0.0, 0.0

    rr_cfg: dict      = config.get("risk_reward", {})
    min_rr: float     = rr_cfg.get("min_rr_ratio", 2.0)

    risk_amount: float = entry_price - stop_price

    # ------------------------------------------------------------------
    # Target selection.
    # ------------------------------------------------------------------
    use_resistance = (
        resistance_price is not None
        and not math.isnan(resistance_price)
        and resistance_price > entry_price
    )

    if use_resistance:
        target_price = float(resistance_price)  # type: ignore[arg-type]
        log.debug(
            "compute_risk_reward: using resistance=%.4f as target", target_price
        )
    else:
        target_price = entry_price + risk_amount * min_rr
        log.debug(
            "compute_risk_reward: using min_rr=%.1f target=%.4f", min_rr, target_price
        )

    reward_risk_ratio: float = (target_price - entry_price) / risk_amount

    log.debug(
        "compute_risk_reward: entry=%.4f stop=%.4f target=%.4f risk=%.4f rr=%.2f",
        entry_price, stop_price, target_price, risk_amount, reward_risk_ratio,
    )
    return target_price, risk_amount, reward_risk_ratio
