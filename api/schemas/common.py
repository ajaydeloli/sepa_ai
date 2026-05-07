"""
api/schemas/common.py
---------------------
Shared envelope types used across every SEPA API response.

  APIResponse[T]   — generic success wrapper  (success=True, data=T)
  PaginationMeta   — pagination metadata injected into APIResponse.meta
  ErrorResponse    — error-only body          (success=False, error=…)
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    """Generic success/error envelope for every endpoint.

    On success:   success=True,  data=<payload>,  error=None
    On error:     success=False, data=None,        error="<message>"
    """

    success: bool
    data: T
    meta: dict | None = None
    error: str | None = None


class PaginationMeta(BaseModel):
    """Pagination metadata carried in APIResponse.meta for list endpoints."""

    total: int
    page: int
    per_page: int
    date: str | None = None


class ErrorResponse(BaseModel):
    """Flat error body returned by exception handlers."""

    success: bool = False
    error: str
    detail: str | None = None
