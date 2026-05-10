#!/usr/bin/env python
"""
scripts/run_daily.py
--------------------
Daily run CLI for the SEPA AI pipeline.

Phase 1 — ingestion, symbol-resolution, and OHLCV persistence.
Fetched rows are validated and appended to data/processed/{symbol}.parquet
via append_row() so the processed store stays current after every run.
Features, rules, and scoring are not yet implemented.

Usage examples
--------------
    python scripts/run_daily.py --date today --dry-run
    python scripts/run_daily.py --watchlist tests/fixtures/sample_watchlist.csv --dry-run
    python scripts/run_daily.py --symbols "RELIANCE,TCS,INFY" --dry-run
    python scripts/run_daily.py --watchlist-only --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

# Ensure project root on sys.path when executed directly
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from ingestion.universe_loader import load_watchlist_file, resolve_symbols  # noqa: E402
from ingestion.validator import validate  # noqa: E402
from ingestion.yfinance_source import YFinanceSource  # noqa: E402
from pipeline.context import RunContext  # noqa: E402
from storage.parquet_store import append_row, get_last_date, write_parquet  # noqa: E402
from storage.sqlite_store import SQLiteStore  # noqa: E402
from utils.exceptions import DataSourceError, DataValidationError, FeatureStoreOutOfSyncError, InsufficientDataError  # noqa: E402
from utils.logger import get_logger  # noqa: E402

log = get_logger("run_daily")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_daily",
        description="SEPA AI — daily screening run (Phase 1 skeleton)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--date",
        default="today",
        metavar="DATE",
        help='Run date as ISO string YYYY-MM-DD or "today"',
    )
    parser.add_argument(
        "--watchlist",
        default=None,
        metavar="FILE",
        help="Path to watchlist file (.csv / .json / .xlsx / .txt)",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        metavar="SYMBOLS",
        help='Comma-separated symbols, e.g. "RELIANCE,TCS,INFY"',
    )
    parser.add_argument(
        "--watchlist-only",
        action="store_true",
        help="Skip universe scan; process only watchlist symbols",
    )
    parser.add_argument(
        "--scope",
        choices=["all", "universe", "watchlist"],
        default="all",
        help='Symbol scope (overridden to "watchlist" when --watchlist-only is set)',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved symbol list and exit without fetching or writing",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()

    # ── Run date ───────────────────────────────────────────────────────────
    if args.date == "today":
        run_date = date.today()
    else:
        try:
            run_date = date.fromisoformat(args.date)
        except ValueError:
            log.error("Invalid --date %r — expected YYYY-MM-DD or 'today'.", args.date)
            sys.exit(1)

    # ── Config ─────────────────────────────────────────────────────────────
    config = _load_config(_ROOT / "config" / "settings.yaml")

    # ── Scope ──────────────────────────────────────────────────────────────
    scope = "watchlist" if args.watchlist_only else args.scope

    # ── Database ───────────────────────────────────────────────────────────
    raw_db_path = config.get("watchlist", {}).get("persist_path", "data/sepa_ai.db")
    db_path = Path(raw_db_path)
    if not db_path.is_absolute():
        db_path = _ROOT / db_path
    db = SQLiteStore(db_path)

    # ── Watchlist file → bulk-add to SQLite ────────────────────────────────
    watchlist_path: Path | None = None
    if args.watchlist:
        watchlist_path = Path(args.watchlist)
        if not watchlist_path.is_absolute():
            watchlist_path = Path.cwd() / watchlist_path
        try:
            file_syms = load_watchlist_file(watchlist_path)
            db.bulk_add(file_syms, added_via="cli_file")
            log.info(
                "Loaded %d symbol(s) from '%s' into watchlist.",
                len(file_syms),
                watchlist_path.name,
            )
        except Exception as exc:
            log.warning("Could not load watchlist file: %s", exc)
            watchlist_path = None


    # ── CLI symbols ────────────────────────────────────────────────────────
    cli_symbols: list[str] | None = None
    if args.symbols:
        cli_symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    # ── Resolve symbols ────────────────────────────────────────────────────
    run_symbols = resolve_symbols(
        config=config,
        db=db,
        cli_watchlist_file=watchlist_path,
        cli_symbols=cli_symbols,
        scope=scope,
    )

    # ── Build RunContext ───────────────────────────────────────────────────
    ctx = RunContext(
        run_date=run_date,
        mode="daily",
        config=config,
        scope=scope,
        dry_run=args.dry_run,
        symbols_override=cli_symbols,
    )

    # ── Dry-run: print and exit ────────────────────────────────────────────
    if ctx.dry_run:
        log.info(
            "Phase 1 skeleton: ingestion and rules not yet implemented. "
            "Resolved %d symbols for run date %s.",
            len(run_symbols.all),
            run_date,
        )
        print(
            f"\n[DRY-RUN] Resolved {len(run_symbols.all)} symbol(s) "
            f"for run date {run_date}:"
        )
        for sym in run_symbols.all:
            print(f"  {sym}")
        print()
        return


    # ── Live run: fetch → validate → append to processed store ──────────────
    log.info(
        "Resolved %d symbols for run date %s. Fetching and persisting OHLCV …",
        len(run_symbols.all),
        run_date,
    )

    # ── Processed-parquet directory (mirrors bootstrap.py) ─────────────────
    raw_processed = config.get("data", {}).get("processed_dir", "data/processed")
    processed_dir = Path(raw_processed)
    if not processed_dir.is_absolute():
        processed_dir = _ROOT / processed_dir
    processed_dir.mkdir(parents=True, exist_ok=True)

    # ── History depth for new symbols ──────────────────────────────────────
    # Reads from config/settings.yaml → data.bootstrap_years (default 5).
    bootstrap_years: int = int(config.get("data", {}).get("bootstrap_years", 5))

    source = YFinanceSource()
    end_date = run_date

    success: int = 0        # existing symbols gap-filled
    bootstrapped: int = 0   # new symbols fetched with full history
    skipped: int = 0        # already up to date
    failed: int = 0
    failed_symbols: list[str] = []

    for symbol in run_symbols.all:
        parquet_path = processed_dir / f"{symbol}.parquet"

        # ── Decide fetch range ──────────────────────────────────────────────
        is_new_symbol = not parquet_path.exists()

        if is_new_symbol:
            # New symbol (or deleted parquet): fetch up to bootstrap_years of history.
            # yfinance will return whatever is available for newly listed stocks.
            start_date = end_date - timedelta(days=bootstrap_years * 365)
            log.info(
                "New symbol %s — bootstrapping up to %d years of history from %s.",
                symbol, bootstrap_years, start_date,
            )
        else:
            # Existing symbol: fetch from the day after the last stored date so
            # no gaps are left even if the pipeline was not run for a long time.
            last_date = get_last_date(parquet_path)
            if last_date is None:
                # Parquet file exists but is empty — treat as new.
                start_date = end_date - timedelta(days=bootstrap_years * 365)
                is_new_symbol = True
                log.warning(
                    "Symbol %s has an empty parquet — bootstrapping full history from %s.",
                    symbol, start_date,
                )
            else:
                start_date = last_date + timedelta(days=1)
                if start_date > end_date:
                    log.debug(
                        "Symbol %s already up to date (last=%s). Skipping.", symbol, last_date
                    )
                    skipped += 1
                    continue
                log.info(
                    "Symbol %s: gap-fill from %s → %s.",
                    symbol, start_date, end_date,
                )
        try:
            df = source.fetch(symbol, start=start_date, end=end_date)
        except DataSourceError as exc:
            log.warning("fetch(%s): DataSourceError — %s", symbol, exc)
            failed += 1
            failed_symbols.append(symbol)
            continue
        except Exception as exc:  # noqa: BLE001
            log.warning("fetch(%s): unexpected error — %s", symbol, exc)
            failed += 1
            failed_symbols.append(symbol)
            continue

        if df.empty:
            log.warning("fetch(%s): empty DataFrame — skipping.", symbol)
            failed += 1
            failed_symbols.append(symbol)
            continue

        # ── Validate ───────────────────────────────────────────────────────
        # min_rows=1 so newly listed symbols with limited history are accepted.
        # For new symbols skip run_date so gap detection doesn't fire on the
        # historical range; for existing symbols pass run_date for daily checks.
        try:
            df = validate(
                df,
                symbol,
                run_date=None if is_new_symbol else run_date,
                min_rows=1,
            )
        except (DataValidationError, InsufficientDataError) as exc:
            log.warning("validate(%s) failed: %s", symbol, exc)
            failed += 1
            failed_symbols.append(symbol)
            continue

        # ── Write to processed parquet ─────────────────────────────────────
        # New symbols: write_parquet (full historical write).
        # Existing symbols: append_row (gap-fill; overlap check built in).
        try:
            if is_new_symbol:
                write_parquet(parquet_path, df)
                log.info(
                    "bootstrap(%s): %d rows written → %s",
                    symbol, len(df), parquet_path.name,
                )
                bootstrapped += 1
            else:
                append_row(parquet_path, df)
                log.info(
                    "append(%s): %d row(s) gap-filled → %s",
                    symbol, len(df), parquet_path.name,
                )
                success += 1
        except FeatureStoreOutOfSyncError:
            # Data already present — idempotent re-run, not an error.
            log.debug("append(%s): data already exists — skipping.", symbol)
            skipped += 1
        except Exception as exc:  # noqa: BLE001
            log.error("write(%s) failed: %s", symbol, exc)
            failed += 1
            failed_symbols.append(symbol)

    log.info(
        "Daily run complete: %d bootstrapped, %d gap-filled, %d already up-to-date, "
        "%d failed (of %d total).",
        bootstrapped, success, skipped, failed, len(run_symbols.all),
    )
    print(
        f"\nDaily run complete: {bootstrapped} bootstrapped, {success} gap-filled, "
        f"{skipped} already up-to-date, {failed} failed (of {len(run_symbols.all)} total)."
    )
    if failed_symbols:
        log.warning("Failed symbols: %s", ", ".join(failed_symbols))
        print(f"Failed symbols ({len(failed_symbols)}): {', '.join(failed_symbols)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Run interrupted by user (KeyboardInterrupt). Exiting cleanly.")
        sys.exit(0)
