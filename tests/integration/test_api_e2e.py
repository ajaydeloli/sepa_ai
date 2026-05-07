"""
tests/integration/test_api_e2e.py
----------------------------------
Full end-to-end integration test for the SEPA AI FastAPI application.

Strategy
--------
* A real (temporary) SQLite DB is created in tmp_path with fixture data so no
  network calls or external services are required.
* The FastAPI TestClient is pointed at api.main.app with the ``get_db``
  dependency overridden to use the temp DB.
* Auth is disabled (API_READ_KEY / API_ADMIN_KEY env vars unset).

Flow under test
---------------
  1. health      → 200, status="ok"
  2. stocks/top  → 200, APIResponse envelope, data is a list
  3. stocks/{symbol} → 200 for known symbol, 404 for unknown
  4. watchlist GET (empty) → 200, data=[]
  5. watchlist POST /{symbol} (add)    → 200, symbol added
  6. watchlist GET (non-empty)         → 200, symbol visible
  7. watchlist DELETE /{symbol}        → 200, removed=True
  8. watchlist DELETE /{symbol} again  → 404
  9. watchlist/bulk add                → 200, added count correct
 10. portfolio GET (no file)           → 404 with APIResponse envelope
 11. Auth gate: read key set, no header → 401
 12. Auth gate: admin key required for POST /watchlist/{symbol} → 403 with read key
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixture DB builder
# ---------------------------------------------------------------------------

TODAY = date.today().isoformat()

_FIXTURE_SYMBOLS = [
    ("RELIANCE", 90, "A+", 2, 1, 1, 0, 92, 2500.0, 2350.0, 6.0),
    ("TCS",      80, "A",  2, 1, 1, 0, 85, 3800.0, 3610.0, 5.0),
    ("INFY",     70, "B",  2, 1, 0, 0, 78, 1500.0, 1425.0, 5.0),
]


def _build_fixture_db(db_path: Path) -> None:
    """Create a minimal sepa_ai SQLite DB pre-populated with fixture rows."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY,
            symbol TEXT NOT NULL UNIQUE,
            note TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            added_via TEXT NOT NULL DEFAULT 'cli',
            last_score REAL,
            last_quality TEXT,
            last_run_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS run_history (
            id INTEGER PRIMARY KEY,
            run_date DATE NOT NULL,
            run_mode TEXT NOT NULL,
            git_sha TEXT,
            config_hash TEXT,
            universe_size INTEGER,
            passed_stage2 INTEGER,
            passed_tt INTEGER,
            vcp_qualified INTEGER,
            a_plus_count INTEGER,
            a_count INTEGER,
            duration_sec REAL,
            status TEXT NOT NULL,
            error_msg TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS screen_results (
            id INTEGER PRIMARY KEY,
            run_date DATE NOT NULL,
            symbol TEXT NOT NULL,
            stage INTEGER,
            score REAL,
            setup_quality TEXT,
            trend_template_pass INTEGER,
            vcp_qualified INTEGER,
            breakout_triggered INTEGER,
            rs_rating INTEGER,
            entry_price REAL,
            stop_loss REAL,
            risk_pct REAL,
            result_json TEXT
        );
    """)

    # run_history row
    conn.execute(
        """INSERT INTO run_history
           (run_date, run_mode, universe_size, a_plus_count, a_count, status, created_at)
           VALUES (?, 'daily', 500, 1, 3, 'success', '2025-01-01 08:00:00')""",
        (TODAY,),
    )

    # screen_results rows
    for (sym, score, quality, stage, tt, vcp, brk, rs, entry, sl, risk) in _FIXTURE_SYMBOLS:
        payload = {
            "symbol": sym,
            "run_date": TODAY,
            "score": score,
            "setup_quality": quality,
            "stage": stage,
            "stage_label": "Stage 2 Uptrend",
            "stage_confidence": 75,
            "trend_template_pass": bool(tt),
            "conditions_met": 7,
            "vcp_qualified": bool(vcp),
            "breakout_triggered": bool(brk),
            "rs_rating": rs,
            "entry_price": entry,
            "stop_loss": sl,
            "risk_pct": risk,
            "target_price": entry * 1.10,
            "reward_risk_ratio": 1.67,
            "news_score": 0.5,
            "fundamental_pass": True,
        }
        conn.execute(
            """INSERT INTO screen_results
               (run_date, symbol, stage, score, setup_quality, trend_template_pass,
                vcp_qualified, breakout_triggered, rs_rating, entry_price, stop_loss,
                risk_pct, result_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (TODAY, sym, stage, score, quality, tt, vcp, brk, rs, entry, sl, risk,
             json.dumps(payload)),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path: Path):
    """Build a temp fixture DB and return its Path."""
    db_path = tmp_path / "test_e2e.db"
    _build_fixture_db(db_path)
    return db_path


@pytest.fixture()
def e2e_client(tmp_db: Path):
    """
    TestClient against api.main.app wired to the temp fixture DB.
    Auth disabled; dependency override for get_db.
    """
    from storage.sqlite_store import SQLiteStore
    from api.deps import get_db
    from api.main import app

    # Clear LRU cache so our override is picked up cleanly
    get_db.cache_clear()

    real_db = SQLiteStore(tmp_db)

    for var in ("API_READ_KEY", "API_ADMIN_KEY"):
        os.environ.pop(var, None)

    app.dependency_overrides[get_db] = lambda: real_db

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client

    app.dependency_overrides.clear()
    get_db.cache_clear()


# ---------------------------------------------------------------------------
# Helper: assert APIResponse envelope
# ---------------------------------------------------------------------------

def _assert_envelope(body: dict, success: bool = True) -> None:
    """Assert the response body uses the APIResponse envelope shape."""
    assert "success" in body, f"Missing 'success' key in: {body}"
    assert body["success"] is success
    assert "data" in body, f"Missing 'data' key in: {body}"
    if not success:
        assert "error" in body and body["error"], f"Missing non-empty 'error' in: {body}"
        assert "detail" not in body, f"Raw FastAPI 'detail' key leaked: {body}"


# ---------------------------------------------------------------------------
# Full flow test
# ---------------------------------------------------------------------------

def test_full_api_flow(e2e_client: TestClient) -> None:  # noqa: PLR0915
    """
    End-to-end test covering every major API area in realistic order.

    Step 1: Health check
    Step 2: Stocks/top — returns fixture data
    Step 3: Single symbol — found + not found
    Step 4: Watchlist — empty list
    Step 5: Watchlist — add symbol
    Step 6: Watchlist — verify symbol visible
    Step 7: Watchlist — remove symbol
    Step 8: Watchlist — remove again → 404
    Step 9: Watchlist/bulk — add multiple
    Step 10: Portfolio — no file → 404 envelope
    """

    # ------------------------------------------------------------------
    # Step 1: Health
    # ------------------------------------------------------------------
    resp = e2e_client.get("/api/v1/health")
    assert resp.status_code == 200
    health = resp.json()
    assert health["status"] == "ok"
    assert health["version"] == "1.0.0"
    assert health["last_run"] == TODAY
    assert health["last_run_status"] == "success"

    # ------------------------------------------------------------------
    # Step 2: Stocks/top — fixture has 3 rows
    # ------------------------------------------------------------------
    resp = e2e_client.get("/api/v1/stocks/top")
    assert resp.status_code == 200
    body = resp.json()
    _assert_envelope(body, success=True)
    data = body["data"]
    assert isinstance(data, list)
    assert len(data) >= 1
    symbols = [r["symbol"] for r in data]
    assert "RELIANCE" in symbols

    # Verify each item carries the expected fields
    first = data[0]
    for field in ("symbol", "run_date", "score", "setup_quality", "stage"):
        assert field in first, f"Missing field '{field}' in stock result"

    # ------------------------------------------------------------------
    # Step 3a: Single symbol — known
    # ------------------------------------------------------------------
    resp = e2e_client.get("/api/v1/stocks/RELIANCE")
    assert resp.status_code == 200
    body = resp.json()
    _assert_envelope(body, success=True)
    assert body["data"]["symbol"] == "RELIANCE"
    assert body["data"]["setup_quality"] == "A+"

    # ------------------------------------------------------------------
    # Step 3b: Single symbol — not in DB → 404 envelope
    # ------------------------------------------------------------------
    resp = e2e_client.get("/api/v1/stocks/FAKEXYZ")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), success=False)

    # ------------------------------------------------------------------
    # Step 4: Watchlist — empty at start
    # ------------------------------------------------------------------
    resp = e2e_client.get("/api/v1/watchlist")
    assert resp.status_code == 200
    body = resp.json()
    _assert_envelope(body, success=True)
    assert body["data"] == []

    # ------------------------------------------------------------------
    # Step 5: Watchlist — add a symbol
    # ------------------------------------------------------------------
    resp = e2e_client.post("/api/v1/watchlist/RELIANCE")
    assert resp.status_code == 200
    body = resp.json()
    _assert_envelope(body, success=True)
    assert body["data"]["symbol"] == "RELIANCE"
    assert body["data"]["already_exists"] is False

    # Adding again returns already_exists=True (still 200)
    resp = e2e_client.post("/api/v1/watchlist/RELIANCE")
    assert resp.status_code == 200
    assert resp.json()["data"]["already_exists"] is True

    # ------------------------------------------------------------------
    # Step 6: Watchlist — verify symbol is visible in list
    # ------------------------------------------------------------------
    resp = e2e_client.get("/api/v1/watchlist")
    assert resp.status_code == 200
    body = resp.json()
    _assert_envelope(body, success=True)
    wl_symbols = [r["symbol"] for r in body["data"]]
    assert "RELIANCE" in wl_symbols

    # ------------------------------------------------------------------
    # Step 7: Watchlist — remove symbol
    # ------------------------------------------------------------------
    resp = e2e_client.delete("/api/v1/watchlist/RELIANCE")
    assert resp.status_code == 200
    body = resp.json()
    _assert_envelope(body, success=True)
    assert body["data"]["removed"] is True

    # ------------------------------------------------------------------
    # Step 8: Remove again → 404 with APIResponse envelope
    # ------------------------------------------------------------------
    resp = e2e_client.delete("/api/v1/watchlist/RELIANCE")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), success=False)

    # ------------------------------------------------------------------
    # Step 9: Watchlist/bulk — add multiple symbols
    # ------------------------------------------------------------------
    resp = e2e_client.post(
        "/api/v1/watchlist/bulk",
        json={"symbols": ["TCS", "INFY", "!!INVALID!!"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_envelope(body, success=True)
    assert body["data"]["added"] == 2
    assert body["data"]["already_exists"] == 0
    assert "!!INVALID!!" in body["data"]["invalid"]

    # ------------------------------------------------------------------
    # Step 10: Portfolio — no portfolio.json → 404 envelope
    # ------------------------------------------------------------------
    resp = e2e_client.get("/api/v1/portfolio")
    assert resp.status_code == 404
    _assert_envelope(resp.json(), success=False)


# ---------------------------------------------------------------------------
# Auth gate tests
# ---------------------------------------------------------------------------


def test_auth_gate_read_key_required(tmp_db: Path) -> None:
    """When API_READ_KEY is set, GET /stocks/top without header → 401."""
    from storage.sqlite_store import SQLiteStore
    from api.deps import get_db
    from api.main import app

    get_db.cache_clear()
    real_db = SQLiteStore(tmp_db)
    app.dependency_overrides[get_db] = lambda: real_db

    os.environ["API_READ_KEY"] = "test-read-secret"
    os.environ["API_ADMIN_KEY"] = "test-admin-secret"
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/api/v1/stocks/top")
        assert resp.status_code == 401
    finally:
        os.environ.pop("API_READ_KEY", None)
        os.environ.pop("API_ADMIN_KEY", None)
        app.dependency_overrides.clear()
        get_db.cache_clear()


def test_auth_gate_read_key_accepted(tmp_db: Path) -> None:
    """When API_READ_KEY is set, correct key in header → 200."""
    from storage.sqlite_store import SQLiteStore
    from api.deps import get_db
    from api.main import app

    get_db.cache_clear()
    real_db = SQLiteStore(tmp_db)
    app.dependency_overrides[get_db] = lambda: real_db

    os.environ["API_READ_KEY"] = "test-read-secret"
    os.environ["API_ADMIN_KEY"] = "test-admin-secret"
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/api/v1/stocks/top",
                headers={"X-API-Key": "test-read-secret"},
            )
        assert resp.status_code == 200
    finally:
        os.environ.pop("API_READ_KEY", None)
        os.environ.pop("API_ADMIN_KEY", None)
        app.dependency_overrides.clear()
        get_db.cache_clear()


def test_auth_gate_read_key_rejected_on_admin_endpoint(tmp_db: Path) -> None:
    """Read key presented to POST /watchlist/{symbol} → 403 Forbidden."""
    from storage.sqlite_store import SQLiteStore
    from api.deps import get_db
    from api.main import app

    get_db.cache_clear()
    real_db = SQLiteStore(tmp_db)
    app.dependency_overrides[get_db] = lambda: real_db

    os.environ["API_READ_KEY"] = "test-read-secret"
    os.environ["API_ADMIN_KEY"] = "test-admin-secret"
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/api/v1/watchlist/TCS",
                headers={"X-API-Key": "test-read-secret"},
            )
        assert resp.status_code == 403
    finally:
        os.environ.pop("API_READ_KEY", None)
        os.environ.pop("API_ADMIN_KEY", None)
        app.dependency_overrides.clear()
        get_db.cache_clear()
