"""
tests/unit/test_api_watchlist.py
---------------------------------
Unit tests for api/routers/watchlist.py.

Uses FastAPI TestClient with a fully-mocked SQLiteStore so no real database
is touched.  Auth is disabled for all tests (API_READ_KEY / API_ADMIN_KEY
unset in env).

Test inventory
--------------
1.  POST /watchlist/RELIANCE           → 200, symbol added
2.  POST /watchlist/RELIANCE (again)   → 200, already_exists=True (no duplicate)
3.  POST /watchlist/FAKE@!#$           → 400, invalid symbol
4.  DELETE /watchlist/RELIANCE         → 200, symbol removed
5.  DELETE /watchlist/MISSING          → 404
6.  POST /watchlist/bulk (3 symbols)   → added=3
7.  POST /watchlist/upload (CSV file)  → added count matches CSV symbols
8.  DELETE /watchlist (clear all)      → removed count returned
9.  GET  /watchlist                    → list sorted by score
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import watchlist as wl_module
from api.deps import get_db, get_config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_wl_row(symbol: str, last_score: float | None = None) -> dict:
    return {
        "id": 1,
        "symbol": symbol,
        "note": None,
        "added_at": "2025-01-01 00:00:00",
        "added_via": "api",
        "last_score": last_score,
        "last_quality": None,
        "last_run_at": None,
    }


@pytest.fixture()
def mock_db():
    """MagicMock that stands in for SQLiteStore."""
    db = MagicMock()
    db.get_watchlist.return_value = []
    return db


@pytest.fixture()
def client(mock_db):
    """TestClient with mocked DB and auth disabled."""
    app = FastAPI()
    app.include_router(wl_module.router)
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_config] = lambda: {}

    for var in ("API_READ_KEY", "API_ADMIN_KEY"):
        os.environ.pop(var, None)

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Test 1 – add new symbol
# ---------------------------------------------------------------------------

class TestAddSymbol:
    def test_add_reliance_returns_200(self, client, mock_db):
        """POST /watchlist/RELIANCE → 200 with symbol in response."""
        mock_db.get_watchlist.return_value = []  # not yet in db
        resp = client.post("/api/v1/watchlist/RELIANCE")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["symbol"] == "RELIANCE"
        assert body["data"]["already_exists"] is False
        mock_db.add_symbol.assert_called_once_with("RELIANCE", note=None, added_via="api")

    # -----------------------------------------------------------------------
    # Test 2 – duplicate add
    # -----------------------------------------------------------------------
    def test_add_reliance_again_returns_200_with_flag(self, client, mock_db):
        """POST /watchlist/RELIANCE when already present → 200, already_exists=True."""
        mock_db.get_watchlist.return_value = [_make_wl_row("RELIANCE")]
        resp = client.post("/api/v1/watchlist/RELIANCE")
        assert resp.status_code == 200
        assert resp.json()["data"]["already_exists"] is True
        # add_symbol is still called (idempotent upsert in the store)
        mock_db.add_symbol.assert_called_once()

    # -----------------------------------------------------------------------
    # Test 3 – invalid symbol
    # -----------------------------------------------------------------------
    def test_invalid_symbol_returns_400(self, client, mock_db):
        """POST /watchlist/FAKE@!#$ → 400 (special characters)."""
        resp = client.post("/api/v1/watchlist/FAKE%40%21%23%24")
        assert resp.status_code == 400
        mock_db.add_symbol.assert_not_called()


# ---------------------------------------------------------------------------
# Tests 4-5 – remove symbol
# ---------------------------------------------------------------------------

class TestRemoveSymbol:
    def test_delete_present_symbol_returns_200(self, client, mock_db):
        """DELETE /watchlist/RELIANCE → 200, removed=True."""
        mock_db.get_watchlist.return_value = [_make_wl_row("RELIANCE")]
        resp = client.delete("/api/v1/watchlist/RELIANCE")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["removed"] is True
        mock_db.remove_symbol.assert_called_once_with("RELIANCE")

    def test_delete_missing_symbol_returns_404(self, client, mock_db):
        """DELETE /watchlist/MISSING → 404."""
        mock_db.get_watchlist.return_value = []  # empty watchlist
        resp = client.delete("/api/v1/watchlist/MISSING")
        assert resp.status_code == 404
        mock_db.remove_symbol.assert_not_called()


# ---------------------------------------------------------------------------
# Test 6 – bulk add
# ---------------------------------------------------------------------------

