"""
api/main.py
-----------
SEPA AI Stock Screener — FastAPI application factory.

Responsibilities
~~~~~~~~~~~~~~~~
* Create the FastAPI application with title / version / CORS / rate-limiting.
* Register all routers (health, stocks, watchlist, portfolio).
* Custom exception handlers that always return the APIResponse envelope shape:
    - HTTP 404  → {"success": false, "error": "Not Found",   "data": null}
    - HTTP 422  → {"success": false, "error": "<detail>",    "data": null}
    - HTTP 405  → {"success": false, "error": "Method Not Allowed", "data": null}
* Startup / shutdown lifecycle via the modern ``lifespan`` context manager.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.rate_limit import limiter
from api.routers import health, portfolio, stocks, watchlist

logger = logging.getLogger("api")


# ---------------------------------------------------------------------------
# Lifecycle — startup / shutdown via lifespan context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: RUF029
    """FastAPI lifespan handler — replaces deprecated @app.on_event decorators.

    Startup: verify the SQLite DB is reachable and all core tables exist.
    Shutdown: clear the get_db LRU cache to release the SQLiteStore singleton.
    """
    # ---- startup -------------------------------------------------------
    from api.deps import get_db

    try:
        db = get_db()
        conn = db._connect()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        required = {"watchlist", "run_history", "screen_results"}
        missing = required - tables
        if missing:
            logger.warning("DB startup check: missing tables %s", missing)
        else:
            logger.info(
                "SEPA AI API v%s started — DB OK (tables: %s)",
                app.version,
                ", ".join(sorted(tables)),
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("DB startup check failed: %s", exc, exc_info=True)

    yield  # ---- application runs here ----------------------------------

    # ---- shutdown -------------------------------------------------------
    try:
        get_db.cache_clear()
        logger.info("SEPA AI API shutdown — DB connections released.")
    except Exception as exc:  # noqa: BLE001
        logger.error("Error during shutdown: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SEPA AI Stock Screener API",
    version="1.0.0",
    description="Minervini SEPA screening results API",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware — CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # TODO: restrict via config in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Rate limiting — SlowAPI
# ---------------------------------------------------------------------------

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(health.router)
app.include_router(stocks.router)
app.include_router(watchlist.router)
app.include_router(portfolio.router)

# ---------------------------------------------------------------------------
# Custom exception handlers — always return APIResponse envelope
# ---------------------------------------------------------------------------


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Return all HTTP errors in APIResponse envelope format.

    Covers 404 (Not Found), 405 (Method Not Allowed), 401, 403, etc.
    The ``detail`` from the original exception becomes the ``error`` field.
    """
    if exc.status_code == status.HTTP_404_NOT_FOUND:
        error_msg = str(exc.detail) if exc.detail and exc.detail != "Not Found" else "Not Found"
    elif exc.status_code == status.HTTP_405_METHOD_NOT_ALLOWED:
        error_msg = "Method Not Allowed"
    else:
        error_msg = str(exc.detail) if exc.detail else str(exc.status_code)

    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "data": None, "error": error_msg},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return Pydantic / FastAPI validation errors in APIResponse envelope format.

    The first error's ``msg`` field is used as the ``error`` string so
    callers receive a human-readable message rather than the raw error list.
    """
    errors = exc.errors()
    if errors:
        first = errors[0]
        loc = " → ".join(str(p) for p in first.get("loc", []) if p != "body")
        msg = first.get("msg", "Validation error")
        error_msg = f"{loc}: {msg}" if loc else msg
    else:
        error_msg = "Validation error"

    return JSONResponse(
        status_code=422,   # integer avoids the deprecated HTTP_422_UNPROCESSABLE_ENTITY name
        content={"success": False, "data": None, "error": error_msg},
    )
