"""End-to-end job execution.

These exercise the exact path the worker runs, so what is verified here is what
runs in production.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.errors import RetryableError
from app.db.session import get_sessionmaker
from app.jobs import queue
from app.jobs.context import JobContext
from app.jobs.registry import job
from app.jobs.runner import run_job
from app.models.channel import AppSetting
from app.models.enums import JobEventType, JobStatus
from app.models.job import BackgroundJob, JobEvent

WORKER = "runner-test-worker"


# Test-only handlers, registered by importing this module.
@job("test.writes_then_fails", max_attempts=1, retry_on=(RetryableError,))
async def _writes_then_fails(ctx: JobContext) -> dict:
    ctx.session.add(AppSetting(key="should_not_persist", value={"written": True}))
    await ctx.session.flush()
    raise RetryableError("failing after a write")


@job("test.writes_then_succeeds", max_attempts=1)
async def _writes_then_succeeds(ctx: JobContext) -> dict:
    ctx.session.add(AppSetting(key="should_persist", value={"written": True}))
    await ctx.session.flush()
    return {"wrote": True}


async def _claim_and_run(worker: str = WORKER) -> str | None:
    maker = get_sessionmaker()
    async with maker() as s:
        claimed = await queue.claim(s, worker)
        await s.commit()
        job_id = claimed.id if claimed else None
    if job_id is None:
        return None
    return await run_job(job_id, worker)


async def _run_to_completion(max_cycles: int = 10) -> int:
    """Drive claim+run until the queue has nothing runnable left."""
    cycles = 0
    for _ in range(max_cycles):
        status = await _claim_and_run()
        if status is None:
            await asyncio.sleep(0.05)  # a retry may be pending its backoff
            if await _claim_and_run() is None:
                break
        cycles += 1
    return cycles


async def _reload(job_id) -> BackgroundJob:
    async with get_sessionmaker()() as s:
        return (
            await s.execute(select(BackgroundJob).where(BackgroundJob.id == job_id))
        ).scalar_one()


async def _event_types(job_id) -> list[str]:
    async with get_sessionmaker()() as s:
        rows = (
            (
                await s.execute(
                    select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.created_at)
                )
            )
            .scalars()
            .all()
        )
        return [r.event_type for r in rows]


# ------------------------------------------------------------------ success ---
async def test_ping_runs_and_stores_its_result(session) -> None:
    job_obj, _ = await queue.enqueue(session, "system.ping", {"hello": "world"})
    await session.commit()

    assert await _claim_and_run() == JobStatus.SUCCEEDED

    reloaded = await _reload(job_obj.id)
    assert reloaded.status == JobStatus.SUCCEEDED
    assert reloaded.result["pong"] is True
    assert reloaded.result["echo"] == {"hello": "world"}
    assert reloaded.finished_at is not None


async def test_handler_writes_commit_with_the_success_record(session) -> None:
    """A job's data and its success must land in the same transaction."""
    await queue.enqueue(session, "test.writes_then_succeeds", {})
    await session.commit()

    assert await _claim_and_run() == JobStatus.SUCCEEDED

    async with get_sessionmaker()() as s:
        assert await s.get(AppSetting, "should_persist") is not None


async def test_handler_writes_are_rolled_back_on_failure(session) -> None:
    """A failed job must not leave half-written state behind."""
    job_obj, _ = await queue.enqueue(session, "test.writes_then_fails", {})
    await session.commit()

    assert await _claim_and_run() == JobStatus.FAILED

    async with get_sessionmaker()() as s:
        assert await s.get(AppSetting, "should_not_persist") is None
    # The failure itself was still recorded, despite the rollback.
    reloaded = await _reload(job_obj.id)
    assert reloaded.error_class == "RetryableError"


