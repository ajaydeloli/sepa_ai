#!/usr/bin/env python
"""
scripts/bootstrap.py
--------------------
Bootstrap full OHLCV history (5 years) for all symbols in a universe.

For each symbol the script:
  1. Skips if ``data/processed/{SYMBOL}.parquet`` already exists (unless --force).
  2. Fetches OHLCV history via :class:`~ingestion.yfinance_source.YFinanceSource`.
  3. Validates the data via :func:`~ingestion.validator.validate`.
  4. Writes to ``data/processed/{SYMBOL}.parquet`` via :func:`~storage.parquet_store.write_parquet`.

Progress is logged every 50 symbols and a summary is printed at the end.

Usage
-----
    python scripts/bootstrap.py --universe nifty500
    python scripts/bootstrap.py --universe nse_all --start-date 2018-01-01
    python scripts/bootstrap.py --symbols "RELIANCE,TCS,INFY"
    python scripts/bootstrap.py --universe nifty500 --force
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from ingestion.nsepython_universe import get_universe  # noqa: E402
from ingestion.yfinance_source import YFinanceSource  # noqa: E402
from ingestion.validator import validate  # noqa: E402
from storage.parquet_store import write_parquet  # noqa: E402
from utils.exceptions import DataSourceError, DataValidationError, InsufficientDataError  # noqa: E402
from utils.logger import get_logger  # noqa: E402

log = get_logger("bootstrap")

_HISTORY_YEARS = 5


def _load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bootstrap",
        description="SEPA AI — bootstrap full OHLCV history for a universe",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--universe",
        choices=["nifty500", "nse_all"],
        default="nifty500",
        help="Universe to bootstrap",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        metavar="DATE",
        help="Override start date (YYYY-MM-DD). Default: 5 years ago.",
    )
    parser.add_argument(
        "--symbols",
        default=None,
        metavar="SYMBOLS",
        help='Comma-separated symbols — overrides --universe',
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if parquet file already exists",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    config = _load_config(_ROOT / "config" / "settings.yaml")

    # ── Output directory ───────────────────────────────────────────────────
    processed_dir = Path(config.get("data", {}).get("processed_dir", "data/processed"))
    if not processed_dir.is_absolute():
        processed_dir = _ROOT / processed_dir
    processed_dir.mkdir(parents=True, exist_ok=True)

    # ── Symbol list ────────────────────────────────────────────────────────
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        log.info("Bootstrap: %d CLI symbol(s).", len(symbols))
    else:
        log.info("Loading universe: %s …", args.universe)
        symbols = get_universe(args.universe)
        log.info("Universe loaded: %d symbol(s).", len(symbols))

    if not symbols:
        log.error("No symbols to bootstrap — check universe connectivity. Exiting.")
        sys.exit(1)

    # ── Date range ─────────────────────────────────────────────────────────
    end_date = date.today()
    if args.start_date:
        try:
            start_date = date.fromisoformat(args.start_date)
        except ValueError:
            log.error("Invalid --start-date %r — expected YYYY-MM-DD.", args.start_date)
            sys.exit(1)
    else:
        start_date = end_date - timedelta(days=_HISTORY_YEARS * 365)

    log.info("Date range: %s → %s", start_date, end_date)

    source = YFinanceSource()
    success: int = 0
    failed: int = 0
    skipped: int = 0
    failed_symbols: list[str] = []


    for i, symbol in enumerate(symbols, start=1):
        parquet_path = processed_dir / f"{symbol}.parquet"

        # ── Skip if exists and no --force ──────────────────────────────────
        if parquet_path.exists() and not args.force:
            log.debug("Skipping %s — parquet already exists.", symbol)
            skipped += 1
            if i % 50 == 0:
                log.info(
                    "Progress: %d/%d (success=%d, failed=%d, skipped=%d)",
                    i, len(symbols), success, failed, skipped,
                )
            continue

        # ── Fetch ──────────────────────────────────────────────────────────
        try:
            df = source.fetch(symbol, start=start_date, end=end_date)
        except DataSourceError as exc:
            log.warning("fetch(%s) failed: %s", symbol, exc)
            failed += 1
            failed_symbols.append(symbol)
            continue

        if df.empty:
            log.warning("fetch(%s): empty DataFrame — skipping.", symbol)
            failed += 1
            failed_symbols.append(symbol)
            continue

        # ── Validate ───────────────────────────────────────────────────────
        try:
            df = validate(df, symbol)
        except (DataValidationError, InsufficientDataError) as exc:
            log.warning("validate(%s) failed: %s", symbol, exc)
            failed += 1
            failed_symbols.append(symbol)
            continue

        # ── Write ──────────────────────────────────────────────────────────
        try:
            write_parquet(parquet_path, df)
            log.info("Written: %s (%d rows) → %s", symbol, len(df), parquet_path.name)
            success += 1
        except Exception as exc:  # noqa: BLE001
            log.error("write_parquet(%s) failed: %s", symbol, exc)
            failed += 1
            failed_symbols.append(symbol)

        # ── Progress every 50 symbols ──────────────────────────────────────
        if i % 50 == 0:
            log.info(
                "Progress: %d/%d processed (success=%d, failed=%d, skipped=%d)",
                i, len(symbols), success, failed, skipped,
            )

    # ── Final summary ──────────────────────────────────────────────────────
    total = len(symbols)
    print(
        f"\nBootstrap complete: {success} successful, {failed} failed, "
        f"{skipped} skipped (of {total} total)."
    )
    if failed_symbols:
        print(f"Failed symbols ({len(failed_symbols)}): {', '.join(failed_symbols)}")
    log.info(
        "Bootstrap summary: %d successful, %d failed, %d skipped (total=%d).",
        success, failed, skipped, total,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Bootstrap interrupted by user (KeyboardInterrupt). Exiting cleanly.")
        sys.exit(0)
