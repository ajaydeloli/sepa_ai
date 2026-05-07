"""
api/auth.py
-----------
Authentication helpers for the SEPA AI API.

Two dependency functions are provided:
  - ``require_read_key``  — read endpoints (GET); accepts both read and admin keys.
  - ``require_admin_key`` — write/admin endpoints (POST /run, etc.); admin key only.

Keys are loaded from environment variables at call-time so they can be
overridden in tests without restarting the process.

Auth is automatically **disabled** when both ``API_READ_KEY`` and
``API_ADMIN_KEY`` are unset (empty strings or missing), which is the default
in development.  Use ``get_auth_status()`` to inspect the current mode.
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _load_keys() -> tuple[str, str]:
    """Return ``(read_key, admin_key)`` from environment variables.

    Returns empty strings when the variables are absent so callers can
    perform a simple truthiness check.
    """
    read_key = os.environ.get("API_READ_KEY", "").strip()
    admin_key = os.environ.get("API_ADMIN_KEY", "").strip()
    return read_key, admin_key


# ---------------------------------------------------------------------------
# Public dependency functions
# ---------------------------------------------------------------------------


def require_read_key(api_key: str | None = Security(API_KEY_HEADER)) -> str:
    """FastAPI dependency: validates X-API-Key for read (GET) endpoints.

    * When auth is **disabled** (both env vars empty) every request passes
      and an empty string is returned as the resolved key.
    * Accepts either ``API_READ_KEY`` or ``API_ADMIN_KEY`` (admin can read).
    * Raises HTTP 401 when a key is required but absent or invalid.
    """
    read_key, admin_key = _load_keys()

    # Auth disabled — development mode
    if not read_key and not admin_key:
        return api_key or ""

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Supply the X-API-Key header.",
        )

    if api_key == read_key or api_key == admin_key:
        return api_key

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key.",
    )


def require_admin_key(api_key: str | None = Security(API_KEY_HEADER)) -> str:
    """FastAPI dependency: validates X-API-Key for admin-only endpoints.

    * When auth is **disabled** every request passes.
    * Only ``API_ADMIN_KEY`` is accepted.
    * Raises HTTP 401 when no key is provided.
    * Raises HTTP 403 when a read key is supplied for an admin endpoint.
    """
    read_key, admin_key = _load_keys()

    # Auth disabled — development mode
    if not read_key and not admin_key:
        return api_key or ""

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Supply the X-API-Key header.",
        )

    # Read key presented at an admin endpoint → 403 (authenticated but not authorised)
    if read_key and api_key == read_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Read key is not authorised for admin endpoints. Use the admin key.",
        )

    if admin_key and api_key == admin_key:
        return api_key

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key.",
    )


# ---------------------------------------------------------------------------
# Status helper
# ---------------------------------------------------------------------------


def get_auth_status() -> dict:
    """Return ``{"auth_enabled": bool}``.

    Auth is considered *disabled* when both ``API_READ_KEY`` and
    ``API_ADMIN_KEY`` are absent or empty — useful for health-check
    endpoints and admin dashboards.
    """
    read_key, admin_key = _load_keys()
    return {"auth_enabled": bool(read_key or admin_key)}
