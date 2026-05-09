"""
api/routers/run.py
------------------
Pipeline trigger endpoint.

  POST /api/v1/run   — kick off a daily screening run in the background.

The run executes scripts/run_daily.py as a subprocess so it never blocks
the API event loop. The response is returned immediately; the run continues
in the background and its result is written to run_history in SQLite.

Requires admin key (or auth-disabled dev mode).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, field_validator

from api.auth import require_admin_key
from api.schemas.common import APIResponse

logger = logging.getLogger("api.run")

# Absolute path to the project root (two levels up from this file)
_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPT = _ROOT / "scripts" / "run_daily.py"

router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_admin_key)],
)


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    scope: str = "all"

    @field_validator("scope")
    @classmethod
    def _validate_scope(cls, v: str) -> str:
        allowed = {"all", "universe", "watchlist"}
        if v not in allowed:
            raise ValueError(f"scope must be one of {allowed}")
        return v


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


async def _run_pipeline(scope: str) -> None:
    """Launch run_daily.py in a subprocess; log stdout/stderr."""
    cmd = [sys.executable, str(_SCRIPT), "--scope", scope, "--date", "today"]
    logger.info("Pipeline trigger: %s", " ".join(cmd))
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_ROOT),
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode(errors="replace").strip()
        if proc.returncode == 0:
            logger.info("Pipeline finished (rc=0):\n%s", output)
        else:
            logger.error("Pipeline failed (rc=%d):\n%s", proc.returncode, output)
    except Exception as exc:  # noqa: BLE001
        logger.error("Pipeline subprocess error: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/run")
async def trigger_run(
    body: RunRequest,
    background_tasks: BackgroundTasks,
) -> APIResponse[dict]:
    """Trigger a manual pipeline run asynchronously.

    Returns immediately with ``{"queued": true}``; the actual run proceeds
    in the background via ``scripts/run_daily.py``.
    """
    background_tasks.add_task(_run_pipeline, body.scope)
    logger.info("Pipeline run queued (scope=%s)", body.scope)
    return APIResponse(
        success=True,
        data={"queued": True, "scope": body.scope},
    )
