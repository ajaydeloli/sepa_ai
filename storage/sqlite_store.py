"""
storage/sqlite_store.py
-----------------------
SQLite persistence layer for the SEPA AI screening system.

All database access is handled through the :class:`SQLiteStore` class,
which creates the required schema on first use and exposes typed methods
for every table.  The underlying ``sqlite3`` standard-library module is
used directly — no ORM dependency.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS watchlist (
    id         INTEGER PRIMARY KEY,
    symbol     TEXT NOT NULL UNIQUE,
    note       TEXT,
    added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    added_via  TEXT NOT NULL DEFAULT 'cli',
    last_score REAL,
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


# ---------------------------------------------------------------------------
# SQLiteStore
# ---------------------------------------------------------------------------


class SQLiteStore:
    """Thin persistence layer over an SQLite database.

    Parameters
    ----------
    db_path:
        Path to the ``.db`` file.  Created (along with any missing parent
        directories) on first use.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------

    def add_symbol(
        self,
        symbol: str,
        note: str | None = None,
        added_via: str = "cli",
    ) -> None:
        """Insert *symbol* into the watchlist (or update note / added_via)."""
        sql = """
            INSERT INTO watchlist (symbol, note, added_via)
            VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                note      = excluded.note,
                added_via = excluded.added_via
        """
        with self._connect() as conn:
            conn.execute(sql, (symbol.upper(), note, added_via))

    def remove_symbol(self, symbol: str) -> None:
        """Delete *symbol* from the watchlist (no-op if absent)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))

    def get_watchlist(self) -> list[dict[str, Any]]:
        """Return all watchlist rows as a list of dicts."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM watchlist ORDER BY added_at DESC"
            ).fetchall()
        return self._rows_to_dicts(rows)

    def clear_watchlist(self) -> None:
        """Remove every row from the watchlist table."""
        with self._connect() as conn:
            conn.execute("DELETE FROM watchlist")

    def bulk_add(self, symbols: list[str], added_via: str = "cli") -> None:
        """Add multiple symbols; silently skips duplicates."""
        sql = """
            INSERT INTO watchlist (symbol, added_via)
            VALUES (?, ?)
            ON CONFLICT(symbol) DO NOTHING
        """
        with self._connect() as conn:
            conn.executemany(sql, [(s.upper(), added_via) for s in symbols])

    # ------------------------------------------------------------------
    # Screen results
    # ------------------------------------------------------------------

    def save_result(self, run_date: date | str, result_dict: dict[str, Any]) -> None:
        """Upsert a single screening result row."""
        sql = """
            INSERT INTO screen_results (
                run_date, symbol, stage, score, setup_quality,
                trend_template_pass, vcp_qualified, breakout_triggered,
                rs_rating, entry_price, stop_loss, risk_pct, result_json
            ) VALUES (
                :run_date, :symbol, :stage, :score, :setup_quality,
                :trend_template_pass, :vcp_qualified, :breakout_triggered,
                :rs_rating, :entry_price, :stop_loss, :risk_pct, :result_json
            )
            ON CONFLICT(run_date, symbol) DO UPDATE SET
                stage               = excluded.stage,
                score               = excluded.score,
                setup_quality       = excluded.setup_quality,
                trend_template_pass = excluded.trend_template_pass,
                vcp_qualified       = excluded.vcp_qualified,
                breakout_triggered  = excluded.breakout_triggered,
                rs_rating           = excluded.rs_rating,
                entry_price         = excluded.entry_price,
                stop_loss           = excluded.stop_loss,
                risk_pct            = excluded.risk_pct,
                result_json         = excluded.result_json
        """
        params = {
            "run_date": str(run_date),
            "symbol": result_dict.get("symbol", "").upper(),
            "stage": result_dict.get("stage"),
            "score": result_dict.get("score"),
            "setup_quality": result_dict.get("setup_quality"),
            "trend_template_pass": int(bool(result_dict.get("trend_template_pass"))),
            "vcp_qualified": int(bool(result_dict.get("vcp_qualified"))),
            "breakout_triggered": int(bool(result_dict.get("breakout_triggered"))),
            "rs_rating": result_dict.get("rs_rating"),
            "entry_price": result_dict.get("entry_price"),
            "stop_loss": result_dict.get("stop_loss"),
            "risk_pct": result_dict.get("risk_pct"),
            "result_json": json.dumps(result_dict),
        }
        with self._connect() as conn:
            conn.execute(sql, params)

    def get_results(self, run_date: date | str) -> list[dict[str, Any]]:
        """Return all screening results for *run_date*."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM screen_results WHERE run_date = ? ORDER BY score DESC",
                (str(run_date),),
            ).fetchall()
        return self._rows_to_dicts(rows)

    def get_result(
        self, symbol: str, run_date: date | str
    ) -> dict[str, Any] | None:
        """Return a single result for *(symbol, run_date)*, or ``None``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM screen_results WHERE symbol = ? AND run_date = ?",
                (symbol.upper(), str(run_date)),
            ).fetchone()
        return self._row_to_dict(row)

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def get_last_alert(self, symbol: str) -> dict[str, Any] | None:
        """Return the most recent alert for *symbol*, or ``None``."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM alerts
                WHERE symbol = ?
                ORDER BY alerted_date DESC, created_at DESC
                LIMIT 1
                """,
                (symbol.upper(),),
            ).fetchone()
        return self._row_to_dict(row)

    def save_alert(
        self,
        symbol: str,
        alerted_date: date | str,
        score: float | None,
        quality: str | None,
        breakout_triggered: bool = False,
        channel: str | None = None,
    ) -> None:
        """Insert a new alert row."""
        sql = """
            INSERT INTO alerts
                (symbol, alerted_date, score, quality, breakout_triggered, channel)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        with self._connect() as conn:
            conn.execute(
                sql,
                (
                    symbol.upper(),
                    str(alerted_date),
                    score,
                    quality,
                    int(breakout_triggered),
                    channel,
                ),
            )

    # ------------------------------------------------------------------
    # Run history
    # ------------------------------------------------------------------

    def save_run(self, run_dict: dict[str, Any]) -> None:
        """Insert a run-history row."""
        sql = """
            INSERT INTO run_history (
                run_date, run_mode, git_sha, config_hash,
                universe_size, passed_stage2, passed_tt,
                vcp_qualified, a_plus_count, a_count,
                duration_sec, status, error_msg
            ) VALUES (
                :run_date, :run_mode, :git_sha, :config_hash,
                :universe_size, :passed_stage2, :passed_tt,
                :vcp_qualified, :a_plus_count, :a_count,
                :duration_sec, :status, :error_msg
            )
        """
        params = {
            "run_date": str(run_dict.get("run_date", "")),
            "run_mode": run_dict.get("run_mode", "manual"),
            "git_sha": run_dict.get("git_sha"),
            "config_hash": run_dict.get("config_hash"),
            "universe_size": run_dict.get("universe_size"),
            "passed_stage2": run_dict.get("passed_stage2"),
            "passed_tt": run_dict.get("passed_tt"),
            "vcp_qualified": run_dict.get("vcp_qualified"),
            "a_plus_count": run_dict.get("a_plus_count"),
            "a_count": run_dict.get("a_count"),
            "duration_sec": run_dict.get("duration_sec"),
            "status": run_dict.get("status", "unknown"),
            "error_msg": run_dict.get("error_msg"),
        }
        with self._connect() as conn:
            conn.execute(sql, params)

    def get_last_run_date(self) -> date | None:
        """Return the most recent ``run_date`` from run_history, or ``None``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT run_date FROM run_history ORDER BY run_date DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        raw = row["run_date"]
        if isinstance(raw, date):
            return raw
        try:
            return datetime.strptime(str(raw), "%Y-%m-%d").date()
        except ValueError:
            return None
