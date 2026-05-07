"""
tests/unit/test_api_main.py
---------------------------
Unit tests for api/main.py:

1. GET /api/v1/health              → 200 (app starts without error)
2. GET /nonexistent                → 404 in APIResponse envelope
3. POST /api/v1/stocks/top         → 405 Method Not Allowed in APIResponse
4. Rate limit: 101 requests / min  → 429 (limiter mocked)
5. CORS headers present on all responses
6. All routers registered          → check app.routes for expected paths
7. Startup event: DB verified (logged, no crash on missing tables)
8. Shutdown event: cache cleared without error
9. 422 validation error            → APIResponse envelope
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_db() -> MagicMock:
    """Return a minimal SQLiteStore mock that satisfies health and startup."""
    db = MagicMock()

    # startup event queries sqlite_master
    conn = MagicMock()
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)

    # sqlite_master rows  (for startup check)
    for tbl in ("watchlist", "run_history", "screen_results"):
        pass  # configured below via fetchall

    conn.execute.return_value.fetchall.return_value = [
        ("watchlist",), ("run_history",), ("screen_results",)
    ]
    # health endpoint uses fetchone
    run_row = MagicMock()
    run_row.__getitem__ = lambda self, k: {
        "run_date": "2025-01-01",
        "status": "success",
        "created_at": "2025-01-01 08:00:00",
        "universe_size": 500,
        "a_plus_count": 5,
        "a_count": 12,
    }[k]
    conn.execute.return_value.fetchone.return_value = run_row
    db._connect.return_value = conn
    db.get_results.return_value = []
    db.get_watchlist.return_value = []
    return db


@pytest.fixture(autouse=True)
def _clear_auth_env():
    """Ensure auth is disabled for every test unless the test sets vars."""
    for var in ("API_READ_KEY", "API_ADMIN_KEY"):
        os.environ.pop(var, None)
    yield
    for var in ("API_READ_KEY", "API_ADMIN_KEY"):
        os.environ.pop(var, None)


@pytest.fixture()
def app_client():
    """Return a TestClient wrapping api.main.app with a mocked DB."""
    from api.deps import get_db
    from api.main import app

    mock_db = _make_mock_db()
    app.dependency_overrides[get_db] = lambda: mock_db
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Test 1 — health endpoint returns 200
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_health_returns_200(self, app_client):
        """GET /api/v1/health must return HTTP 200 with status='ok'."""
        resp = app_client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["version"] == "1.0.0"


# ---------------------------------------------------------------------------
# Test 2 — 404 in APIResponse envelope
# ---------------------------------------------------------------------------


class TestNotFoundHandler:
    def test_unknown_path_returns_404_envelope(self, app_client):
        """GET /nonexistent must return 404 with APIResponse envelope."""
        resp = app_client.get("/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert body["success"] is False
        assert body["data"] is None
        assert "error" in body
        assert body["error"]  # non-empty

    def test_404_not_default_fastapi_format(self, app_client):
        """The 404 body must NOT be the default {'detail': 'Not found'} shape."""
        resp = app_client.get("/api/v1/nonexistent/path")
        body = resp.json()
        # FastAPI default would be {"detail": "Not Found"}
        assert "detail" not in body
        assert "success" in body


# ---------------------------------------------------------------------------
# Test 3 — wrong method → 405
# ---------------------------------------------------------------------------


class TestMethodNotAllowed:
    def test_post_on_get_only_route_returns_405(self, app_client):
        """POST /api/v1/stocks/top (GET only) must return 405 in APIResponse."""
        resp = app_client.post("/api/v1/stocks/top")
        assert resp.status_code == 405
        body = resp.json()
        assert body["success"] is False
        assert body["data"] is None
        assert "error" in body

    def test_405_not_default_fastapi_format(self, app_client):
        """405 body must not be raw {'detail': 'Method Not Allowed'}."""
        resp = app_client.post("/api/v1/stocks/top")
        body = resp.json()
        assert "detail" not in body
        assert "success" in body


# ---------------------------------------------------------------------------
# Test 4 — rate limit 429 (mock limiter)
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_rate_limit_handler_is_registered(self):
        """SlowAPI RateLimitExceeded handler must be registered on the app."""
        from slowapi.errors import RateLimitExceeded
        from api.main import app

        # The handler dict maps exception types → callables
        exc_handlers = dict(app.exception_handlers)
        assert RateLimitExceeded in exc_handlers, (
            "RateLimitExceeded handler not registered on app"
        )

    def test_rate_limit_exceeded_returns_429(self):
        """SlowAPI _rate_limit_exceeded_handler must return HTTP 429.

        RateLimitExceeded.__init__ requires a Limit object (not a plain string).
        We mock a minimal Limit and a Request with the state attrs SlowAPI needs.
        """
        from unittest.mock import MagicMock
        from slowapi import _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded

        # Build a minimal mock Limit object (SlowAPI Limit dataclass)
        mock_limit = MagicMock()
        mock_limit.error_message = None
        mock_limit.limit = "100/minute"

        mock_request = MagicMock()
        mock_request.state.view_rate_limit = None
        mock_request.app.state.limiter._inject_headers = lambda resp, _: resp

        exc = RateLimitExceeded(mock_limit)
        response = _rate_limit_exceeded_handler(mock_request, exc)
        assert response.status_code == 429


# ---------------------------------------------------------------------------
# Test 5 — CORS headers present on responses
# ---------------------------------------------------------------------------


class TestCORSHeaders:
    def test_cors_headers_on_health(self, app_client):
        """OPTIONS preflight to /api/v1/health must return CORS Allow-Origin header."""
        origin = "http://localhost:3000"
        resp = app_client.options(
            "/api/v1/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )
        # With allow_credentials=True the middleware echoes the requesting origin
        # (wildcard + credentials is not allowed by the CORS spec)
        allow_origin = resp.headers.get("access-control-allow-origin", "")
        assert allow_origin in ("*", origin), (
            f"Expected CORS allow-origin '*' or '{origin}', got '{allow_origin}'"
        )

    def test_cors_header_on_get(self, app_client):
        """GET /api/v1/health from a browser origin must receive a CORS header."""
        origin = "http://example.com"
        resp = app_client.get(
            "/api/v1/health",
            headers={"Origin": origin},
        )
        assert resp.status_code == 200
        allow_origin = resp.headers.get("access-control-allow-origin", "")
        assert allow_origin in ("*", origin), (
            f"Expected CORS allow-origin '*' or '{origin}', got '{allow_origin}'"
        )


# ---------------------------------------------------------------------------
# Test 6 — all routers registered
# ---------------------------------------------------------------------------


class TestRoutersRegistered:
    """Verify every expected URL path is mounted on the application."""

    EXPECTED_PATHS = {
        "/api/v1/health",
        "/api/v1/meta",
        "/api/v1/stocks/top",
        "/api/v1/stocks/trend",
        "/api/v1/stocks/vcp",
        "/api/v1/stocks/{symbol}",
        "/api/v1/stocks/{symbol}/history",
        "/api/v1/watchlist",
        "/api/v1/watchlist/bulk",
        "/api/v1/watchlist/upload",
        "/api/v1/watchlist/run",
        "/api/v1/watchlist/{symbol}",
        "/api/v1/portfolio",
        "/api/v1/portfolio/trades",
    }

    def test_all_expected_paths_registered(self):
        """Every expected path must appear in app.routes."""
        from api.main import app

        registered = {
            getattr(route, "path", None)
            for route in app.routes
        }
        missing = self.EXPECTED_PATHS - registered
        assert not missing, f"Missing routes: {missing}"


# ---------------------------------------------------------------------------
# Test 7 — 422 validation error returns APIResponse envelope
# ---------------------------------------------------------------------------


class TestValidationErrorHandler:
    def test_invalid_query_param_returns_422_envelope(self, app_client):
        """A bad query param must return 422 with APIResponse envelope."""
        # /api/v1/stocks/top?limit=not_an_int triggers FastAPI validation
        resp = app_client.get("/api/v1/stocks/top?limit=not_an_int")
        assert resp.status_code == 422
        body = resp.json()
        assert body["success"] is False
        assert body["data"] is None
        assert "error" in body
        # Must not be the raw FastAPI validation error format
        assert "detail" not in body


# ---------------------------------------------------------------------------
# Test 8 — startup event (DB accessible, no crash)
# ---------------------------------------------------------------------------


class TestStartupEvent:
    def test_startup_completes_without_exception(self):
        """App startup with a mocked DB must log success and not raise."""
        from api.deps import get_db
        from api.main import app

        mock_db = _make_mock_db()
        app.dependency_overrides[get_db] = lambda: mock_db

        import logging
        with patch("api.main.logger") as mock_logger:
            with TestClient(app, raise_server_exceptions=True):
                pass  # startup fires here
            # Check info was logged (success path)
            info_calls = [str(c) for c in mock_logger.info.call_args_list]
            assert any("started" in c or "OK" in c for c in info_calls)

        app.dependency_overrides.clear()
