"""System and diagnostic jobs.

`system.ping`, `system.flaky` and friends are not filler — they are how the
Phase 0 exit criteria are demonstrated and how the queue is exercised end to
end without any domain code existing yet. They stay in the codebase as
diagnostics for verifying a deployment.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.errors import RetryableError, TerminalError
from app.jobs import queue
from app.jobs.context import JobContext
from app.jobs.registry import job


@job(
    "system.ping",
    max_attempts=1,
    timeout_seconds=30,
    description="Health probe. Echoes its payload back as the job result.",
)
async def ping(ctx: JobContext) -> dict[str, Any]:
    ctx.logger.info("system.ping", payload=ctx.payload)
    return {"pong": True, "echo": ctx.payload, "attempt": ctx.attempt}


@job(
    "system.sleep",
    max_attempts=1,
    timeout_seconds=10,
    description="Sleeps for payload.seconds. Used to demonstrate timeout handling.",
)
async def sleep_job(ctx: JobContext) -> dict[str, Any]:
    seconds = float(ctx.payload.get("seconds", 1))
    await asyncio.sleep(seconds)
    return {"slept_seconds": seconds}


@job(
    "system.flaky",
    max_attempts=3,
    timeout_seconds=30,
    retry_on=(RetryableError,),
    description="Fails for the first payload.fail_times attempts, then succeeds.",
)
async def flaky(ctx: JobContext) -> dict[str, Any]:
    """Demonstrates retry-then-succeed.

    Deterministic on purpose: driven by the attempt counter rather than
    randomness, so the retry path is reproducible in tests.
    """
    fail_times = int(ctx.payload.get("fail_times", 1))
    if ctx.attempt <= fail_times:
        raise RetryableError(f"synthetic transient failure on attempt {ctx.attempt}")
    return {"succeeded_on_attempt": ctx.attempt, "failed_attempts": fail_times}


@job(
    "system.always_fails",
    max_attempts=2,
    timeout_seconds=30,
    retry_on=(RetryableError,),
    description="Always raises a retryable error. Demonstrates attempt exhaustion.",
)
async def always_fails(ctx: JobContext) -> dict[str, Any]:
    raise RetryableError(f"synthetic permanent-transient failure (attempt {ctx.attempt})")


@job(
    "system.terminal_failure",
    max_attempts=5,
    timeout_seconds=30,
    description="Raises a terminal error. Must fail on attempt 1 despite max_attempts=5.",
)
async def terminal_failure(ctx: JobContext) -> dict[str, Any]:
    raise TerminalError("synthetic terminal failure; retrying cannot help")


@job(
    "system.reap_stale_jobs",
    max_attempts=1,
    timeout_seconds=60,
    default_priority=10,
    description="Requeues jobs whose worker stopped heartbeating.",
)
async def reap_stale_jobs(ctx: JobContext) -> dict[str, Any]:
    reaped = await queue.reap_stale(ctx.session)
    if reaped:
        ctx.logger.warning("system.reaped_stale_jobs", count=reaped)
    return {"reaped": reaped}


@job(
    "system.purge_job_history",
    max_attempts=1,
    timeout_seconds=300,
    description="Deletes terminal jobs older than the retention window.",
)
async def purge_job_history(ctx: JobContext) -> dict[str, Any]:
    deleted = await queue.purge_history(ctx.session)
    ctx.logger.info("system.purged_job_history", deleted=deleted)
    return {"deleted": deleted}


@job(
    "system.heartbeat",
    max_attempts=1,
    timeout_seconds=30,
    description="Periodic liveness marker proving the scheduler and worker are paired up.",
)
async def system_heartbeat(ctx: JobContext) -> dict[str, Any]:
    from app.services.system_status import record_heartbeat

    await record_heartbeat(ctx.session)
    return {"ok": True}
