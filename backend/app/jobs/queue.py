"""PostgreSQL-backed job queue (ADR 0001).

Claiming uses `SELECT ... FOR UPDATE SKIP LOCKED`, which lets N workers pull
distinct rows without blocking each other and without a broker.

Transaction discipline: none of these functions commit. They flush, and the
caller owns the transaction. That is deliberate — it is what allows a state
transition and the job it triggers to commit atomically
(`PHASE-1-ARCHITECTURE.md` §8.3 rule 4).
"""

from __future__ import annotations

import traceback as tb_module
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import String, bindparam, delete, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.clock import utcnow
from app.core.ids import uuid7
from app.core.logging import get_logger
from app.jobs.backoff import compute_backoff_seconds
from app.jobs.registry import JobDefinition, get_definition, is_registered
from app.models.enums import JobEventType, JobStatus
from app.models.job import BackgroundJob, JobEvent

log = get_logger(__name__)

# Extra slack beyond a job's timeout before the reaper assumes the worker died.
REAP_GRACE_SECONDS = 30


# The claim statement.
#
# Two notes, both learned the hard way:
#
# 1. The type filter uses CAST(...) rather than the shorter `::type` form.
#    SQLAlchemy's text() parser will not bind a parameter immediately followed
#    by `::`, because that is ambiguous with a PostgreSQL cast. The casts
#    themselves are needed because PostgreSQL cannot infer a parameter's type
#    from an empty array, and WORKER_JOB_TYPES is empty by default.
#
# 2. Keep SQL comments out of this string. text() scans the whole statement for
#    `:name` tokens, comments included, so a colon inside a comment silently
#    becomes a required bind parameter and breaks execution. Explanations
#    belong here, in Python, where they cannot do that.
#
# `test_claim_sql_binds_exactly_the_expected_parameters` guards both.
_CLAIM_SQL = text(
    """
    UPDATE background_job AS j
       SET status       = 'RUNNING',
           attempt      = j.attempt + 1,
           claimed_by   = :worker_id,
           claimed_at   = now(),
           started_at   = COALESCE(j.started_at, now()),
           heartbeat_at = now(),
           updated_at   = now()
     WHERE j.id = (
           SELECT c.id
             FROM background_job AS c
            WHERE c.status = 'QUEUED'
              AND c.run_after <= now()
              AND (
                    CAST(:filter_types AS boolean) = false
                 OR c.job_type = ANY(CAST(:job_types AS text[]))
              )
            ORDER BY c.priority DESC, c.run_after
            FOR UPDATE SKIP LOCKED
            LIMIT 1
     )
    RETURNING j.id
    """
).bindparams(bindparam("job_types", type_=ARRAY(String)))

# Every parameter the claim statement expects. Asserted by the unit tests so a
# stray colon can never reintroduce a phantom parameter.
CLAIM_SQL_PARAMS = frozenset({"worker_id", "filter_types", "job_types"})


async def record_event(
    session: AsyncSession,
    job: BackgroundJob,
    event_type: JobEventType,
    *,
    message: str | None = None,
    data: dict[str, Any] | None = None,
) -> JobEvent:
    event = JobEvent(
        id=uuid7(),
        job_id=job.id,
        event_type=event_type,
        attempt=job.attempt,
        message=message,
        data=data,
    )
    session.add(event)
    return event


