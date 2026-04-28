"""
alerts/alert_deduplicator.py
-----------------------------
Deduplication logic for the SEPA alert pipeline.

Prevents alert spam by checking whether a symbol was alerted recently and
whether the new result represents a meaningful improvement over the last one.

The backing store is the ``alerts`` table managed by :class:`SQLiteStore`.
All reads and writes go through the store's public API so the dedup check
survives process restarts.
"""

from __future__ import annotations

from datetime import date

from rules.scorer import SEPAResult
from storage.sqlite_store import SQLiteStore
from utils.logger import get_logger

log = get_logger(__name__)

# Ordered mapping of quality grades → numeric rank for comparison.
QUALITY_RANK: dict[str, int] = {"FAIL": 0, "C": 1, "B": 2, "A": 3, "A+": 4}


def should_alert(result: SEPAResult, db: SQLiteStore, config: dict) -> bool:
    """Return ``True`` if *result* should trigger a new alert dispatch.

    Reads the most recent alert for ``result.symbol`` from the SQLite
    ``alerts`` table and applies five re-alert conditions (any is sufficient):

    1. Symbol has never been alerted (no row found).
    2. Days since last alert >= ``config["alerts"]["dedup_days"]`` (default 3).
    3. Score improved by >= ``config["alerts"]["dedup_score_jump"]`` (default 10).
    4. Setup quality improved (e.g. "B" → "A" or "A" → "A+").
    5. Breakout newly triggered: ``result.breakout_triggered`` is True and the
       previous alert had ``breakout_triggered`` False.

    Returns ``False`` only when within the dedup window AND none of conditions
    3–5 are satisfied.
    """
    alert_cfg = config.get("alerts", {})
    dedup_days: int = int(alert_cfg.get("dedup_days", 3))
    dedup_score_jump: float = float(alert_cfg.get("dedup_score_jump", 10))

    last = db.get_last_alert(result.symbol)

    # ── Condition 1: never alerted ──────────────────────────────────────────
    if last is None:
        log.debug("should_alert(%s): no prior alert — alerting", result.symbol)
        return True

    # Parse the stored alert date.
    try:
        prev_date = date.fromisoformat(str(last["alerted_date"]))
    except (ValueError, TypeError):
        log.warning(
            "should_alert(%s): unparseable alerted_date %r — re-alerting",
            result.symbol, last.get("alerted_date"),
        )
        return True

    days_since: int = (result.run_date - prev_date).days

    # ── Condition 2: outside dedup window ───────────────────────────────────
    if days_since >= dedup_days:
        log.debug(
            "should_alert(%s): %d days since last alert (threshold %d) — alerting",
            result.symbol, days_since, dedup_days,
        )
        return True

    # ── Within dedup window: check improvement conditions ───────────────────
    prev_score: float = float(last.get("score") or 0)
    prev_quality: str = str(last.get("quality") or "FAIL")
    prev_breakout: bool = bool(last.get("breakout_triggered"))

    # Condition 3: significant score jump
    score_delta = result.score - prev_score
    if score_delta >= dedup_score_jump:
        log.debug(
            "should_alert(%s): score jumped +%.1f (threshold %.1f) — alerting",
            result.symbol, score_delta, dedup_score_jump,
        )
        return True

    # Condition 4: quality grade improved
    if QUALITY_RANK.get(result.setup_quality, 0) > QUALITY_RANK.get(prev_quality, 0):
        log.debug(
            "should_alert(%s): quality %s → %s — alerting",
            result.symbol, prev_quality, result.setup_quality,
        )
        return True

    # Condition 5: breakout newly triggered
    if result.breakout_triggered and not prev_breakout:
        log.debug("should_alert(%s): breakout newly triggered — alerting", result.symbol)
        return True

    log.debug(
        "should_alert(%s): within %d-day window, no meaningful improvement — skipping",
        result.symbol, dedup_days,
    )
    return False


def record_alert(result: SEPAResult, db: SQLiteStore) -> None:
    """Persist an alert record after a successful dispatch.

    Idempotent for the same (symbol, run_date) pair: if a row already exists
    for today's date no duplicate is inserted.

    Parameters
    ----------
    result:
        The SEPAResult that was just alerted.
    db:
        Open :class:`SQLiteStore` instance.
    """
    # Guard against double-recording on the same date.
    last = db.get_last_alert(result.symbol)
    if last is not None:
        try:
            prev_date = date.fromisoformat(str(last["alerted_date"]))
            if prev_date == result.run_date:
                log.debug(
                    "record_alert(%s): row for %s already exists — skipping duplicate",
                    result.symbol, result.run_date,
                )
                return
        except (ValueError, TypeError):
            pass  # malformed date — proceed with insert

    db.save_alert(
        symbol=result.symbol,
        alerted_date=result.run_date,
        score=float(result.score),
        quality=result.setup_quality,
        breakout_triggered=result.breakout_triggered,
        channel=None,
    )
    log.debug("record_alert(%s): persisted for %s", result.symbol, result.run_date)
