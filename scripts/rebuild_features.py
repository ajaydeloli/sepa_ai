#!/usr/bin/env python3
"""
scripts/rebuild_features.py
----------------------------
CLI: Recompute feature Parquet files that are missing or stale.

For each symbol in the resolved universe this script calls
``features.feature_store.needs_bootstrap()`` and only rebuilds when the
feature file is absent / empty — or when ``--force`` is passed.

Usage examples
--------------
    python scripts/rebuild_features.py
    python scripts/rebuild_features.py --universe nse_all
    python scripts/rebuild_features.py --universe /path/to/symbols.csv
    python scripts/rebuild_features.py --symbol RELIANCE
    python scripts/rebuild_features.py --universe nifty500 --force
    python scripts/rebuild_features.py --universe nifty500 --dry-run
    python scripts/rebuild_features.py --universe nifty500 --workers 8

Exit codes
----------
0  — all attempted rebuilds succeeded (or nothing to do).
1  — one or more symbols failed.
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import yaml

# Ensure project root on sys.path when executed directly
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from features.feature_store import bootstrap, needs_bootstrap  # noqa: E402
from ingestion.universe_loader import load_watchlist_file      # noqa: E402
from ingestion.nsepython_universe import get_universe          # noqa: E402
from utils.logger import get_logger                            # noqa: E402

log = get_logger("rebuild_features")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rebuild_features",
        description="SEPA AI — rebuild feature Parquet files that are missing or stale",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--universe",
        default="nifty500",
        metavar="UNIVERSE",
        help=(
            'Symbol universe: "nifty500", "nse_all", or path to a CSV file '
            "with a 'symbol' column.  Ignored when --symbol is provided."
        ),
    )
    parser.add_argument(
        "--symbol",
        default=None,
        metavar="STR",
        help="Rebuild a single symbol only (overrides --universe).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild even if the feature file exists and seems valid.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List symbols that would be rebuilt; do not write any files.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        metavar="N",
        help="Parallel worker processes.",
    )
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        metavar="FILE",
        help="Path to settings.yaml.",
    )
    return parser.parse_args()

# ---------------------------------------------------------------------------
# Universe / symbol resolution
# ---------------------------------------------------------------------------

def _resolve_universe(args: argparse.Namespace, config: dict) -> list[str]:
    """Return the list of symbols to consider, before needs_bootstrap filtering."""

    # Single-symbol shortcut
    if args.symbol:
        symbol = args.symbol.strip().upper()
        log.info("--symbol override: single symbol %s.", symbol)
        return [symbol]

    universe_arg = args.universe.strip()

    # Path to CSV file
    csv_path = Path(universe_arg)
    if csv_path.suffix.lower() == ".csv" or csv_path.exists():
        log.info("Loading universe from CSV file: %s", csv_path)
        try:
            symbols = load_watchlist_file(csv_path)
            log.info("CSV universe loaded: %d symbols.", len(symbols))
            return symbols
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to load universe CSV '%s': %s", csv_path, exc)
            sys.exit(1)

    # Named universe: nifty500 or nse_all
    known = {"nifty500", "nse_all"}
    if universe_arg not in known:
        log.error(
            "Unknown --universe %r.  Expected one of %s or a path to a CSV file.",
            universe_arg,
            sorted(known),
        )
        sys.exit(1)

    log.info("Loading universe '%s' via nsepython …", universe_arg)
    try:
        symbols = get_universe(universe_arg)
        log.info("Universe '%s' resolved to %d symbols.", universe_arg, len(symbols))
        return symbols
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to load universe '%s': %s", universe_arg, exc)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Worker (subprocess-safe — must be importable at module level)
# ---------------------------------------------------------------------------

def _worker_bootstrap(symbol: str, config: dict) -> str:
    """Bootstrap *symbol* inside a worker process.  Returns symbol on success."""
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

    from features.feature_store import bootstrap as _bootstrap  # noqa: PLC0415
    _bootstrap(symbol, config)
    return symbol


# ---------------------------------------------------------------------------
# Core rebuild logic
# ---------------------------------------------------------------------------

def _pick_symbols_to_rebuild(
    all_symbols: list[str],
    config: dict,
    force: bool,
) -> list[str]:
    """Return only those symbols that need rebuilding."""
    if force:
        return list(all_symbols)

    to_rebuild: list[str] = []
    for sym in all_symbols:
        try:
            if needs_bootstrap(sym, config):
                to_rebuild.append(sym)
            else:
                log.debug("Skipping %s — feature file OK", sym)
        except Exception as exc:  # noqa: BLE001
            log.warning("needs_bootstrap(%s) raised %s — will rebuild.", sym, exc)
            to_rebuild.append(sym)

    return to_rebuild


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()

    # ── Config ─────────────────────────────────────────────────────────────
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = _ROOT / config_path
    if not config_path.exists():
        log.error("Config file not found: %s", config_path)
        sys.exit(1)
    config = _load_config(config_path)

    # ── All symbols in universe ────────────────────────────────────────────
    all_symbols = _resolve_universe(args, config)
    total = len(all_symbols)

    if total == 0:
        log.warning("No symbols resolved — nothing to do.")
        return

    # ── Filter: which ones actually need work? ─────────────────────────────
    to_rebuild = _pick_symbols_to_rebuild(all_symbols, config, force=args.force)
    n_rebuild = len(to_rebuild)
    n_skip = total - n_rebuild

    log.info(
        "Universe: %d symbols | to rebuild: %d | skipping (feature file OK): %d",
        total, n_rebuild, n_skip,
    )

    # ── Dry-run ────────────────────────────────────────────────────────────
    if args.dry_run:
        qualifier = " (force)" if args.force else " (needs_bootstrap)"
        print(
            f"\n[DRY-RUN] Would rebuild {n_rebuild}/{total} symbols{qualifier}:"
        )
        for sym in to_rebuild:
            print(f"  {sym}")
        if n_skip:
            print(f"\n  ({n_skip} symbol(s) skipped — feature file already OK)")
        print()
        return

    if n_rebuild == 0:
        print(f"\nAll {total} symbols are up-to-date — nothing to rebuild.")
        return

    # ── Live run ───────────────────────────────────────────────────────────
    print(
        f"\nRebuilding {n_rebuild}/{total} symbols "
        f"(workers={args.workers}) …\n"
    )
    log.info(
        "Starting rebuild for %d/%d symbols with %d workers …",
        n_rebuild, total, args.workers,
    )

    start_time = time.monotonic()
    success: int = 0
    failed: int = 0
    failed_symbols: list[str] = []

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        future_to_sym = {
            executor.submit(_worker_bootstrap, sym, config): sym
            for sym in to_rebuild
        }

        for future in as_completed(future_to_sym):
            sym = future_to_sym[future]
            try:
                future.result()
                success += 1
                log.info("Rebuilt %s", sym)
            except Exception as exc:  # noqa: BLE001
                log.error("bootstrap(%s) FAILED: %s", sym, exc)
                failed += 1
                failed_symbols.append(sym)

            n_done = success + failed
            if n_done % 10 == 0:
                print(f"  Progress: {n_done}/{n_rebuild} …")

    elapsed = time.monotonic() - start_time

    # ── Summary ────────────────────────────────────────────────────────────
    print(f"\nRebuilt {success}/{total} symbols in {elapsed:.1f}s")
    log.info(
        "Rebuild complete: %d rebuilt, %d skipped, %d failed, elapsed=%.1fs",
        success, n_skip, failed, elapsed,
    )

    if failed_symbols:
        print(f"Failures: {failed}")
        log.warning(
            "Failed symbols (%d): %s", failed, ", ".join(failed_symbols)
        )
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Rebuild interrupted by user. Exiting cleanly.")
        sys.exit(0)
