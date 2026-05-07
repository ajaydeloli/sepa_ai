"""
tests/unit/test_api_portfolio.py
---------------------------------
Unit tests for api/routers/portfolio.py.

Uses FastAPI TestClient.  The portfolio JSON file is mocked via
unittest.mock.patch so no real filesystem I/O occurs.  Auth is
disabled for all tests.

Test inventory
--------------
10. GET /portfolio         (valid portfolio.json)   → PortfolioSummarySchema
11. GET /portfolio         (missing file)           → 404
12. GET /portfolio/trades?status=closed             → only closed trades
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import portfolio as portfolio_module
from api.deps import get_db


# ---------------------------------------------------------------------------
# Sample portfolio data helpers
# ---------------------------------------------------------------------------

_TODAY = date.today().isoformat()
_YESTERDAY = "2025-04-01"


def _make_portfolio_json(
    cash: float = 80_000.0,
    initial_capital: float = 100_000.0,
    positions: dict | None = None,
    closed_trades: list | None = None,
) -> str:
    """Build a minimal portfolio.json payload."""
    return json.dumps({
        "initial_capital": initial_capital,
        "cash": cash,
        "positions": positions or {},
        "closed_trades": closed_trades or [],
        "equity_curve": [],
    })


def _make_closed_trade(
    symbol: str = "RELIANCE",
    entry_date: str = _YESTERDAY,
    exit_date: str = _TODAY,
    pnl: float = 1_500.0,
    pnl_pct: float = 6.0,
    r_multiple: float = 2.5,
    exit_reason: str = "target",
) -> dict:
    return {
        "symbol": symbol,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_price": 2_500.0,
        "exit_price": 2_650.0,
        "quantity": 10,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "exit_reason": exit_reason,
        "r_multiple": r_multiple,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """TestClient with auth disabled."""
    app = FastAPI()
    app.include_router(portfolio_module.router)

    for var in ("API_READ_KEY", "API_ADMIN_KEY"):
        os.environ.pop(var, None)

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Test 10 – GET /portfolio with valid portfolio.json
# ---------------------------------------------------------------------------

class TestGetPortfolio:
    def test_portfolio_returns_summary_schema(self, client):
        """Test 10: valid portfolio.json → 200 with PortfolioSummarySchema fields."""
        portfolio_json = _make_portfolio_json(
            cash=80_000.0,
            initial_capital=100_000.0,
        )

        # Patch _PORTFOLIO_FILE inside the router module
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = portfolio_json

        with patch.object(portfolio_module, "_PORTFOLIO_FILE", mock_path):
            resp = client.get("/api/v1/portfolio")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

        data = body["data"]
        # Verify expected PortfolioSummarySchema fields are present
        for field in ("cash", "open_value", "total_value", "initial_capital",
                      "total_return_pct", "win_rate", "total_trades",
                      "open_count", "closed_count", "positions"):
            assert field in data, f"Missing field: {field}"

        assert data["cash"] == pytest.approx(80_000.0)
        assert data["initial_capital"] == pytest.approx(100_000.0)
        assert isinstance(data["positions"], list)

    def test_portfolio_with_open_positions(self, client):
        """Portfolio with one open position includes it in summary."""
        positions = {
            "RELIANCE": {
                "symbol": "RELIANCE",
                "entry_date": _YESTERDAY,
                "entry_price": 2500.0,
                "quantity": 5,
                "stop_loss": 2350.0,
                "target_price": 2750.0,
                "sepa_score": 85,
                "setup_quality": "A+",
                "pyramided": False,
                "pyramid_qty": 0,
                "peak_close": 2550.0,
                "trailing_stop": 2365.0,
                "days_held": 3,
            }
        }
        portfolio_json = _make_portfolio_json(cash=50_000.0, positions=positions)

        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = portfolio_json

        with patch.object(portfolio_module, "_PORTFOLIO_FILE", mock_path):
            resp = client.get("/api/v1/portfolio")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["open_count"] == 1
        assert len(data["positions"]) == 1
        assert data["positions"][0]["symbol"] == "RELIANCE"


# ---------------------------------------------------------------------------
# Test 11 – GET /portfolio with missing file
# ---------------------------------------------------------------------------

class TestGetPortfolioMissing:
    def test_missing_portfolio_json_returns_404(self, client):
        """Test 11: portfolio.json absent → HTTP 404."""
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = False

        with patch.object(portfolio_module, "_PORTFOLIO_FILE", mock_path):
            resp = client.get("/api/v1/portfolio")

        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body

    def test_trades_endpoint_also_404_when_missing(self, client):
        """GET /portfolio/trades also returns 404 when file absent."""
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = False

        with patch.object(portfolio_module, "_PORTFOLIO_FILE", mock_path):
            resp = client.get("/api/v1/portfolio/trades")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 12 – GET /portfolio/trades?status=closed
# ---------------------------------------------------------------------------

class TestGetTrades:
    def test_closed_trades_filter(self, client):
        """Test 12: status=closed → only closed trade records returned."""
        trades = [
            _make_closed_trade("RELIANCE", pnl=1500.0, exit_reason="target"),
            _make_closed_trade("TCS", pnl=-500.0, exit_reason="trailing_stop"),
        ]
        portfolio_json = _make_portfolio_json(closed_trades=trades)

        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = portfolio_json

        with patch.object(portfolio_module, "_PORTFOLIO_FILE", mock_path):
            resp = client.get("/api/v1/portfolio/trades?status=closed")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert isinstance(data, list)
        assert len(data) == 2
        # Validate required TradeSchema fields are present
        for trade in data:
            for field in ("symbol", "entry_date", "exit_date", "entry_price",
                          "exit_price", "quantity", "pnl", "pnl_pct",
                          "r_multiple", "exit_reason"):
                assert field in trade, f"Missing field: {field}"

    def test_all_trades_filter(self, client):
        """status=all returns same as status=closed (closed trades only)."""
        trades = [_make_closed_trade("RELIANCE")]
        portfolio_json = _make_portfolio_json(closed_trades=trades)

        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = portfolio_json

        with patch.object(portfolio_module, "_PORTFOLIO_FILE", mock_path):
            resp_all = client.get("/api/v1/portfolio/trades?status=all")
            resp_closed = client.get("/api/v1/portfolio/trades?status=closed")

        assert resp_all.status_code == 200
        assert resp_closed.status_code == 200
        assert len(resp_all.json()["data"]) == len(resp_closed.json()["data"])

    def test_empty_closed_trades(self, client):
        """No closed trades → returns empty list (not 404)."""
        portfolio_json = _make_portfolio_json(closed_trades=[])

        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = portfolio_json

        with patch.object(portfolio_module, "_PORTFOLIO_FILE", mock_path):
            resp = client.get("/api/v1/portfolio/trades?status=closed")

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    def test_invalid_status_returns_400(self, client):
        """Unknown status value → HTTP 400."""
        portfolio_json = _make_portfolio_json()

        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = portfolio_json

        with patch.object(portfolio_module, "_PORTFOLIO_FILE", mock_path):
            resp = client.get("/api/v1/portfolio/trades?status=invalid")

        assert resp.status_code == 400

    def test_trades_meta_includes_count(self, client):
        """Response meta.count equals number of returned trades."""
        trades = [
            _make_closed_trade("RELIANCE"),
            _make_closed_trade("TCS"),
        ]
        portfolio_json = _make_portfolio_json(closed_trades=trades)

        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = portfolio_json

        with patch.object(portfolio_module, "_PORTFOLIO_FILE", mock_path):
            resp = client.get("/api/v1/portfolio/trades")

        body = resp.json()
        assert body["meta"]["count"] == len(body["data"])
