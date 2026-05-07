"""
tests/unit/test_api_stocks.py
-----------------------------
Unit tests for api/routers/health.py and api/routers/stocks.py.

Uses FastAPI TestClient with a mocked SQLiteStore so no real DB is needed.
Auth is disabled (env vars unset) for all tests except the 401 test.
"""

from __future__ import annotations

import json
import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import health as health_router_module
from api.routers import stocks as stocks_router_module
from api.deps import get_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TODAY = date.today().isoformat()


def _make_result_row(
    symbol: str = "RELIANCE",
    run_date: str = TODAY,
    score: int = 85,
    setup_quality: str = "A+",
    stage: int = 2,
    trend_template_pass: int = 1,
    vcp_qualified: int = 1,
    breakout_triggered: int = 0,
    rs_rating: int = 90,
    entry_price: float = 2500.0,
    stop_loss: float = 2350.0,
    risk_pct: float = 6.0,
) -> dict:
    """Build a minimal screen_results row dict as SQLite would return it."""
    payload = {
        "symbol": symbol,
        "run_date": run_date,
        "score": score,
        "setup_quality": setup_quality,
        "stage": stage,
        "stage_label": "Stage 2 Uptrend",
        "stage_confidence": 80,
        "trend_template_pass": trend_template_pass,
        "conditions_met": 8,
        "vcp_qualified": vcp_qualified,
        "breakout_triggered": breakout_triggered,
        "rs_rating": rs_rating,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "risk_pct": risk_pct,
        "target_price": 2750.0,
        "reward_risk_ratio": 1.67,
        "news_score": 0.5,
        "fundamental_pass": True,
    }
    return {**payload, "result_json": json.dumps(payload)}


@pytest.fixture()
def mock_db():
    """Return a MagicMock that stands in for SQLiteStore."""
    db = MagicMock()

    # Default: run_history returns one row
    run_row = MagicMock()
    run_row.__getitem__ = lambda self, key: {
        "run_date": TODAY,
        "status": "success",
        "created_at": "2025-01-01 08:00:00",
        "universe_size": 500,
        "a_plus_count": 5,
        "a_count": 12,
    }[key]

    # _connect() → returns a connection mock with .execute().fetchone()
    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    conn.execute.return_value.fetchone.return_value = run_row
    conn.execute.return_value.fetchall.return_value = []
    db._connect.return_value = conn

    # get_results returns a list of rows
    db.get_results.return_value = [
        _make_result_row("RELIANCE", setup_quality="A+", score=90),
        _make_result_row("TCS", setup_quality="A", score=80),
        _make_result_row("INFY", setup_quality="B", score=70),
    ]

    # get_result: return RELIANCE row or None for unknown symbols
    def _get_result(symbol, run_date):
        if symbol == "RELIANCE":
            return _make_result_row("RELIANCE")
        return None

    db.get_result.side_effect = _get_result
    return db