# ------------------------------------------------------------------ enqueue ---
async def enqueue(
    session: AsyncSession,
    job_type: str,
    payload: dict[str, Any] | None = None,
    *,
    priority: int | None = None,
    run_after: Any = None,
    delay_seconds: float | None = None,
    max_attempts: int | None = None,
    timeout_seconds: int | None = None,
    idempotency_key: str | None = None,
    project_id: uuid.UUID | None = None,
) -> tuple[BackgroundJob, bool]:
    """Enqueue a job. Returns `(job, created)`.

    When `idempotency_key` collides with an existing job, nothing is inserted
    and the existing job is returned with `created=False`. This is what makes
    scheduled triggers safe to fire more than once (ARCH §9.3) and is the first
    layer of the never-upload-twice guard (ARCH §13.4).

    Does not commit — the caller's transaction decides.
    """
    if not is_registered(job_type):
        raise KeyError(
            f"Job type {job_type!r} is not registered. "
            "Register it with @job(...) and import it from load_all_jobs()."
        )
    definition = get_definition(job_type)

    if run_after is None:
        run_after = utcnow() + timedelta(seconds=delay_seconds) if delay_seconds else utcnow()

    values = {
        "id": uuid7(),
        "job_type": job_type,
        "payload": payload or {},
        "status": JobStatus.QUEUED.value,
        "priority": definition.default_priority if priority is None else priority,
        "run_after": run_after,
        "attempt": 0,
        "max_attempts": max_attempts or definition.max_attempts,
        "timeout_seconds": timeout_seconds or definition.timeout_seconds,
        "idempotency_key": idempotency_key,
        "project_id": project_id,
    }

    stmt = (
        pg_insert(BackgroundJob)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(BackgroundJob.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()

    if inserted_id is None:
        # Idempotency collision: return the job that already owns the key.
        existing = (
            await session.execute(
                select(BackgroundJob).where(BackgroundJob.idempotency_key == idempotency_key)
            )
        ).scalar_one()
        log.debug(
            "job.enqueue.deduplicated",
            job_type=job_type,
            idempotency_key=idempotency_key,
            existing_job_id=str(existing.id),
        )
        return existing, False

    job_obj = (
        await session.execute(select(BackgroundJob).where(BackgroundJob.id == inserted_id))
    ).scalar_one()
    await record_event(session, job_obj, JobEventType.ENQUEUED, data={"payload": payload or {}})
    await session.flush()

    log.info(
        "job.enqueued",
        job_id=str(job_obj.id),
        job_type=job_type,
        priority=job_obj.priority,
        project_id=str(project_id) if project_id else None,
    )
    return job_obj, True


# -------------------------------------------------------------------- claim ---
async def claim(
    session: AsyncSession,
    worker_id: str,
    job_types: list[str] | None = None,
) -> BackgroundJob | None:
    """Atomically claim one runnable job, or return None.

    Increments `attempt` as part of the claim, so a worker that dies mid-job
    cannot cause an infinite retry loop: the attempt is already spent.
    """
    types = job_types or []
    result = await session.execute(
        _CLAIM_SQL,
        {"worker_id": worker_id, "filter_types": bool(types), "job_types": types},
    )
    claimed_id = result.scalar_one_or_none()
    if claimed_id is None:
        return None

    job_obj = (
        await session.execute(select(BackgroundJob).where(BackgroundJob.id == claimed_id))
    ).scalar_one()
    await record_event(
        session, job_obj, JobEventType.CLAIMED, data={"worker_id": worker_id}
    )
    await session.flush()
    return job_obj


async def heartbeat(session: AsyncSession, job_id: uuid.UUID, worker_id: str) -> bool:
    """Refresh a running job's liveness marker. False if we no longer own it."""
    result = await session.execute(
        text(
            """
            UPDATE background_job
               SET heartbeat_at = now(), updated_at = now()
             WHERE id = :job_id AND claimed_by = :worker_id AND status = 'RUNNING'
            RETURNING id
            """
        ),
        {"job_id": job_id, "worker_id": worker_id},
    )
    return result.scalar_one_or_none() is not None


# --------------------------------------------------------------- completion ---
async def complete(
    session: AsyncSession,
    job_obj: BackgroundJob,
    result: dict[str, Any] | None = None,
) -> BackgroundJob:
    job_obj.status = JobStatus.SUCCEEDED
    job_obj.finished_at = utcnow()
    job_obj.result = result
    job_obj.error_class = None
    job_obj.error_message = None
    job_obj.traceback = None
    await record_event(session, job_obj, JobEventType.SUCCEEDED, data=result)
    await session.flush()
    log.info(
        "job.succeeded",
        job_id=str(job_obj.id),
        job_type=job_obj.job_type,
        attempt=job_obj.attempt,
    )
    return job_obj


async def fail(
    session: AsyncSession,
    job_obj: BackgroundJob,
    exc: BaseException,
    definition: JobDefinition | None = None,
) -> BackgroundJob:
    """Record a failed attempt, then either schedule a retry or fail terminally.

    Retryability comes from the job definition, never from a guess. An
    unrecognised exception is terminal by default (ARCH §9.2) — blind retries
    burn quota and, in the publishing path, risk duplicate uploads.
    """
    if definition is None and is_registered(job_obj.job_type):
        definition = get_definition(job_obj.job_type)

    retryable = definition.is_retryable(exc) if definition else False
    attempts_left = job_obj.attempt < job_obj.max_attempts

    job_obj.error_class = type(exc).__name__
    job_obj.error_message = str(exc)[:4000]
    job_obj.traceback = "".join(
        tb_module.format_exception(type(exc), exc, exc.__traceback__)
    )[:16000]

    await record_event(
        session,
        job_obj,
        JobEventType.ATTEMPT_FAILED,
        message=f"{type(exc).__name__}: {exc}"[:1000],
        data={"retryable": retryable, "attempts_left": attempts_left},
    )

    if retryable and attempts_left:
        delay = compute_backoff_seconds(
            job_obj.attempt,
            base_seconds=settings.JOB_RETRY_BASE_SECONDS,
            max_seconds=settings.JOB_RETRY_MAX_SECONDS,
        )
        job_obj.status = JobStatus.QUEUED
        job_obj.run_after = utcnow() + timedelta(seconds=delay)
        job_obj.claimed_by = None
        job_obj.claimed_at = None
        job_obj.heartbeat_at = None
        await record_event(
            session,
            job_obj,
            JobEventType.RETRY_SCHEDULED,
            message=f"retry in {delay:.1f}s",
            data={"delay_seconds": round(delay, 3), "next_attempt": job_obj.attempt + 1},
        )
        log.warning(
            "job.retry_scheduled",
            job_id=str(job_obj.id),
            job_type=job_obj.job_type,
            attempt=job_obj.attempt,
            max_attempts=job_obj.max_attempts,
            delay_seconds=round(delay, 3),
            error=job_obj.error_class,
        )
    else:
        job_obj.status = JobStatus.FAILED
        job_obj.finished_at = utcnow()
        reason = "attempts_exhausted" if retryable else "terminal_error"
        await record_event(session, job_obj, JobEventType.FAILED, message=reason)
        log.error(
            "job.failed",
            job_id=str(job_obj.id),
            job_type=job_obj.job_type,
            attempt=job_obj.attempt,
            reason=reason,
            error=job_obj.error_class,
            error_message=job_obj.error_message,
        )

    await session.flush()
    return job_obj


# ---------------------------------------------------------------- recovery ---
async def _requeue_or_fail(
    session: AsyncSession,
    job_obj: BackgroundJob,
    event_type: JobEventType,
    message: str,
) -> None:
    if job_obj.attempt < job_obj.max_attempts:
        job_obj.status = JobStatus.QUEUED
        job_obj.run_after = utcnow()
        job_obj.claimed_by = None
        job_obj.claimed_at = None
        job_obj.heartbeat_at = None
        await record_event(session, job_obj, event_type, message=message)
    else:
        job_obj.status = JobStatus.FAILED
        job_obj.finished_at = utcnow()
        job_obj.error_class = job_obj.error_class or "WorkerLost"
        job_obj.error_message = job_obj.error_message or message
        await record_event(session, job_obj, JobEventType.FAILED, message=message)


async def reap_stale(session: AsyncSession, grace_seconds: int = REAP_GRACE_SECONDS) -> int:
    """Recover jobs whose worker stopped heartbeating.

    A worker that is SIGKILLed or loses its host leaves rows stuck in RUNNING.
    Without this they are invisible to the claim query forever.
    """
    stale = (
        (
            await session.execute(
                select(BackgroundJob)
                .where(BackgroundJob.status == JobStatus.RUNNING)
                .where(
                    text(
                        "heartbeat_at < now() - make_interval("
                        "secs => background_job.timeout_seconds + :grace)"
                    ).bindparams(grace=grace_seconds)
                )
            )
        )
        .scalars()
        .all()
    )

    for job_obj in stale:
        log.warning(
            "job.reaped",
            job_id=str(job_obj.id),
            job_type=job_obj.job_type,
            claimed_by=job_obj.claimed_by,
            attempt=job_obj.attempt,
        )
        await _requeue_or_fail(
            session,
            job_obj,
            JobEventType.REAPED,
            f"heartbeat stale; worker {job_obj.claimed_by} presumed dead",
        )

    if stale:
        await session.flush()
    return len(stale)


async def recover_worker_jobs(session: AsyncSession, worker_id: str) -> int:
    """Requeue jobs this worker owned before it restarted (ARCH §9.2)."""
    orphans = (
        (
            await session.execute(
                select(BackgroundJob)
                .where(BackgroundJob.status == JobStatus.RUNNING)
                .where(BackgroundJob.claimed_by == worker_id)
            )
        )
        .scalars()
        .all()
    )
    for job_obj in orphans:
        await _requeue_or_fail(
            session,
            job_obj,
            JobEventType.REQUEUED,
            f"worker {worker_id} restarted while this job was running",
        )
    if orphans:
        await session.flush()
        log.warning("job.recovered_on_startup", worker_id=worker_id, count=len(orphans))
    return len(orphans)


# ------------------------------------------------------------ manual control ---
async def requeue(session: AsyncSession, job_obj: BackgroundJob, *, reset_attempts: bool) -> None:
    """Human-initiated retry from the dashboard.

    Terminal jobs never retry themselves; putting a FAILED job back in flight is
    always an explicit decision.
    """
    job_obj.status = JobStatus.QUEUED
    job_obj.run_after = utcnow()
    job_obj.claimed_by = None
    job_obj.claimed_at = None
    job_obj.heartbeat_at = None
    job_obj.finished_at = None
    if reset_attempts:
        job_obj.attempt = 0
    await record_event(
        session,
        job_obj,
        JobEventType.REQUEUED,
        message="requeued by operator",
        data={"reset_attempts": reset_attempts},
    )
    await session.flush()


async def cancel(session: AsyncSession, job_obj: BackgroundJob) -> None:
    job_obj.status = JobStatus.CANCELLED
    job_obj.finished_at = utcnow()
    await record_event(session, job_obj, JobEventType.CANCELLED)
    await session.flush()


async def purge_history(session: AsyncSession, older_than_days: int | None = None) -> int:
    """Delete terminal jobs older than the retention window."""
    days = older_than_days or settings.JOB_HISTORY_RETENTION_DAYS
    cutoff = utcnow() - timedelta(days=days)
    result = await session.execute(
        delete(BackgroundJob)
        .where(BackgroundJob.status.in_([s.value for s in JobStatus.terminal()]))
        .where(BackgroundJob.finished_at < cutoff)
    )
    await session.flush()
    return result.rowcount or 0
