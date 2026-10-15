"""Bounded background wrapper for the human-triggered planner."""

from __future__ import annotations

import uuid
from typing import Any

from app.core.errors import RetryableError
from app.jobs.context import JobContext
from app.jobs.registry import job
from app.services import planner


@job(
    "planner.prepare_next_video",
    max_attempts=1,
    timeout_seconds=900,
    retry_on=(RetryableError,),
    default_priority=5,
    description="Prepare one researched content draft for human review.",
)
async def prepare_next_video(ctx: JobContext) -> dict[str, Any]:
    run = await planner.execute(
        ctx.session, run_id=uuid.UUID(ctx.require("planner_run_id"))
    )
    return {"planner_run_id": str(run.id), "status": run.status}