@pytest.fixture()
def client(mock_db):
    """TestClient with mocked DB and auth disabled (no env keys set)."""
    app = FastAPI()
    app.include_router(health_router_module.router)
    app.include_router(stocks_router_module.router)

    # Override the get_db dependency to use the mock
    app.dependency_overrides[get_db] = lambda: mock_db

    # Ensure auth is disabled
    for var in ("API_READ_KEY", "API_ADMIN_KEY"):
        os.environ.pop(var, None)

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Test 1: GET /api/v1/health → 200 with status="ok"."""

    def test_health_returns_ok(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["version"] == "1.0.0"
        assert "last_run" in body
        assert "last_run_status" in body

    def test_health_no_run_history(self, mock_db, client):
        """When run_history is empty, last_run and last_run_status are None."""
        # Return None from fetchone to simulate empty table
        mock_db._connect.return_value.execute.return_value.fetchone.return_value = None
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["last_run"] is None


class TestGetTopStocks:
    """Tests 2–4: GET /api/v1/stocks/top."""

    def test_top_returns_list(self, client):
        """Test 2: returns 200 with a list of StockResultSchema."""
        resp = client.get("/api/v1/stocks/top")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert isinstance(body["data"], list)
        assert len(body["data"]) > 0
        assert body["data"][0]["symbol"] == "RELIANCE"

    def test_top_quality_filter(self, client):
        """Test 3: quality=A%2B filters to only A+ results."""
        resp = client.get("/api/v1/stocks/top?quality=A%2B")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert all(r["setup_quality"] == "A+" for r in data)
        assert any(r["symbol"] == "RELIANCE" for r in data)

    def test_top_limit(self, client):
        """Test 4: limit=1 returns at most 1 result."""
        resp = client.get("/api/v1/stocks/top?limit=1")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) <= 1


class TestGetSingleStock:
    """Tests 5–6: GET /api/v1/stocks/{symbol}."""

    def test_existing_symbol_returns_200(self, client):
        """Test 5: known symbol → 200 with single StockResultSchema."""
        resp = client.get("/api/v1/stocks/RELIANCE")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["symbol"] == "RELIANCE"

    def test_missing_symbol_returns_404(self, client):
        """Test 6: unknown symbol → 404."""
        resp = client.get("/api/v1/stocks/FAKE999")
        assert resp.status_code == 404


class TestGetStockHistory:
    """Test 7: GET /api/v1/stocks/{symbol}/history."""

    def test_history_returns_list_of_dicts(self, mock_db, client):
        """Returns StockHistorySchema with N history dicts."""
        # Simulate 30 history rows from _connect().execute().fetchall()
        history_rows = []
        for i in range(30):
            row = MagicMock()
            row.keys.return_value = ["run_date", "score", "quality", "stage"]
            row.__iter__ = lambda s: iter([
                ("run_date", f"2025-01-{i+1:02d}"),
                ("score", 70 + i),
                ("quality", "A"),
                ("stage", 2),
            ])
            # Make dict(row) work — use a plain dict instead
            history_rows.append({
                "run_date": f"2025-01-{i+1:02d}",
                "score": 70 + i,
                "quality": "A",
                "stage": 2,
            })

        # Patch the conn so fetchall returns our history
        conn = mock_db._connect.return_value
        conn.execute.return_value.fetchall.return_value = [
            MagicMock(**{"__iter__": lambda s: iter(r.items()), "keys": lambda: r.keys()})
            for r in history_rows
        ]
        # Use sqlite3.Row-like objects — simplest: patch dict() call by making rows dict-able
        # Override by monkeypatching _connect to return rows directly from dict
        import sqlite3
        real_rows = []
        for r in history_rows:
            m = MagicMock(spec=sqlite3.Row)
            m.__iter__ = lambda s, _r=r: iter(_r.items())
            m.keys = lambda _r=r: _r.keys()
            m.__getitem__ = lambda s, k, _r=r: _r[k]
            real_rows.append(m)
        conn.execute.return_value.fetchall.return_value = real_rows

        resp = client.get("/api/v1/stocks/RELIANCE/history?days=30")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["symbol"] == "RELIANCE"
        assert isinstance(body["data"]["history"], list)


class TestAuthRequired:
    """Test 8: missing API key → 401 when auth is enabled."""

    def test_missing_key_returns_401(self, mock_db):
        """When API_READ_KEY is set, omitting X-API-Key header gives 401."""
        os.environ["API_READ_KEY"] = "secret-key"
        os.environ["API_ADMIN_KEY"] = "admin-key"
        try:
            app = FastAPI()
            app.include_router(stocks_router_module.router)
            app.dependency_overrides[get_db] = lambda: mock_db

            with TestClient(app, raise_server_exceptions=True) as c:
                resp = c.get("/api/v1/stocks/top")
            assert resp.status_code == 401
        finally:
            os.environ.pop("API_READ_KEY", None)
            os.environ.pop("API_ADMIN_KEY", None)
