"""
pipeline/scheduler.py
----------------------
APScheduler-based scheduler for the SEPA daily pipeline.

Public API
----------
start_scheduler(config)        -- Start the blocking scheduler (blocks forever)
run_once_now(config, scope)    -- Trigger an immediate single run (API entrypoint)

Schedule
--------
* Daily job   : Mon–Fri at ``config["scheduler"]["run_time"]`` (default 15:35 IST).
  Checks ``is_trading_day()`` at runtime — skips NSE holidays silently.
* Monthly job : 1st of month at 02:00 IST — runs bootstrap.py --dry-run for sanity.

The scheduler never stores state on disk; run_daily() writes to SQLite independently.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from pipeline.context import RunContext
from pipeline.runner import run_daily
from utils.logger import get_logger
from utils.trading_calendar import is_trading_day

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_ctx(config: dict, scope: str = "all") -> RunContext:
    """Construct a RunContext for an immediate daily run."""
    return RunContext(
        run_date=date.today(),
        mode="daily",
        config=config,
        scope=scope,
    )


def _run_bootstrap_dry_run() -> None:
    """Invoke ``scripts/bootstrap.py --universe nifty500 --dry-run`` as a subprocess.

    Runs in a subprocess so any import-time side-effects in bootstrap.py
    cannot corrupt the scheduler process's state.  Output is captured and
    summarised in the log.
    """
    script = _PROJECT_ROOT / "scripts" / "bootstrap.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--universe", "nifty500", "--dry-run"],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            timeout=120,
        )
        if result.stdout:
            log.info("bootstrap dry-run stdout (first 500 chars): %s", result.stdout[:500])
        if result.returncode != 0:
            log.warning(
                "bootstrap dry-run exited %d — stderr: %s",
                result.returncode,
                result.stderr[:300],
            )
        else:
            log.info("bootstrap dry-run completed successfully (exit 0)")
    except subprocess.TimeoutExpired:
        log.error("bootstrap dry-run timed out after 120s")
    except Exception as exc:
        log.error("bootstrap dry-run failed: %s", exc)


# ---------------------------------------------------------------------------
# Scheduled job callbacks
# ---------------------------------------------------------------------------

def _daily_job(config: dict) -> None:
    """APScheduler callback — runs Mon–Fri at the configured time.

    Checks ``is_trading_day()`` at call time, not at schedule time, so
    NSE holidays discovered after the scheduler started are still respected.
    """
    today = date.today()

    if not is_trading_day(today):
        log.info("Skipping: %s is not an NSE trading day", today)
        return

    log.info("Scheduler: starting daily pipeline run for %s", today)
    ctx = _build_ctx(config)
    try:
        summary = run_daily(ctx)
        log.info(
            "Scheduler: run complete for %s — stage2=%d  A+=%d  A=%d  alerts=%d",
            today,
            summary.get("passed_stage2", 0),
            summary.get("a_plus", 0),
            summary.get("a", 0),
            summary.get("alerts_sent", 0),
        )
    except Exception as exc:
        # run_daily already sent a Telegram error alert and logged the failure;
        # we only need to prevent the exception from killing the scheduler.
        log.error("Scheduler: daily run for %s failed: %s", today, exc)


def _monthly_bootstrap_job(config: dict) -> None:
    """APScheduler callback — runs on the 1st of each month at 02:00 IST.

    Executes ``scripts/bootstrap.py --universe nifty500 --dry-run`` as a
    lightweight sanity check to verify the processed data store is healthy.
    Does NOT re-download or overwrite any data.
    """
    log.info("Scheduler: monthly bootstrap sanity check starting")
    _run_bootstrap_dry_run()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_scheduler(config: dict) -> None:
    """Start the APScheduler blocking scheduler.

    Reads the schedule from:
      ``config["scheduler"]["run_time"]``  → ``"HH:MM"`` (default ``"15:35"``)
      ``config["scheduler"]["timezone"]``  → IANA name (default ``"Asia/Kolkata"``)

    Two jobs are registered:
    1. **Daily pipeline** — Mon–Fri at ``run_time``; honours ``is_trading_day()``
       at runtime so NSE holidays are skipped automatically.
    2. **Monthly bootstrap check** — 1st of each month at 02:00 IST; runs
       bootstrap.py in --dry-run mode as a sanity check.

    This function **blocks indefinitely**.  Send SIGINT or SIGTERM to stop.

    Parameters
    ----------
    config:
        Parsed ``settings.yaml`` as a plain dict.
    """
    scheduler_cfg = config.get("scheduler", {})
    run_time: str = str(scheduler_cfg.get("run_time", "15:35"))
    timezone: str = str(scheduler_cfg.get("timezone", "Asia/Kolkata"))

    hour_str, minute_str = run_time.split(":")

    scheduler = BlockingScheduler(timezone=timezone)

    # ── Job 1: Daily pipeline ──────────────────────────────────────────────
    scheduler.add_job(
        func=_daily_job,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=int(hour_str),
            minute=int(minute_str),
            timezone=timezone,
        ),
        args=[config],
        id="sepa_daily",
        name="SEPA daily screen",
        replace_existing=True,
        misfire_grace_time=300,   # tolerate up to 5-minute late start
    )
    log.info(
        "Scheduled daily job: Mon–Fri %s:%s %s (is_trading_day checked at runtime)",
        hour_str, minute_str, timezone,
    )

    # ── Job 2: Monthly bootstrap sanity check ─────────────────────────────
    scheduler.add_job(
        func=_monthly_bootstrap_job,
        trigger=CronTrigger(
            day=1,
            hour=2,
            minute=0,
            timezone=timezone,
        ),
        args=[config],
        id="sepa_monthly_bootstrap",
        name="SEPA monthly bootstrap sanity",
        replace_existing=True,
    )
    log.info("Scheduled monthly bootstrap check: 1st of month at 02:00 %s", timezone)

    log.info("Scheduler starting — blocking until interrupted (SIGINT/SIGTERM to stop)")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped cleanly by user")


def run_once_now(config: dict, scope: str = "all") -> dict:
    """Trigger a single immediate pipeline run synchronously.

    Used by the FastAPI layer (``POST /api/v1/run``) to kick off an
    on-demand run outside the normal schedule.

    Parameters
    ----------
    config:
        Parsed ``settings.yaml`` as a plain dict.
    scope:
        Symbol scope — ``"all"`` | ``"universe"`` | ``"watchlist"``.

    Returns
    -------
    dict
        The run-summary dict returned by :func:`~pipeline.runner.run_daily`.

    Raises
    ------
    Exception
        Propagated from ``run_daily()`` on critical failure; the API layer
        should catch and return an appropriate HTTP error response.
    """
    ctx = RunContext(
        run_date=date.today(),
        mode="daily",
        config=config,
        scope=scope,
    )
    log.info("run_once_now: triggering immediate run for %s (scope=%s)", ctx.run_date, scope)
    return run_daily(ctx)
