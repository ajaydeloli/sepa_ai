"""
tests/unit/test_api_auth.py
---------------------------
Unit tests for api/auth.py.

All tests manipulate os.environ directly (via monkeypatch) so the module
re-reads keys on every call without any import-time caching side-effects.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.auth import get_auth_status, require_admin_key, require_read_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

READ_KEY = "test-read-key-abc"
ADMIN_KEY = "test-admin-key-xyz"
BAD_KEY = "completely-wrong-key"


def _set_keys(monkeypatch, *, read: str = READ_KEY, admin: str = ADMIN_KEY) -> None:
    """Populate both env vars."""
    monkeypatch.setenv("API_READ_KEY", read)
    monkeypatch.setenv("API_ADMIN_KEY", admin)


def _clear_keys(monkeypatch) -> None:
    """Remove both env vars (simulates auth-disabled mode)."""
    monkeypatch.delenv("API_READ_KEY", raising=False)
    monkeypatch.delenv("API_ADMIN_KEY", raising=False)


# ---------------------------------------------------------------------------
# require_read_key
# ---------------------------------------------------------------------------


class TestRequireReadKey:
    def test_valid_read_key_passes(self, monkeypatch):
        """require_read_key accepts a valid read key and returns it."""
        _set_keys(monkeypatch)
        result = require_read_key(api_key=READ_KEY)
        assert result == READ_KEY

    def test_valid_admin_key_passes(self, monkeypatch):
        """Admin key is also accepted on read endpoints (admin can read)."""
        _set_keys(monkeypatch)
        result = require_read_key(api_key=ADMIN_KEY)
        assert result == ADMIN_KEY

    def test_invalid_key_raises_401(self, monkeypatch):
        """An unrecognised key raises HTTP 401."""
        _set_keys(monkeypatch)
        with pytest.raises(HTTPException) as exc_info:
            require_read_key(api_key=BAD_KEY)
        assert exc_info.value.status_code == 401

    def test_missing_key_raises_401(self, monkeypatch):
        """Missing key (None) raises HTTP 401 when auth is enabled."""
        _set_keys(monkeypatch)
        with pytest.raises(HTTPException) as exc_info:
            require_read_key(api_key=None)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# require_admin_key
# ---------------------------------------------------------------------------


class TestRequireAdminKey:
    def test_read_key_raises_403(self, monkeypatch):
        """Supplying a read key to an admin endpoint raises HTTP 403."""
        _set_keys(monkeypatch)
        with pytest.raises(HTTPException) as exc_info:
            require_admin_key(api_key=READ_KEY)
        assert exc_info.value.status_code == 403

    def test_valid_admin_key_passes(self, monkeypatch):
        """Admin key is accepted on admin endpoints and returned."""
        _set_keys(monkeypatch)
        result = require_admin_key(api_key=ADMIN_KEY)
        assert result == ADMIN_KEY

    def test_missing_key_raises_401(self, monkeypatch):
        """No key at an admin endpoint raises HTTP 401."""
        _set_keys(monkeypatch)
        with pytest.raises(HTTPException) as exc_info:
            require_admin_key(api_key=None)
        assert exc_info.value.status_code == 401

    def test_invalid_key_raises_401(self, monkeypatch):
        """An unrecognised key that is not the read key raises HTTP 401."""
        _set_keys(monkeypatch)
        with pytest.raises(HTTPException) as exc_info:
            require_admin_key(api_key=BAD_KEY)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Auth disabled (empty env vars)
# ---------------------------------------------------------------------------


class TestAuthDisabled:
    def test_read_endpoint_passes_without_key(self, monkeypatch):
        """When auth is disabled, require_read_key lets through a None key."""
        _clear_keys(monkeypatch)
        result = require_read_key(api_key=None)
        assert result == ""

    def test_admin_endpoint_passes_without_key(self, monkeypatch):
        """When auth is disabled, require_admin_key lets through a None key."""
        _clear_keys(monkeypatch)
        result = require_admin_key(api_key=None)
        assert result == ""

    def test_read_endpoint_passes_with_any_key(self, monkeypatch):
        """When auth is disabled, any key value is also accepted."""
        _clear_keys(monkeypatch)
        result = require_read_key(api_key="whatever")
        assert result == "whatever"

    def test_get_auth_status_disabled(self, monkeypatch):
        """get_auth_status returns auth_enabled=False when env vars are absent."""
        _clear_keys(monkeypatch)
        status = get_auth_status()
        assert status == {"auth_enabled": False}

    def test_get_auth_status_enabled(self, monkeypatch):
        """get_auth_status returns auth_enabled=True when keys are set."""
        _set_keys(monkeypatch)
        status = get_auth_status()
        assert status == {"auth_enabled": True}