class TestBulkAdd:
    def test_bulk_add_three_symbols(self, client, mock_db):
        """POST /watchlist/bulk with 3 valid symbols → added=3."""
        mock_db.get_watchlist.return_value = []  # nothing pre-existing
        resp = client.post(
            "/api/v1/watchlist/bulk",
            json={"symbols": ["RELIANCE", "TCS", "INFY"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["added"] == 3
        assert body["data"]["already_exists"] == 0
        assert body["data"]["invalid"] == []
        mock_db.bulk_add.assert_called_once()

    def test_bulk_add_mixed(self, client, mock_db):
        """Bulk add: 1 new, 1 existing, 1 invalid."""
        mock_db.get_watchlist.return_value = [_make_wl_row("TCS")]
        resp = client.post(
            "/api/v1/watchlist/bulk",
            json={"symbols": ["RELIANCE", "TCS", "BAD SYM!"]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["added"] == 1
        assert data["already_exists"] == 1
        assert "BAD SYM!" in data["invalid"]

    def test_bulk_add_invalid_body_returns_400(self, client, mock_db):
        """Body with 'symbols' not a list → 400."""
        resp = client.post(
            "/api/v1/watchlist/bulk",
            json={"symbols": "RELIANCE"},  # string, not list
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Test 7 – upload CSV
# ---------------------------------------------------------------------------

class TestUploadWatchlist:
    def test_upload_csv_adds_symbols(self, client, mock_db):
        """POST /watchlist/upload with a valid CSV → added count matches rows."""
        mock_db.get_watchlist.return_value = []

        # 3-symbol CSV matching the fixture format
        csv_content = b"symbol\nRELIANCE\nTCS\nINFY\n"
        resp = client.post(
            "/api/v1/watchlist/upload",
            files={"file": ("watchlist.csv", io.BytesIO(csv_content), "text/csv")},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["added"] == 3
        assert data["skipped"] == 0
        assert data["invalid"] == []

    def test_upload_oversized_file_returns_400(self, client, mock_db):
        """File larger than 1 MB → 400."""
        big_content = b"symbol\n" + b"RELIANCE\n" * 200_000  # > 1 MB
        resp = client.post(
            "/api/v1/watchlist/upload",
            files={"file": ("big.csv", io.BytesIO(big_content), "text/csv")},
        )
        assert resp.status_code == 400

    def test_upload_unsupported_format_returns_400(self, client, mock_db):
        """Unsupported file extension → 400."""
        resp = client.post(
            "/api/v1/watchlist/upload",
            files={"file": ("watchlist.pdf", io.BytesIO(b"data"), "application/pdf")},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Test 8 – clear watchlist
# ---------------------------------------------------------------------------

class TestClearWatchlist:
    def test_clear_returns_removed_count(self, client, mock_db):
        """DELETE /watchlist → 200 with count of removed symbols."""
        mock_db.get_watchlist.return_value = [
            _make_wl_row("RELIANCE"),
            _make_wl_row("TCS"),
            _make_wl_row("INFY"),
        ]
        resp = client.delete("/api/v1/watchlist")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["removed"] == 3
        mock_db.clear_watchlist.assert_called_once()

    def test_clear_empty_watchlist_returns_zero(self, client, mock_db):
        """Clearing an already-empty watchlist returns removed=0."""
        mock_db.get_watchlist.return_value = []
        resp = client.delete("/api/v1/watchlist")
        assert resp.status_code == 200
        assert resp.json()["data"]["removed"] == 0


# ---------------------------------------------------------------------------
# Test 9 – get watchlist sorted by score
# ---------------------------------------------------------------------------

class TestGetWatchlist:
    def test_get_returns_list(self, client, mock_db):
        """GET /watchlist → 200 with list of watchlist rows."""
        mock_db.get_watchlist.return_value = [
            _make_wl_row("RELIANCE", last_score=85),
            _make_wl_row("TCS", last_score=92),
            _make_wl_row("INFY", last_score=70),
        ]
        resp = client.get("/api/v1/watchlist")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert isinstance(data, list)
        assert len(data) == 3

    def test_get_sorted_by_score_descending(self, client, mock_db):
        """Default sort=score → highest last_score first."""
        mock_db.get_watchlist.return_value = [
            _make_wl_row("LOW", last_score=40),
            _make_wl_row("HIGH", last_score=95),
            _make_wl_row("MID", last_score=70),
        ]
        resp = client.get("/api/v1/watchlist?sort=score")
        assert resp.status_code == 200
        symbols = [r["symbol"] for r in resp.json()["data"]]
        assert symbols == ["HIGH", "MID", "LOW"]

    def test_get_limit_applied(self, client, mock_db):
        """limit=2 returns at most 2 symbols."""
        mock_db.get_watchlist.return_value = [
            _make_wl_row("A", last_score=90),
            _make_wl_row("B", last_score=80),
            _make_wl_row("C", last_score=70),
        ]
        resp = client.get("/api/v1/watchlist?limit=2")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2

    def test_get_meta_contains_count(self, client, mock_db):
        """Response meta includes count matching returned list length."""
        mock_db.get_watchlist.return_value = [_make_wl_row("RELIANCE")]
        resp = client.get("/api/v1/watchlist")
        body = resp.json()
        assert body["meta"]["count"] == len(body["data"])
