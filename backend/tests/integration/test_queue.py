"""Queue correctness against a real PostgreSQL instance.

ADR 0001 accepted the risk of owning queue code on the condition that this file
exists. Every guarantee claimed in `PHASE-1-ARCHITECTURE.md` §9.2 is asserted
here: retries, idempotency, timeouts, history, failure classification, recovery.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.clock import utcnow
from app.core.errors import RetryableError, TerminalError
from app.db.session import get_sessionmaker
from app.jobs import queue
from app.jobs.registry import get_definition
from app.models.enums import JobEventType, JobStatus
from app.models.job import BackgroundJob, JobEvent

WORKER = "test-worker"


async def _events(session, job_id) -> list[str]:
    rows = (
        (
            await session.execute(
                select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [r.event_type for r in rows]


# ----------------------------------------------------------------- enqueue ---
async def test_enqueue_creates_queued_job_with_history(session) -> None:
    job, created = await queue.enqueue(session, "system.ping", {"hello": "world"})
    await session.commit()

    assert created is True
    assert job.status == JobStatus.QUEUED
    assert job.attempt == 0
    assert job.payload == {"hello": "world"}
    # Defaults come from the job definition, not from the caller.
    definition = get_definition("system.ping")
    assert job.max_attempts == definition.max_attempts
    assert job.timeout_seconds == definition.timeout_seconds
    assert await _events(session, job.id) == [JobEventType.ENQUEUED]


async def test_enqueue_rejects_unregistered_job_type(session) -> None:
    with pytest.raises(KeyError, match="not registered"):
        await queue.enqueue(session, "system.does_not_exist", {})


async def test_idempotency_key_prevents_a_second_job(session) -> None:
    first, created_first = await queue.enqueue(
        session, "system.ping", {"n": 1}, idempotency_key="same-key"
    )
    await session.commit()
    second, created_second = await queue.enqueue(
        session, "system.ping", {"n": 2}, idempotency_key="same-key"
    )
    await session.commit()

    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    # The second enqueue must not have overwritten the first job's payload.
    assert second.payload == {"n": 1}

    total = (await session.execute(select(BackgroundJob))).scalars().all()
    assert len(total) == 1


async def test_null_idempotency_keys_do_not_collide(session) -> None:
    """Two jobs without keys are two units of work, not a duplicate."""
    await queue.enqueue(session, "system.ping", {"n": 1})
    await queue.enqueue(session, "system.ping", {"n": 2})
    await session.commit()

    total = (await session.execute(select(BackgroundJob))).scalars().all()
    assert len(total) == 2


async def test_delay_seconds_defers_run_after(session) -> None:
    job, _ = await queue.enqueue(session, "system.ping", {}, delay_seconds=120)
    await session.commit()
    assert job.run_after > utcnow() + timedelta(seconds=100)


# ------------------------------------------------------------------- claim ---
async def test_claim_marks_running_and_spends_an_attempt(session) -> None:
    await queue.enqueue(session, "system.ping", {})
    await session.commit()

    claimed = await queue.claim(session, WORKER)
    await session.commit()

    assert claimed is not None
    assert claimed.status == JobStatus.RUNNING
    assert claimed.attempt == 1  # spent at claim time, so a dead worker cannot loop forever
    assert claimed.claimed_by == WORKER
    assert claimed.heartbeat_at is not None
    assert JobEventType.CLAIMED in await _events(session, claimed.id)


async def test_claim_returns_none_when_queue_is_empty(session) -> None:
    assert await queue.claim(session, WORKER) is None


async def test_claim_respects_priority_then_age(session) -> None:
    await queue.enqueue(session, "system.ping", {"tag": "low"}, priority=0)
    await queue.enqueue(session, "system.ping", {"tag": "high"}, priority=100)
    await session.commit()

    first = await queue.claim(session, WORKER)
    await session.commit()
    assert first is not None
    assert first.payload["tag"] == "high"


async def test_claim_ignores_jobs_scheduled_for_the_future(session) -> None:
    await queue.enqueue(session, "system.ping", {}, delay_seconds=3600)
    await session.commit()
    assert await queue.claim(session, WORKER) is None


async def test_claim_can_filter_by_job_type(session) -> None:
    """Job-type filtering is how media work moves to a dedicated worker later."""
    await queue.enqueue(session, "system.ping", {})
    await session.commit()

    assert await queue.claim(session, WORKER, job_types=["system.sleep"]) is None
    claimed = await queue.claim(session, WORKER, job_types=["system.ping"])
    await session.commit()
    assert claimed is not None


async def test_concurrent_claims_never_return_the_same_job(session) -> None:
    """The SKIP LOCKED guarantee — the whole reason this queue works."""
    for i in range(6):
        await queue.enqueue(session, "system.ping", {"n": i})
    await session.commit()

    maker = get_sessionmaker()

    async def claim_one(worker_id: str):
        async with maker() as s:
            job = await queue.claim(s, worker_id)
            await s.commit()
            return job.id if job else None

    results = await asyncio.gather(*(claim_one(f"w{i}") for i in range(6)))
    claimed_ids = [r for r in results if r is not None]

    assert len(claimed_ids) == 6
    assert len(set(claimed_ids)) == 6  # no two workers got the same row


# -------------------------------------------------------------- completion ---
async def test_complete_records_result_and_clears_error(session) -> None:
    await queue.enqueue(session, "system.ping", {})
    await session.commit()
    job = await queue.claim(session, WORKER)
    job.error_message = "stale error from a previous attempt"

    await queue.complete(session, job, {"pong": True})
    await session.commit()

    assert job.status == JobStatus.SUCCEEDED
    assert job.result == {"pong": True}
    assert job.finished_at is not None
    assert job.error_message is None
    assert JobEventType.SUCCEEDED in await _events(session, job.id)


# ------------------------------------------------------------------ failure ---
async def test_retryable_failure_requeues_with_backoff(session) -> None:
    await queue.enqueue(session, "system.flaky", {"fail_times": 1})
    await session.commit()
    job = await queue.claim(session, WORKER)

    await queue.fail(session, job, RetryableError("boom"), get_definition("system.flaky"))
    await session.commit()

    assert job.status == JobStatus.QUEUED  # claimable again, not a distinct FAILED state
    assert job.attempt == 1
    assert job.claimed_by is None
    assert job.error_class == "RetryableError"
    assert job.run_after > utcnow()

    events = await _events(session, job.id)
    assert JobEventType.ATTEMPT_FAILED in events
    assert JobEventType.RETRY_SCHEDULED in events


async def test_failure_on_last_attempt_is_terminal(session) -> None:
    await queue.enqueue(session, "system.flaky", {}, max_attempts=1)
    await session.commit()
    job = await queue.claim(session, WORKER)

    await queue.fail(session, job, RetryableError("boom"), get_definition("system.flaky"))
    await session.commit()

    assert job.status == JobStatus.FAILED
    assert job.finished_at is not None
    assert JobEventType.FAILED in await _events(session, job.id)


async def test_terminal_error_never_retries_despite_remaining_attempts(session) -> None:
    """The guard that stops a bad payload from burning quota five times over."""
    await queue.enqueue(session, "system.terminal_failure", {}, max_attempts=5)
    await session.commit()
    job = await queue.claim(session, WORKER)

    await queue.fail(
        session, job, TerminalError("nope"), get_definition("system.terminal_failure")
    )
    await session.commit()

    assert job.status == JobStatus.FAILED
    assert job.attempt == 1
    assert job.max_attempts == 5  # attempts remained, and were deliberately not used


async def test_unclassified_exception_is_treated_as_terminal(session) -> None:
    await queue.enqueue(session, "system.flaky", {}, max_attempts=5)
    await session.commit()
    job = await queue.claim(session, WORKER)

    # system.flaky declares retry_on=(RetryableError,) only.
    await queue.fail(session, job, ValueError("surprise"), get_definition("system.flaky"))
    await session.commit()

    assert job.status == JobStatus.FAILED
    assert job.error_class == "ValueError"


async def test_failure_records_traceback(session) -> None:
    await queue.enqueue(session, "system.flaky", {}, max_attempts=1)
    await session.commit()
    job = await queue.claim(session, WORKER)
    try:
        raise RetryableError("with a traceback")
    except RetryableError as exc:
        await queue.fail(session, job, exc, get_definition("system.flaky"))
    await session.commit()

    assert job.traceback is not None
    assert "RetryableError" in job.traceback


# ----------------------------------------------------------------- recovery ---
async def test_heartbeat_refreshes_only_for_the_owning_worker(session) -> None:
    await queue.enqueue(session, "system.ping", {})
    await session.commit()
    job = await queue.claim(session, WORKER)
    await session.commit()

    assert await queue.heartbeat(session, job.id, WORKER) is True
    assert await queue.heartbeat(session, job.id, "someone-else") is False


async def test_reap_requeues_jobs_whose_worker_stopped_beating(session) -> None:
    await queue.enqueue(session, "system.ping", {}, max_attempts=3)
    await session.commit()
    job = await queue.claim(session, WORKER)
    # Simulate a worker that died: heartbeat far older than timeout + grace.
    job.heartbeat_at = utcnow() - timedelta(seconds=job.timeout_seconds + 600)
    await session.commit()

    reaped = await queue.reap_stale(session)
    await session.commit()
    await session.refresh(job)

    assert reaped == 1
    assert job.status == JobStatus.QUEUED
    assert job.claimed_by is None
    assert JobEventType.REAPED in await _events(session, job.id)


async def test_reap_leaves_healthy_jobs_alone(session) -> None:
    await queue.enqueue(session, "system.ping", {})
    await session.commit()
    job = await queue.claim(session, WORKER)
    await session.commit()

    assert await queue.reap_stale(session) == 0
    await session.refresh(job)
    assert job.status == JobStatus.RUNNING


async def test_reap_fails_job_that_has_no_attempts_left(session) -> None:
    await queue.enqueue(session, "system.ping", {}, max_attempts=1)
    await session.commit()
    job = await queue.claim(session, WORKER)
    job.heartbeat_at = utcnow() - timedelta(seconds=job.timeout_seconds + 600)
    await session.commit()

    await queue.reap_stale(session)
    await session.commit()
    await session.refresh(job)

    assert job.status == JobStatus.FAILED
    assert job.error_class == "WorkerLost"


async def test_worker_restart_recovers_its_own_orphans(session) -> None:
    await queue.enqueue(session, "system.ping", {}, max_attempts=3)
    await session.commit()
    job = await queue.claim(session, WORKER)
    await session.commit()

    recovered = await queue.recover_worker_jobs(session, WORKER)
    await session.commit()
    await session.refresh(job)

    assert recovered == 1
    assert job.status == JobStatus.QUEUED
    assert JobEventType.REQUEUED in await _events(session, job.id)


async def test_recovery_ignores_other_workers_jobs(session) -> None:
    await queue.enqueue(session, "system.ping", {})
    await session.commit()
    await queue.claim(session, WORKER)
    await session.commit()

    assert await queue.recover_worker_jobs(session, "a-different-worker") == 0


# ----------------------------------------------------------- manual control ---
async def test_requeue_can_restore_the_full_attempt_budget(session) -> None:
    await queue.enqueue(session, "system.flaky", {}, max_attempts=1)
    await session.commit()
    job = await queue.claim(session, WORKER)
    await queue.fail(session, job, RetryableError("x"), get_definition("system.flaky"))
    await session.commit()
    assert job.status == JobStatus.FAILED

    await queue.requeue(session, job, reset_attempts=True)
    await session.commit()

    assert job.status == JobStatus.QUEUED
    assert job.attempt == 0
    assert job.finished_at is None


async def test_cancel_marks_cancelled(session) -> None:
    job, _ = await queue.enqueue(session, "system.ping", {})
    await session.commit()

    await queue.cancel(session, job)
    await session.commit()

    assert job.status == JobStatus.CANCELLED
    assert await queue.claim(session, WORKER) is None


async def test_purge_history_only_removes_old_terminal_jobs(session) -> None:
    old_done, _ = await queue.enqueue(session, "system.ping", {"tag": "old"})
    recent_done, _ = await queue.enqueue(session, "system.ping", {"tag": "recent"})
    still_queued, _ = await queue.enqueue(session, "system.ping", {"tag": "queued"})
    await session.commit()

    old_done.status = JobStatus.SUCCEEDED
    old_done.finished_at = utcnow() - timedelta(days=120)
    recent_done.status = JobStatus.SUCCEEDED
    recent_done.finished_at = utcnow()
    await session.commit()

    deleted = await queue.purge_history(session, older_than_days=90)
    await session.commit()

    assert deleted == 1
    remaining = {
        j.payload["tag"] for j in (await session.execute(select(BackgroundJob))).scalars().all()
    }
    assert remaining == {"recent", "queued"}
    assert still_queued.status == JobStatus.QUEUED
