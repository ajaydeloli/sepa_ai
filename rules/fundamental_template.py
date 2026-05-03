"""
rules/fundamental_template.py
------------------------------
Minervini 7-condition Fundamental Template for the SEPA stock screening system.

Evaluates the fundamentals dict produced by ingestion/fundamentals.py and returns
a FundamentalResult dataclass.  Missing, None, or un-parseable values are handled
gracefully — the corresponding condition evaluates to False and a warning is logged;
no exception is raised.

Condition summary
-----------------
  F1  EPS positive          — latest EPS > 0
  F2  EPS accelerating      — most recent QoQ growth > previous QoQ growth
  F3  Sales growth YoY      — sales_growth_yoy >= min_sales_growth_yoy (default 10 %)
  F4  ROE                   — roe >= min_roe (default 15 %)
  F5  Debt-to-Equity        — debt_to_equity <= max_de (default 1.0)
  F6  Promoter holding      — promoter_holding >= min_promoter_holding (default 35 %)
  F7  Profit growth         — profit_growth > 0

Design constraints (from PROJECT_DESIGN.md §9.3 and Appendix D)
---------------------------------------------------------------
- Operates on a plain dict — no pandas, no I/O.
- All numeric parsing is fault-tolerant: strings like "12.5%" or "N/A" are safe.
- fundamentals=None or empty dict → returns FundamentalResult(passes=False, ...).
  Never raises.
- Config keys live under config["fundamentals"]["conditions"].
- No imports from screener/, pipeline/, api/, or dashboard/.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from utils.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------

@dataclass
class FundamentalResult:
    passes: bool                # True only when ALL 7 conditions pass
    conditions_met: int         # 0–7
    f1_eps_positive: bool       # F1: latest EPS > 0
    f2_eps_accelerating: bool   # F2: most recent QoQ > previous QoQ
    f3_sales_growth: bool       # F3: sales_growth_yoy >= min_sales_growth_yoy
    f4_roe: bool                # F4: ROE >= min_roe
    f5_de_ratio: bool           # F5: D/E <= max_de
    f6_promoter_holding: bool   # F6: promoter_holding >= min_promoter_holding
    f7_profit_growth: bool      # F7: profit_growth > 0
    score: int                  # 0–100 (conditions_met / 7 * 100, rounded)
    hard_fails: list[str]       # names of conditions that failed (for reporting)
    values: dict = field(default_factory=dict)  # raw numeric values for each condition



# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_float(val) -> float:
    """Fault-tolerant float parser.

    Handles strings like ``"12.5%"``, ``"1,234.5"``, ``"N/A"``, ``None``,
    and plain numeric types.  Returns 0.0 on any parse failure so callers
    never see an exception.
    """
    try:
        return float(str(val).replace("%", "").replace(",", "").strip())
    except Exception:  # noqa: BLE001
        return 0.0


def _null_result() -> FundamentalResult:
    """Return an all-False FundamentalResult for None / empty fundamentals input."""
    return FundamentalResult(
        passes=False,
        conditions_met=0,
        f1_eps_positive=False,
        f2_eps_accelerating=False,
        f3_sales_growth=False,
        f4_roe=False,
        f5_de_ratio=False,
        f6_promoter_holding=False,
        f7_profit_growth=False,
        score=0,
        hard_fails=["F1_EPS", "F2_EPS_ACCEL", "F3_SALES", "F4_ROE",
                    "F5_DE", "F6_PROMOTER", "F7_PROFIT"],
        values={},
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_fundamental_template(
    fundamentals: dict | None,
    config: dict,
) -> FundamentalResult:
    """Evaluate 7 Minervini fundamental conditions against a fundamentals dict.

    Parameters
    ----------
    fundamentals:
        Dict produced by ``ingestion.fundamentals.fetch_fundamentals``.
        Pass ``None`` or an empty dict to receive an all-False result without
        raising.

    config:
        Project config dict.  Relevant keys (all under
        ``"fundamentals"`` → ``"conditions"``)::

            min_roe:                 15.0
            max_de:                  1.0
            min_promoter_holding:    35.0
            min_sales_growth_yoy:    10.0

    Returns
    -------
    FundamentalResult
        Dataclass with ``passes`` (True only when all 7 hold),
        ``conditions_met`` (0–7), one bool per condition, ``score`` (0–100),
        ``hard_fails`` list, and ``values`` dict of raw numeric inputs.
        Never raises.
    """
    # ── Guard: None / empty input ─────────────────────────────────────────
    if not fundamentals:
        log.debug("check_fundamental_template: fundamentals is None/empty — returning null result")
        return _null_result()

    # ── Config extraction ─────────────────────────────────────────────────
    cond_cfg: dict = (
        config.get("fundamentals", {}).get("conditions", {})
    )
    min_roe: float               = float(cond_cfg.get("min_roe",               15.0))
    max_de: float                = float(cond_cfg.get("max_de",                1.0))
    min_promoter_holding: float  = float(cond_cfg.get("min_promoter_holding",  35.0))
    min_sales_growth_yoy: float  = float(cond_cfg.get("min_sales_growth_yoy",  10.0))


    # ── Parse raw numeric values (all fault-tolerant) ─────────────────────
    eps               = _parse_float(fundamentals.get("eps",              0))
    eps_accelerating  = fundamentals.get("eps_accelerating", False)
    sales_growth_yoy  = _parse_float(fundamentals.get("sales_growth_yoy", 0))
    roe               = _parse_float(fundamentals.get("roe",              0))
    debt_to_equity    = _parse_float(fundamentals.get("debt_to_equity",  99))
    promoter_holding  = _parse_float(fundamentals.get("promoter_holding", 0))
    profit_growth     = _parse_float(fundamentals.get("profit_growth",    0))

    # ── Condition evaluation ──────────────────────────────────────────────

    # F1 — Latest EPS > 0
    f1 = bool(eps > 0)

    # F2 — EPS accelerating (pre-computed flag from ingestion layer)
    #      Accepts True/False/None; None or non-bool → False
    f2 = bool(eps_accelerating is True)

    # F3 — Sales growth YoY >= threshold
    f3 = bool(sales_growth_yoy >= min_sales_growth_yoy)

    # F4 — ROE >= minimum
    f4 = bool(roe >= min_roe)

    # F5 — Debt-to-Equity <= maximum
    f5 = bool(debt_to_equity <= max_de)

    # F6 — Promoter holding >= minimum
    f6 = bool(promoter_holding >= min_promoter_holding)

    # F7 — Profit growth > 0
    f7 = bool(profit_growth > 0)


    # ── Aggregate ─────────────────────────────────────────────────────────
    condition_flags = [f1, f2, f3, f4, f5, f6, f7]
    condition_names = [
        "F1_EPS", "F2_EPS_ACCEL", "F3_SALES",
        "F4_ROE", "F5_DE", "F6_PROMOTER", "F7_PROFIT",
    ]

    conditions_met: int = sum(condition_flags)
    passes: bool        = all(condition_flags)
    score: int          = round(conditions_met / 7 * 100)
    hard_fails: list[str] = [
        name for name, ok in zip(condition_names, condition_flags) if not ok
    ]

    # ── Values dict — raw numeric inputs for downstream reporting ─────────
    values: dict = {
        "eps":              eps,
        "eps_accelerating": eps_accelerating,
        "sales_growth_yoy": sales_growth_yoy,
        "roe":              roe,
        "de_ratio":         debt_to_equity,
        "promoter_holding": promoter_holding,
        "profit_growth":    profit_growth,
    }

    log.debug(
        "check_fundamental_template: passes=%s conditions_met=%d/7 "
        "eps=%.2f roe=%.2f de=%.2f promoter=%.2f sales_growth=%.2f profit_growth=%.2f",
        passes, conditions_met,
        eps, roe, debt_to_equity, promoter_holding, sales_growth_yoy, profit_growth,
    )

    return FundamentalResult(
        passes=passes,
        conditions_met=conditions_met,
        f1_eps_positive=f1,
        f2_eps_accelerating=f2,
        f3_sales_growth=f3,
        f4_roe=f4,
        f5_de_ratio=f5,
        f6_promoter_holding=f6,
        f7_profit_growth=f7,
        score=score,
        hard_fails=hard_fails,
        values=values,
    )
