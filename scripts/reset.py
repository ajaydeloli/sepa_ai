#!/usr/bin/env python
"""
scripts/reset.py
----------------
Reset the SEPA AI project to a clean, fresh-installation state.

What gets wiped by default (--all):
  • SQLite databases       data/sepa_ai.db  data/minervini.db  (schema re-created)
  • SQLite WAL/SHM files   *.db-wal  *.db-shm
  • Paper-trading state    data/paper_trading/portfolio.json + trades.json
  • Screening results      data/features/*.parquet
  • Processed OHLCV        data/processed/*.parquet
  • Raw downloads          data/raw/*  (non-.gitkeep)
  • Fundamentals cache     data/fundamentals/*.json
  • News cache             data/news/market_news.json
  • Daily reports          data/reports/*.csv / *.html   reports/*.csv / *.html
  • Rotating log files     logs/sepa_ai.log*
  • Metadata cache         data/metadata/symbol_info.csv
  • Next.js build cache    frontend/.next/  (optional, see --keep-frontend)

Selective flags let you reset only specific subsystems.

Usage examples
--------------
    python scripts/reset.py                    # interactive full reset
    python scripts/reset.py --all --yes        # full reset, no prompts
    python scripts/reset.py --db --yes         # databases only
    python scripts/reset.py --data             # downloaded data only
    python scripts/reset.py --paper            # paper-trading portfolio only
    python scripts/reset.py --logs             # log files only
    python scripts/reset.py --dry-run --all    # preview every file to be deleted
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Callable

# ── Project root ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent

# ── ANSI colours (gracefully disabled on non-TTY) ─────────────────────────────
_TTY = sys.stdout.isatty()
_R  = "\033[0;31m"  if _TTY else ""   # red
_G  = "\033[0;32m"  if _TTY else ""   # green
_Y  = "\033[1;33m"  if _TTY else ""   # yellow
_B  = "\033[1;34m"  if _TTY else ""   # blue
_DIM = "\033[2m"    if _TTY else ""   # dim
_NC = "\033[0m"     if _TTY else ""   # reset


def _info(msg: str)    -> None: print(f"{_G}  ✔{_NC}  {msg}")
def _warn(msg: str)    -> None: print(f"{_Y}  ⚠{_NC}  {msg}")
def _skip(msg: str)    -> None: print(f"{_DIM}  –  {msg}{_NC}")
def _dry(msg: str)     -> None: print(f"{_B}  ~{_NC}  {_DIM}[dry]{_NC} {msg}")
def _section(msg: str) -> None: print(f"\n{_B}▶  {msg}{_NC}")
def _error(msg: str)   -> None: print(f"{_R}  ✘{_NC}  {msg}", file=sys.stderr)


# ── Counters ──────────────────────────────────────────────────────────────────
_deleted  = 0
_skipped  = 0
_errors   = 0


def _rm(path: Path, dry: bool) -> None:
    """Delete a single file, tracking counts."""
    global _deleted, _skipped, _errors
    if not path.exists():
        _skipped += 1
        _skip(f"(not found) {path.relative_to(ROOT)}")
        return
    if dry:
        _dry(str(path.relative_to(ROOT)))
        _deleted += 1
        return
    try:
        path.unlink()
        _info(str(path.relative_to(ROOT)))
        _deleted += 1
    except OSError as exc:
        _error(f"Could not delete {path.relative_to(ROOT)}: {exc}")
        _errors += 1


def _rmdir(path: Path, dry: bool) -> None:
    """Recursively delete a directory, tracking counts."""
    global _deleted, _skipped, _errors
    if not path.exists():
        _skipped += 1
        _skip(f"(not found) {path.relative_to(ROOT)}/")
        return
    if dry:
        count = sum(1 for _ in path.rglob("*") if _.is_file())
        _dry(f"{path.relative_to(ROOT)}/  ({count} files)")
        _deleted += count
        return
    try:
        shutil.rmtree(path)
        _info(f"{path.relative_to(ROOT)}/  (removed)")
        _deleted += 1
    except OSError as exc:
        _error(f"Could not remove {path.relative_to(ROOT)}: {exc}")
        _errors += 1


def _glob_rm(pattern_root: Path, pattern: str, dry: bool) -> None:
    """Delete all files matching a glob pattern inside a directory."""
    paths = sorted(pattern_root.glob(pattern))
    if not paths:
        _skip(f"(nothing to delete) {pattern_root.relative_to(ROOT)}/{pattern}")
        return
    for p in paths:
        _rm(p, dry)


# ── DDL (mirrors storage/sqlite_store.py) ─────────────────────────────────────
_DDL = """
CREATE TABLE IF NOT EXISTS watchlist (
    id           INTEGER PRIMARY KEY,
    symbol       TEXT NOT NULL UNIQUE,
    note         TEXT,
    added_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    added_via    TEXT NOT NULL DEFAULT 'cli',
    last_score   REAL,
    last_quality TEXT,
    last_run_at  TIMESTAMP
);
CREATE TABLE IF NOT EXISTS run_history (
    id              INTEGER PRIMARY KEY,
    run_date        DATE NOT NULL,
    run_mode        TEXT NOT NULL,
    git_sha         TEXT,
    config_hash     TEXT,
    universe_size   INTEGER,
    passed_stage2   INTEGER,
    passed_tt       INTEGER,
    vcp_qualified   INTEGER,
    a_plus_count    INTEGER,
    a_count         INTEGER,
    duration_sec    REAL,
    status          TEXT NOT NULL,
    error_msg       TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS screen_results (
    id                  INTEGER PRIMARY KEY,
    run_date            DATE NOT NULL,
    symbol              TEXT NOT NULL,
    stage               INTEGER,
    score               REAL,
    setup_quality       TEXT,
    trend_template_pass INTEGER,
    vcp_qualified       INTEGER,
    breakout_triggered  INTEGER,
    rs_rating           INTEGER,
    entry_price         REAL,
    stop_loss           REAL,
    risk_pct            REAL,
    result_json         TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_date, symbol)
);
CREATE TABLE IF NOT EXISTS alerts (
    id                 INTEGER PRIMARY KEY,
    symbol             TEXT NOT NULL,
    alerted_date       DATE NOT NULL,
    score              REAL,
    quality            TEXT,
    breakout_triggered INTEGER DEFAULT 0,
    channel            TEXT,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _recreate_db(db_path: Path, dry: bool) -> None:
    """Wipe and re-initialise a SQLite database with a clean schema."""
    global _deleted, _errors
    rel = db_path.relative_to(ROOT)
    # Also nuke WAL / SHM companions
    for suffix in ("", "-wal", "-shm"):
        p = db_path.with_suffix(db_path.suffix + suffix) if suffix else db_path
        p2 = Path(str(db_path) + suffix) if suffix else db_path
        if p2 != db_path:
            _rm(p2, dry)
    if dry:
        _dry(f"recreate schema → {rel}")
        _deleted += 1
        return
    # Delete the main file last (after WAL/SHM)
    if db_path.exists():
        try:
            db_path.unlink()
            _info(f"deleted {rel}")
            _deleted += 1
        except OSError as exc:
            _error(f"Could not delete {rel}: {exc}")
            _errors += 1
            return
    # Re-create with fresh schema
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript(_DDL)
        conn.close()
        _info(f"schema re-created → {rel}")
    except Exception as exc:  # noqa: BLE001
        _error(f"Could not recreate schema for {rel}: {exc}")
        _errors += 1


def _reset_paper_trading(dry: bool, initial_capital: float = 100_000.0) -> None:
    """Restore paper-trading JSON files to their empty initial state."""
    portfolio_path = ROOT / "data" / "paper_trading" / "portfolio.json"
    trades_path    = ROOT / "data" / "paper_trading" / "trades.json"

    blank_portfolio = {
        "initial_capital": initial_capital,
        "cash": initial_capital,
        "positions": {},
        "closed_trades": [],
        "equity_curve": [],
    }

    for path, content in [
        (portfolio_path, blank_portfolio),
        (trades_path,    []),
    ]:
        rel = path.relative_to(ROOT)
        if dry:
            _dry(f"reset {rel}")
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
            _info(f"reset {rel}")
        except OSError as exc:
            _error(f"Could not reset {rel}: {exc}")


def _read_initial_capital() -> float:
    """Read paper_trading.initial_capital from config/settings.yaml (fallback 100 000)."""
    try:
        import yaml  # type: ignore[import-untyped]
        cfg_path = ROOT / "config" / "settings.yaml"
        with cfg_path.open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        return float(cfg.get("paper_trading", {}).get("initial_capital", 100_000))
    except Exception:  # noqa: BLE001
        return 100_000.0


# ── Individual reset sections ─────────────────────────────────────────────────

def reset_databases(dry: bool) -> None:
    _section("Databases (SQLite)")
    for name in ("sepa_ai.db", "minervini.db"):
        _recreate_db(ROOT / "data" / name, dry)
    # Remove stale test DB (no need to recreate)
    _rm(ROOT / "data" / "test_smoke.db", dry)


def reset_paper_trading(dry: bool) -> None:
    _section("Paper-trading portfolio")
    cap = _read_initial_capital()
    _reset_paper_trading(dry, cap)


def reset_features(dry: bool) -> None:
    _section("Computed feature Parquets  (data/features/)")
    _glob_rm(ROOT / "data" / "features", "*.parquet", dry)


def reset_processed(dry: bool) -> None:
    _section("Processed OHLCV Parquets  (data/processed/)")
    _glob_rm(ROOT / "data" / "processed", "*.parquet", dry)


def reset_raw(dry: bool) -> None:
    _section("Raw downloads  (data/raw/)")
    _glob_rm(ROOT / "data" / "raw", "*", dry)
    # .gitkeep survives because glob("*") matches files but we guard below
    gitkeep = ROOT / "data" / "raw" / ".gitkeep"
    if not gitkeep.exists() and not dry:
        gitkeep.touch()


def reset_fundamentals(dry: bool) -> None:
    _section("Fundamentals cache  (data/fundamentals/)")
    _glob_rm(ROOT / "data" / "fundamentals", "*.json", dry)


def reset_news(dry: bool) -> None:
    _section("News cache  (data/news/)")
    news_file = ROOT / "data" / "news" / "market_news.json"
    _rm(news_file, dry)


def reset_reports(dry: bool) -> None:
    _section("Daily reports  (data/reports/  +  reports/)")
    for reports_dir in [ROOT / "data" / "reports", ROOT / "reports"]:
        for ext in ("*.csv", "*.html"):
            _glob_rm(reports_dir, ext, dry)


def reset_logs(dry: bool) -> None:
    _section("Log files  (logs/)")
    _glob_rm(ROOT / "logs", "sepa_ai.log*", dry)


def reset_metadata(dry: bool) -> None:
    _section("Metadata cache  (data/metadata/)")
    _rm(ROOT / "data" / "metadata" / "symbol_info.csv", dry)


def reset_frontend_cache(dry: bool) -> None:
    _section("Next.js build cache  (frontend/.next/)")
    _rmdir(ROOT / "frontend" / ".next", dry)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="reset",
        description="Reset SEPA AI to a clean, fresh-installation state.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Scope flags (combinable; --all is the default when none are given):
  --all           Everything listed below
  --db            SQLite databases (schema wiped + re-created)
  --paper         Paper-trading portfolio + trades
  --features      Computed feature Parquets  (data/features/)
  --processed     Processed OHLCV Parquets   (data/processed/)
  --raw           Raw ticker downloads       (data/raw/)
  --fundamentals  Fundamentals JSON cache    (data/fundamentals/)
  --news          News JSON cache            (data/news/)
  --reports       CSV/HTML daily reports
  --logs          Rotating log files         (logs/)
  --metadata      Symbol-info CSV            (data/metadata/)
  --frontend      Next.js build cache        (frontend/.next/)

Examples:
  python scripts/reset.py --all --yes          # full reset, no confirmation
  python scripts/reset.py --dry-run --all      # preview every deletion
  python scripts/reset.py --db --paper --yes   # databases + paper trading only
  python scripts/reset.py --keep-downloaded    # reset everything EXCEPT processed/raw
        """,
    )
    # Behaviour
    p.add_argument("--dry-run",  action="store_true",
                   help="Print what would be deleted without touching anything")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Skip all confirmation prompts (non-interactive mode)")

    # Scope
    scope = p.add_argument_group("scope flags")
    scope.add_argument("--all",          action="store_true", help="Reset everything (default when no scope flag given)")
    scope.add_argument("--db",           action="store_true", help="SQLite databases")
    scope.add_argument("--paper",        action="store_true", help="Paper-trading portfolio + trades")
    scope.add_argument("--features",     action="store_true", help="Computed feature Parquets")
    scope.add_argument("--processed",    action="store_true", help="Processed OHLCV Parquets")
    scope.add_argument("--raw",          action="store_true", help="Raw ticker downloads")
    scope.add_argument("--fundamentals", action="store_true", help="Fundamentals JSON cache")
    scope.add_argument("--news",         action="store_true", help="News JSON cache")
    scope.add_argument("--reports",      action="store_true", help="CSV / HTML daily reports")
    scope.add_argument("--logs",         action="store_true", help="Rotating log files")
    scope.add_argument("--metadata",     action="store_true", help="Symbol-info CSV")
    scope.add_argument("--frontend",     action="store_true", help="Next.js build cache (frontend/.next/)")

    # Convenience inverse
    scope.add_argument("--keep-downloaded", action="store_true",
                       help="Shorthand for --all but SKIP --processed and --raw (keep fetched OHLCV)")

    return p.parse_args()


def _confirm(prompt: str) -> bool:
    try:
        ans = input(f"\n{_Y}{prompt}{_NC} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes")


def main() -> None:  # noqa: C901  (complexity fine for a top-level CLI)
    global _deleted, _skipped, _errors

    args = _parse_args()

    # If no scope flag given, default to --all
    any_scope = any([
        args.all, args.db, args.paper, args.features,
        args.processed, args.raw, args.fundamentals,
        args.news, args.reports, args.logs,
        args.metadata, args.frontend, args.keep_downloaded,
    ])
    if not any_scope:
        args.all = True

    # Expand --keep-downloaded = --all minus processed and raw
    if args.keep_downloaded:
        args.all = True
        args.processed = False   # will be excluded explicitly below
        args.raw = False

    # Determine active sections
    do_db           = args.all or args.db
    do_paper        = args.all or args.paper
    do_features     = args.all or args.features
    do_processed    = (args.all or args.processed) and not args.keep_downloaded
    do_raw          = (args.all or args.raw) and not args.keep_downloaded
    do_fundamentals = args.all or args.fundamentals
    do_news         = args.all or args.news
    do_reports      = args.all or args.reports
    do_logs         = args.all or args.logs
    do_metadata     = args.all or args.metadata
    do_frontend     = args.all or args.frontend

    # ── Header ────────────────────────────────────────────────────────────────
    print(f"\n{_B}{'━' * 60}")
    print(f"  SEPA AI — Project Reset")
    if args.dry_run:
        print(f"  {_Y}DRY RUN{_B} — nothing will be deleted")
    print(f"{'━' * 60}{_NC}")

    sections_active = [
        ("Databases (sepa_ai.db, minervini.db)",     do_db),
        ("Paper-trading portfolio + trades",          do_paper),
        ("Feature Parquets (data/features/)",         do_features),
        ("Processed OHLCV (data/processed/)",         do_processed),
        ("Raw downloads (data/raw/)",                 do_raw),
        ("Fundamentals cache (data/fundamentals/)",   do_fundamentals),
        ("News cache (data/news/)",                   do_news),
        ("Daily reports (data/reports/ + reports/)",  do_reports),
        ("Log files (logs/)",                         do_logs),
        ("Metadata CSV (data/metadata/)",             do_metadata),
        ("Next.js build cache (frontend/.next/)",     do_frontend),
    ]

    print(f"\n{_B}Scope:{_NC}")
    for label, active in sections_active:
        mark = f"{_G}✔{_NC}" if active else f"{_DIM}–{_NC}"
        print(f"  {mark}  {label}")

    if not args.dry_run and not args.yes:
        if not _confirm("⚠  This is IRREVERSIBLE. Proceed?"):
            print(f"\n{_Y}Aborted.{_NC}\n")
            sys.exit(0)

    # ── Execute ───────────────────────────────────────────────────────────────
    if do_db:           reset_databases(args.dry_run)
    if do_paper:        reset_paper_trading(args.dry_run)
    if do_features:     reset_features(args.dry_run)
    if do_processed:    reset_processed(args.dry_run)
    if do_raw:          reset_raw(args.dry_run)
    if do_fundamentals: reset_fundamentals(args.dry_run)
    if do_news:         reset_news(args.dry_run)
    if do_reports:      reset_reports(args.dry_run)
    if do_logs:         reset_logs(args.dry_run)
    if do_metadata:     reset_metadata(args.dry_run)
    if do_frontend:     reset_frontend_cache(args.dry_run)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{_B}{'━' * 60}{_NC}")
    if args.dry_run:
        print(f"  {_B}Dry-run complete.{_NC}  "
              f"{_deleted} file(s) would be deleted, "
              f"{_skipped} already absent.")
    else:
        status = f"{_G}Done.{_NC}" if _errors == 0 else f"{_R}Done with {_errors} error(s).{_NC}"
        print(f"  {status}  "
              f"{_G}{_deleted}{_NC} deleted, "
              f"{_DIM}{_skipped} skipped{_NC}"
              + (f", {_R}{_errors} error(s){_NC}" if _errors else ""))
        if not args.keep_downloaded:
            print(f"\n  {_Y}Next step:{_NC} run the pipeline or bootstrap to reload data.")
            print(f"  {_DIM}  make bootstrap    # re-download full OHLCV history")
            print(f"    make daily        # run today's screen{_NC}")
    print(f"{_B}{'━' * 60}{_NC}\n")

    sys.exit(1 if _errors else 0)


if __name__ == "__main__":
    main()
