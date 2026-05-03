"""
pipeline/runner.py
------------------
Main orchestrator for the Minervini SEPA daily screening pipeline.

Public API
----------
run_daily(ctx: RunContext) -> dict
    Execute the full 14-step pipeline for a single trading date.
    Always returns a summary dict; never returns None.
    Re-raises on critical failure (steps 2–7) after sending an error alert.

run_historical(ctx: RunContext, start: date, end: date) -> list[dict]
    Iterate over every NSE trading day in [start, end] and call run_daily().
    Returns one summary dict per day processed (including error dicts).

Error-handling contract
-----------------------
* Critical path (steps 1–7): any unhandled exception sends a Telegram error
  alert, logs the run as "error" in run_history, and re-raises so callers
  (CLI, API) receive a full traceback.
* Non-critical path (steps 8–13, reports + alerts): each step is wrapped in
  an independent try/except; failures are logged at ERROR level and the run
  continues without aborting.
* Individual symbol failures in steps 3–4 (OHLCV write, feature update):
  logged at WARNING level and skipped — the pipeline continues with
  remaining symbols.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from alerts.alert_deduplicator import record_alert, should_alert
from alerts.telegram_alert import send_daily_watchlist, send_error_alert
from features.feature_store import bootstrap, needs_bootstrap, update
from ingestion import source_factory
from ingestion.universe_loader import resolve_symbols
from pipeline.context import RunContext
from reports.chart_generator import generate_batch_charts
from reports.daily_watchlist import (
    generate_csv_report,
    generate_html_report,
    get_report_summary,
)
from screener.pipeline import run_screen
from screener.results import persist_results
from storage.parquet_store import append_row, read_parquet
from storage.sqlite_store import SQLiteStore
from utils.exceptions import FeatureStoreOutOfSyncError
from utils.logger import get_logger
from utils.trading_calendar import trading_days as get_trading_days

log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_db(config: dict) -> SQLiteStore:
    """Open (or create) the SQLite database defined in config."""
    raw = config.get("watchlist", {}).get("persist_path", "data/sepa_ai.db")
    db_path = Path(raw)
    if not db_path.is_absolute():
        db_path = _PROJECT_ROOT / db_path
    return SQLiteStore(db_path)


def _get_output_dir(config: dict) -> str:
    """Return the absolute path to the report output directory."""
    raw = config.get("reports", {}).get("output_dir", "data/reports")
    p = Path(raw)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return str(p)


def _get_processed_dir(config: dict) -> Path:
    """Return the absolute Path to data/processed/."""
    raw = config.get("data", {}).get("processed_dir", "data/processed")
    p = Path(raw)
    return p if p.is_absolute() else _PROJECT_ROOT / p


def _git_sha() -> Optional[str]:
    """Return the short HEAD SHA of the current git commit, or None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            timeout=5,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _config_hash(config: dict) -> str:
    """Return a 12-char MD5 hex digest of the serialised config dict."""
    raw = json.dumps(config, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def _load_symbol_info(universe: list[str], config: dict) -> pd.DataFrame:
    """Load symbol metadata (symbol, sector columns).

    Resolution order:
    1. ``data/fundamentals/symbol_info.parquet`` — pre-populated by fundamentals ingestion.
    2. Fallback: a minimal DataFrame constructed from *universe* with sector="Unknown".

    The result is always a DataFrame with at minimum ``symbol`` and ``sector`` columns,
    which is what ``run_screen`` and ``score_symbol`` require.
    """
    fundamentals_dir = Path(
        config.get("data", {}).get("fundamentals_dir", "data/fundamentals")
    )
    if not fundamentals_dir.is_absolute():
        fundamentals_dir = _PROJECT_ROOT / fundamentals_dir

    si_path = fundamentals_dir / "symbol_info.parquet"
    if si_path.exists():
        try:
            df = pd.read_parquet(si_path)
            if "symbol" in df.columns and "sector" in df.columns:
                log.debug("_load_symbol_info: %d rows from %s", len(df), si_path)
                return df
        except Exception as exc:
            log.warning(
                "_load_symbol_info: could not read %s (%s) — using fallback", si_path, exc
            )

    log.debug("_load_symbol_info: building fallback from universe (%d symbols)", len(universe))
    return pd.DataFrame({"symbol": universe, "sector": ["Unknown"] * len(universe)})


def _load_benchmark(config: dict) -> pd.DataFrame:
    """Load the Nifty-500 index OHLCV DataFrame needed by run_rs_rating_pass().

    Resolution order:
    1. ``data/processed/NIFTY500.parquet`` — pre-cached during bootstrap.
    2. Fetch ``^NSEI`` via the configured data source as a fallback.
    3. Empty DataFrame on complete failure (run_screen handles the missing case gracefully).
    """
    processed_dir = _get_processed_dir(config)
    benchmark_path = processed_dir / "NIFTY500.parquet"

    if benchmark_path.exists():
        try:
            df = read_parquet(benchmark_path)
            if not df.empty and len(df) >= 65:
                log.debug("_load_benchmark: %d rows from %s", len(df), benchmark_path)
                return df
            log.warning(
                "_load_benchmark: %s has only %d rows (need 65) — fetching live",
                benchmark_path,
                len(df),
            )
        except Exception as exc:
            log.warning("_load_benchmark: read failed (%s) — fetching live", exc)

    log.info("_load_benchmark: fetching live from source")
    try:
        source = source_factory.get_source(config)
        today = date.today()
        df = source.fetch("^NSEI", start=today - timedelta(days=400), end=today)
        if not df.empty:
            log.info("_load_benchmark: fetched %d live rows for ^NSEI", len(df))
            return df
    except Exception as exc:
        log.warning("_load_benchmark: live fetch failed (%s) — returning empty", exc)

    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_daily(ctx: RunContext) -> dict:
    """Execute the full SEPA daily pipeline for *ctx.run_date*.

    Steps
    -----
    1.  resolve_symbols                → universe + watchlist
    2.  fetch_universe_batch           → today's OHLCV per symbol
    3.  Append OHLCV to processed store (per-symbol; failures skipped)
    4.  Feature store bootstrap/update  (per-symbol; failures skipped)
    5.  Load benchmark_df + symbol_info
    6.  run_screen                     → list[SEPAResult]
    7.  persist_results                → SQLite upsert
    8.  generate_csv_report            (non-critical)
    9.  generate_html_report           (non-critical)
    10. generate_batch_charts          (non-critical)
    11. Filter results via should_alert (non-critical)
    12. send_daily_watchlist           (non-critical)
    13. record_alert per sent symbol   (non-critical)
    14. Log run_history to SQLite      (non-critical)

    Parameters
    ----------
    ctx:
        Run context carrying run_date, config, scope, dry_run flag, etc.

    Returns
    -------
    dict
        ``{run_date, duration_sec, universe_size, passed_stage2, a_plus, a,
           report_csv, report_html, alerts_sent}``

    Raises
    ------
    Exception
        Re-raised from any failure in steps 1–7 after sending a Telegram
        error alert and writing a "error" row to run_history.
    """
    t0 = time.perf_counter()
    config = ctx.config
    run_date = ctx.run_date

    log.info("=" * 60)
    log.info(
        "run_daily START  date=%s  scope=%s  mode=%s  dry_run=%s",
        run_date, ctx.scope, ctx.mode, ctx.dry_run,
    )

    # Open the database once and share across all steps
    db = _get_db(config)
    output_dir = _get_output_dir(config)

    # Initialise summary with safe defaults so we can always return a complete dict
    summary: dict = {
        "run_date": str(run_date),
        "duration_sec": 0.0,
        "universe_size": 0,
        "passed_stage2": 0,
        "a_plus": 0,
        "a": 0,
        "report_csv": "",
        "report_html": "",
        "alerts_sent": 0,
    }

    try:
        # ── Step 1: Resolve symbols ─────────────────────────────────────────
        log.info("Step 1 — resolve_symbols  scope=%s", ctx.scope)
        run_symbols = resolve_symbols(
            config=config,
            db=db,
            cli_watchlist_file=None,          # not carried in RunContext
            cli_symbols=ctx.symbols_override,
            scope=ctx.scope,
        )
        universe: list[str] = run_symbols.all
        watchlist_symbols: list[str] = run_symbols.watchlist
        summary["universe_size"] = len(universe)
        log.info(
            "Step 1 complete: %d total symbols  (watchlist=%d)",
            len(universe), len(watchlist_symbols),
        )

        # ── Step 2: Fetch today's OHLCV via the configured source ───────────
        log.info("Step 2 — fetch_universe_batch  symbols=%d", len(universe))
        source = source_factory.get_source(config)
        ohlcv_data: dict[str, pd.DataFrame] = source.fetch_universe_batch(
            universe, period="5d"
        )
        log.info("Step 2 complete: %d/%d symbols fetched", len(ohlcv_data), len(universe))

        # ── Step 3: Append OHLCV rows to data/processed/{symbol}.parquet ────
        log.info("Step 3 — append OHLCV to processed store")
        processed_dir = _get_processed_dir(config)
        processed_dir.mkdir(parents=True, exist_ok=True)

        # ohlcv_data_full is the union of all successfully stored OHLCV frames —
        # passed later to generate_batch_charts so charts are not silently empty.
        ohlcv_data_full: dict[str, pd.DataFrame] = {}

        for symbol, df in ohlcv_data.items():
            if df is None or df.empty:
                log.warning("Step 3: empty OHLCV for %s — skipping", symbol)
                continue
            parquet_path = processed_dir / f"{symbol}.parquet"
            try:
                if not ctx.dry_run:
                    try:
                        append_row(parquet_path, df)
                    except FeatureStoreOutOfSyncError:
                        # Today's rows already present — idempotent re-run
                        log.debug("Step 3: %s already up-to-date — skipping append", symbol)
                ohlcv_data_full[symbol] = df
            except Exception as exc:
                # Per-symbol failure: log + skip; do NOT abort
                log.warning(
                    "Step 3: append_row(%s) failed: %s — skipping symbol", symbol, exc
                )

        log.info("Step 3 complete: %d/%d symbols in processed store", len(ohlcv_data_full), len(universe))

        # ── Step 4: Feature store bootstrap/update ──────────────────────────
        log.info("Step 4 — feature store update/bootstrap  universe=%d", len(universe))
        for symbol in universe:
            try:
                if needs_bootstrap(symbol, config):
                    log.info("Step 4: bootstrap(%s)", symbol)
                    if not ctx.dry_run:
                        bootstrap(symbol, config)
                else:
                    if not ctx.dry_run:
                        update(symbol, run_date, config)
            except Exception as exc:
                # Per-symbol failure: log + skip; do NOT abort
                log.warning(
                    "Step 4: feature update failed for %s: %s — skipping", symbol, exc
                )

        # ── Step 5: Load benchmark + symbol_info ────────────────────────────
        log.info("Step 5 — load benchmark_df + symbol_info")
        benchmark_df = _load_benchmark(config)
        symbol_info = _load_symbol_info(universe, config)
        log.info(
            "Step 5 complete: benchmark=%d rows  symbol_info=%d rows",
            len(benchmark_df), len(symbol_info),
        )

        # ── Step 5b: Pre-fetch fundamentals and news (per pre-filter candidates) ──
        # These are passed to run_screen so screener workers can access them without
        # making redundant HTTP calls inside subprocesses.
        fundamentals_map: dict[str, dict] | None = None
        news_scores_map: dict[str, float] | None = None

        if config.get("fundamentals", {}).get("enabled", True):
            try:
                from ingestion.fundamentals import fetch_fundamentals
                from screener.pre_filter import build_features_index, pre_filter as _pf
                _fi = build_features_index(universe, config)
                _candidates = _pf(_fi, config)
                _fmap: dict[str, dict] = {}
                for sym in _candidates:
                    try:
                        _fmap[sym] = fetch_fundamentals(sym)
                    except Exception as _fe:
                        log.warning("Step 5b: fundamentals failed for %s: %s", sym, _fe)
                fundamentals_map = _fmap
                log.info("Step 5b: fetched fundamentals for %d candidates", len(_fmap))
            except Exception as _exc:
                log.warning("Step 5b: fundamentals fetch skipped: %s", _exc)

        if config.get("news", {}).get("enabled", True):
            try:
                from ingestion.news import (
                    compute_news_score,
                    fetch_market_news,
                    fetch_symbol_news,
                )
                from screener.pre_filter import build_features_index, pre_filter as _pf2
                _fi2 = build_features_index(universe, config)
                _cands2 = _pf2(_fi2, config)
                _all_news = fetch_market_news()       # single HTTP call — cached
                _nsmap: dict[str, float] = {}
                for sym in _cands2:
                    try:
                        arts = fetch_symbol_news(sym, _all_news, use_llm=True)
                        _nsmap[sym] = compute_news_score(arts)
                    except Exception as _ne:
                        log.warning("Step 5b: news failed for %s: %s", sym, _ne)
                news_scores_map = _nsmap
                log.info("Step 5b: computed news scores for %d candidates", len(_nsmap))
            except Exception as _exc:
                log.warning("Step 5b: news fetch skipped: %s", _exc)

        # ── Step 6: Run the SEPA screener ───────────────────────────────────
        log.info("Step 6 — run_screen  universe=%d", len(universe))
        results = run_screen(
            universe=universe,
            run_date=run_date,
            config=config,
            symbol_info=symbol_info,
            benchmark_df=benchmark_df,
            fundamentals_map=fundamentals_map,
            news_scores=news_scores_map,
        )

        rep = get_report_summary(results)
        summary["passed_stage2"] = rep["stage2_count"]
        summary["a_plus"] = rep["a_plus"]
        summary["a"] = rep["a"]
        log.info(
            "Step 6 complete: screened=%d  stage2=%d  A+=%d  A=%d",
            len(results), summary["passed_stage2"], summary["a_plus"], summary["a"],
        )

        # ── Step 7: Persist screening results to SQLite ──────────────────────
        log.info("Step 7 — persist_results  count=%d", len(results))
        if not ctx.dry_run:
            persist_results(results, db, run_date)

        # ── Steps 8–13: Non-critical (reports + alerts) ──────────────────────
        # Each wrapped independently; a failure in one does NOT block the others.

        # Step 8 — CSV report
        try:
            csv_path = generate_csv_report(
                results, output_dir, run_date, watchlist_symbols
            )
            summary["report_csv"] = csv_path
            log.info("Step 8: CSV report → %s", csv_path)
        except Exception as exc:
            log.error("Step 8 (CSV report) failed: %s", exc)

        # Step 9 — HTML report
        try:
            html_path = generate_html_report(
                results, output_dir, run_date, watchlist_symbols
            )
            summary["report_html"] = html_path
            log.info("Step 9: HTML report → %s", html_path)
        except Exception as exc:
            log.error("Step 9 (HTML report) failed: %s", exc)

        # Step 10 — Batch charts
        chart_paths: dict[str, str] = {}
        try:
            # vcp_data would ideally be populated by extracting VCPMetrics from
            # SEPAResult objects; for now we pass an empty dict and let
            # generate_batch_charts skip VCP overlays gracefully.
            vcp_data: dict = {}
            chart_paths = generate_batch_charts(
                results=results,
                ohlcv_data=ohlcv_data_full,
                vcp_data=vcp_data,
                output_dir=output_dir,
                run_date=run_date,
                watchlist_symbols=watchlist_symbols,
            )
            log.info("Step 10: generated %d charts", len(chart_paths))
        except Exception as exc:
            log.error("Step 10 (charts) failed: %s", exc)

        # Step 11 — Filter alertable results via deduplication logic
        alertable: list = []
        try:
            for r in results:
                if should_alert(r, db, config):
                    alertable.append(r)
            log.info("Step 11: %d/%d results are alertable", len(alertable), len(results))
        except Exception as exc:
            log.error("Step 11 (alert filter) failed: %s", exc)

        # Step 12 — Send Telegram alerts
        alerts_sent = 0
        try:
            if not ctx.dry_run:
                alerts_sent = send_daily_watchlist(
                    alertable, chart_paths, config, run_date, watchlist_symbols
                )
            summary["alerts_sent"] = alerts_sent
            log.info("Step 12: sent %d Telegram alerts", alerts_sent)
        except Exception as exc:
            log.error("Step 12 (Telegram send) failed: %s", exc)

        # Step 13 — Record each sent alert in SQLite for deduplication
        try:
            if not ctx.dry_run:
                for r in alertable:
                    record_alert(r, db)
        except Exception as exc:
            log.error("Step 13 (record_alert) failed: %s", exc)

        # ── Step 14: Write run_history row to SQLite ─────────────────────────
        duration = time.perf_counter() - t0
        summary["duration_sec"] = round(duration, 2)

        try:
            if not ctx.dry_run:
                db.save_run({
                    "run_date": run_date,
                    "run_mode": ctx.mode,
                    "git_sha": _git_sha(),
                    "config_hash": _config_hash(config),
                    "universe_size": len(universe),
                    "passed_stage2": summary["passed_stage2"],
                    "passed_tt": sum(1 for r in results if r.trend_template_pass),
                    "vcp_qualified": sum(1 for r in results if r.vcp_qualified),
                    "a_plus_count": summary["a_plus"],
                    "a_count": summary["a"],
                    "duration_sec": duration,
                    "status": "success",
                    "error_msg": None,
                })
        except Exception as exc:
            log.error("Step 14 (run history) failed: %s", exc)

        log.info(
            "run_daily COMPLETE  date=%s  duration=%.1fs  "
            "stage2=%d  A+=%d  A=%d  alerts=%d",
            run_date, duration,
            summary["passed_stage2"], summary["a_plus"],
            summary["a"], summary["alerts_sent"],
        )

    except Exception as exc:
        # ── Critical failure: steps 1–7 raised ──────────────────────────────
        duration = time.perf_counter() - t0
        summary["duration_sec"] = round(duration, 2)

        log.exception("run_daily FAILED  date=%s  error=%s", run_date, exc)

        # Attempt Telegram error alert — must NOT mask the original exception
        try:
            send_error_alert(
                f"run_daily failed on {run_date}:\n"
                f"{type(exc).__name__}: {exc}",
                config,
            )
        except Exception as alert_exc:
            log.error("send_error_alert also failed: %s", alert_exc)

        # Attempt to write failure record to run_history
        try:
            db.save_run({
                "run_date": run_date,
                "run_mode": ctx.mode,
                "git_sha": _git_sha(),
                "config_hash": _config_hash(config),
                "universe_size": summary.get("universe_size", 0),
                "passed_stage2": 0,
                "passed_tt": 0,
                "vcp_qualified": 0,
                "a_plus_count": 0,
                "a_count": 0,
                "duration_sec": duration,
                "status": "error",
                "error_msg": f"{type(exc).__name__}: {exc}",
            })
        except Exception:
            pass  # best-effort; do not suppress original exception

        raise  # always re-raise — callers must see the full traceback

    return summary


def run_historical(
    ctx: RunContext,
    start: date,
    end: date,
) -> list[dict]:
    """Run the daily pipeline for each NSE trading day in [start, end].

    Holidays and weekends are automatically skipped via the trading calendar.
    Failures on individual days are caught, logged at ERROR level, and
    represented as error-summary dicts in the return list — they do NOT
    abort the remaining days.

    Parameters
    ----------
    ctx:
        Base :class:`RunContext`.  ``run_date`` is replaced per iteration;
        ``mode`` is overridden to ``"backtest"``.
    start:
        First date of the historical range (inclusive).
    end:
        Last date of the historical range (inclusive).

    Returns
    -------
    list[dict]
        One summary dict per trading day processed (success or error).
    """
    td_index = get_trading_days(start.isoformat(), end.isoformat())
    summaries: list[dict] = []

    log.info(
        "run_historical: %d trading days in [%s, %s]",
        len(td_index), start, end,
    )

    for ts in td_index:
        day = ts.date() if hasattr(ts, "date") else ts
        day_ctx = replace(ctx, run_date=day, mode="backtest")
        log.info("run_historical: processing %s", day)
        try:
            summary = run_daily(day_ctx)
            summaries.append(summary)
        except Exception as exc:
            log.error("run_historical: run for %s failed: %s", day, exc)
            summaries.append({
                "run_date": str(day),
                "status": "error",
                "error": str(exc),
                "duration_sec": 0.0,
                "universe_size": 0,
                "passed_stage2": 0,
                "a_plus": 0,
                "a": 0,
                "report_csv": "",
                "report_html": "",
                "alerts_sent": 0,
            })

    log.info(
        "run_historical: completed %d/%d days", len(summaries), len(td_index)
    )
    return summaries
