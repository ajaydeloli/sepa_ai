#!/usr/bin/env python3
"""
scripts/rebuild_features.py
----------------------------
CLI: Recompute all feature Parquet files from scratch.

Reads the processed OHLCV Parquet files for each symbol and calls
``features.feature_store.bootstrap()`` to regenerate every feature file.
Run this once after a fresh setup or whenever the feature schema changes.

Usage examples
--------------
    python scripts/rebuild_features.py --universe nifty500
    python scripts/rebuild_features.py --universe all
    python scripts/rebuild_features.py --symbols "RELIANCE,TCS,INFY"
    python scripts/rebuild_features.py --universe nifty500 --dry-run
    python scripts/rebuild_features.py --universe nifty500 --workers 8

Options
-------
--universe  : "nifty500" | "all" | "custom"  (default: "nifty500")
--symbols   : comma-separated list (overrides --universe if provided)
--config    : path to settings.yaml (default: "config/settings.yaml")
--dry-run   : log what would happen, skip all writes
--workers   : number of parallel workers for ProcessPoolExecutor (default: 4)
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

from ingestion.nsepython_universe import get_universe  # noqa: E402
from utils.logger import get_logger  # noqa: E402

log = get_logger("rebuild_features")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rebuild_features",
        description="SEPA AI — rebuild all feature Parquet files from scratch",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--universe",
        default="nifty500",
        choices=["nifty500", "all", "custom"],
        help='Symbol universe to rebuild. Ignored when --symbols is provided.',
    )
    parser.add_argument(
        "--symbols",
        default=None,
        metavar="SYMBOLS",
        help='Comma-separated symbols, e.g. "RELIANCE,TCS,INFY". Overrides --universe.',
    )
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        metavar="FILE",
        help="Path to settings.yaml.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would happen without writing any files.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        metavar="N",
        help="Number of parallel workers (ProcessPoolExecutor).",
    )
    return parser.parse_args()


def _resolve_symbols(args: argparse.Namespace, config: dict) -> list[str]:
    """Return the list of symbols to rebuild, respecting --symbols override."""
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        log.info("--symbols override: %d symbols specified on CLI.", len(symbols))
        return symbols

    # Use universe loader
    index = args.universe
    if index == "custom":
        # Fall back to whatever is in config
        index = config.get("universe", {}).get("index", "nifty500")
    log.info("Loading universe '%s' via nsepython …", index)
    try:
        symbols = get_universe(index)
        log.info("Universe '%s' resolved to %d symbols.", index, len(symbols))
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to load universe '%s': %s", index, exc)
        sys.exit(1)
    return symbols


# ---------------------------------------------------------------------------
# Worker: runs in a subprocess (must be importable at module level)
# ---------------------------------------------------------------------------

def _bootstrap_symbol(symbol: str, config: dict) -> str:
    """Bootstrap a single symbol.  Called inside a worker process.

    Returns the symbol string on success.  Raises on failure so the
    Future carries the exception back to the main process.
    """
    # Re-import here because this runs in a fresh subprocess
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

    from features.feature_store import bootstrap  # noqa: PLC0415
    bootstrap(symbol, config)
    return symbol


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

    # ── Symbol list ────────────────────────────────────────────────────────
    symbols = _resolve_symbols(args, config)
    total = len(symbols)

    if total == 0:
        log.warning("No symbols resolved — nothing to do.")
        return

    # ── Dry-run: print and exit ────────────────────────────────────────────
    if args.dry_run:
        log.info(
            "[DRY-RUN] Would rebuild features for %d symbols using %d workers.",
            total, args.workers,
        )
        print(f"\n[DRY-RUN] Would rebuild {total} symbols (workers={args.workers}):")
        for sym in symbols:
            print(f"  {sym}")
        print()
        return

    # ── Live run ───────────────────────────────────────────────────────────
    log.info(
        "Starting feature rebuild for %d symbols with %d workers …",
        total, args.workers,
    )
    print(f"\nRebuilding features for {total} symbols (workers={args.workers}) …\n")

    start_time = time.monotonic()
    success: int = 0
    failed: int = 0
    failed_symbols: list[str] = []

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        future_to_sym = {
            executor.submit(_bootstrap_symbol, sym, config): sym
            for sym in symbols
        }

        for future in as_completed(future_to_sym):
            sym = future_to_sym[future]
            try:
                future.result()
                success += 1
            except Exception as exc:  # noqa: BLE001
                log.error("bootstrap(%s) FAILED: %s", sym, exc)
                failed += 1
                failed_symbols.append(sym)

            # Progress update every 10 symbols
            n_done = success + failed
            if n_done % 10 == 0:
                print(f"  Rebuilt {n_done}/{total} symbols …")

    elapsed = time.monotonic() - start_time
    print(f"\nRebuilt {success} / {total} symbols in {elapsed:.1f}s")
    if failed_symbols:
        log.warning(
            "Failed symbols (%d): %s", len(failed_symbols), ", ".join(failed_symbols)
        )
        print(f"Failed symbols ({failed}): {', '.join(failed_symbols)}")

    log.info(
        "Feature rebuild complete: %d succeeded, %d failed, elapsed=%.1fs",
        success, failed, elapsed,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Rebuild interrupted by user (KeyboardInterrupt). Exiting cleanly.")
        sys.exit(0)
