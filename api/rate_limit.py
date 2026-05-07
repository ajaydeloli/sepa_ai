"""
api/rate_limit.py
-----------------
Centralised SlowAPI rate-limiter for the SEPA AI API.

Usage in route modules
~~~~~~~~~~~~~~~~~~~~~~
::

    from api.rate_limit import limiter, read_limit, admin_limit
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    # In app setup (main.py / app factory):
    app.state.limiter = get_limiter()
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # On individual route functions:
    @router.get("/stocks")
    @limiter.limit(read_limit)
    async def list_stocks(request: Request, ...):
        ...

    @router.post("/run")
    @limiter.limit(admin_limit)
    async def trigger_run(request: Request, ...):
        ...

Rate limits
~~~~~~~~~~~
* ``read_limit``  — 100 requests / minute  (GET endpoints)
* ``admin_limit`` —  10 requests / minute  (POST /run and other write endpoints)

Keys are resolved by remote IP address via :func:`slowapi.util.get_remote_address`.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

# ---------------------------------------------------------------------------
# Shared limiter instance
# ---------------------------------------------------------------------------

limiter: Limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# Limit strings — import these in routers rather than hard-coding the values
# ---------------------------------------------------------------------------

#: Default limit for read (GET) endpoints.
read_limit: str = "100/minute"

#: Stricter limit for admin / write endpoints (POST /run, etc.).
admin_limit: str = "10/minute"


# ---------------------------------------------------------------------------
# Accessor — useful when the limiter must be injected via FastAPI dependency
# ---------------------------------------------------------------------------


def get_limiter() -> Limiter:
    """Return the shared :class:`~slowapi.Limiter` singleton."""
    return limiter