# -------------------------------------------------------------------- retry ---
async def test_flaky_job_retries_then_succeeds(session) -> None:
    job_obj, _ = await queue.enqueue(session, "system.flaky", {"fail_times": 2})
    await session.commit()

    await _run_to_completion()

    reloaded = await _reload(job_obj.id)
    assert reloaded.status == JobStatus.SUCCEEDED
    assert reloaded.attempt == 3
    assert reloaded.result == {"succeeded_on_attempt": 3, "failed_attempts": 2}

    events = await _event_types(job_obj.id)
    assert events.count(JobEventType.ATTEMPT_FAILED) == 2
    assert events.count(JobEventType.RETRY_SCHEDULED) == 2
    assert events[-1] == JobEventType.SUCCEEDED


async def test_persistent_failure_exhausts_attempts_then_fails(session) -> None:
    job_obj, _ = await queue.enqueue(session, "system.always_fails", {})
    await session.commit()

    await _run_to_completion()

    reloaded = await _reload(job_obj.id)
    assert reloaded.status == JobStatus.FAILED
    assert reloaded.attempt == 2  # max_attempts for system.always_fails
    assert reloaded.error_class == "RetryableError"

    events = await _event_types(job_obj.id)
    assert events.count(JobEventType.ATTEMPT_FAILED) == 2
    assert events[-1] == JobEventType.FAILED


async def test_terminal_failure_does_not_retry(session) -> None:
    job_obj, _ = await queue.enqueue(session, "system.terminal_failure", {})
    await session.commit()

    assert await _claim_and_run() == JobStatus.FAILED

    reloaded = await _reload(job_obj.id)
    assert reloaded.attempt == 1
    assert reloaded.max_attempts == 5  # budget remained, deliberately unused
    assert JobEventType.RETRY_SCHEDULED not in await _event_types(job_obj.id)


# ------------------------------------------------------------------ timeout ---
async def test_job_exceeding_its_timeout_is_recorded_as_timed_out(session) -> None:
    job_obj, _ = await queue.enqueue(
        session, "system.sleep", {"seconds": 30}, timeout_seconds=1
    )
    await session.commit()

    assert await _claim_and_run() == JobStatus.FAILED

    reloaded = await _reload(job_obj.id)
    assert reloaded.error_class == "JobTimeoutError"
    assert "timeout of 1s" in reloaded.error_message
    assert JobEventType.TIMED_OUT in await _event_types(job_obj.id)


# ------------------------------------------------------------ missing handler ---
async def test_job_with_no_registered_handler_fails_terminally(session) -> None:
    """A queued row whose code was deleted. Retrying cannot bring it back."""
    job_obj, _ = await queue.enqueue(session, "system.ping", {})
    job_obj.job_type = "system.removed_in_a_refactor"
    await session.commit()

    maker = get_sessionmaker()
    async with maker() as s:
        claimed = await queue.claim(s, WORKER)
        await s.commit()
        assert claimed is not None

    assert await run_job(claimed.id, WORKER) == JobStatus.FAILED
    reloaded = await _reload(job_obj.id)
    assert reloaded.status == JobStatus.FAILED
    assert reloaded.error_class == "UnknownJobType"


# ------------------------------------------------------------------ chaining ---
async def test_scheduled_reap_job_runs_and_reports(session) -> None:
    job_obj, _ = await queue.enqueue(session, "system.reap_stale_jobs", {})
    await session.commit()

    assert await _claim_and_run() == JobStatus.SUCCEEDED
    reloaded = await _reload(job_obj.id)
    assert reloaded.result == {"reaped": 0}


async def test_heartbeat_job_persists_a_marker(session) -> None:
    await queue.enqueue(session, "system.heartbeat", {})
    await session.commit()

    assert await _claim_and_run() == JobStatus.SUCCEEDED

    from app.services.system_status import HEARTBEAT_KEY, get_last_heartbeat

    async with get_sessionmaker()() as s:
        assert await s.get(AppSetting, HEARTBEAT_KEY) is not None
        assert await get_last_heartbeat(s) is not None
