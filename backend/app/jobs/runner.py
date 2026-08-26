"""Job execution.

Shared by the worker and by tests, so the exact code path that runs in
production is the one under test.

One subtlety worth stating: the handler's database work and the job's
completion record commit in the *same* transaction. A job that writes rows and
then reports success cannot leave those two facts disagreeing.
"""

from __future__ import annotations

import asyncio
import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import JobTimeoutError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.jobs import queue
from app.jobs.context import JobContext
from app.jobs.registry import JobDefinition, UnknownJobType, get_definition
from app.models.enums import JobEventType, JobStatus
from app.models.job import BackgroundJob

log = get_logger(__name__)


async def run_job(job_id: uuid.UUID, worker_id: str) -> str:
    """Execute one claimed job to a terminal-or-requeued outcome.

    Returns the resulting job status. Never raises for handler failures — those
    are recorded on the job. It only raises if the queue itself is broken.
    """
    async with session_scope() as session:
        job_obj = await session.get(BackgroundJob, job_id)
        if job_obj is None:
            log.error("job.missing_on_run", job_id=str(job_id))
            return "MISSING"

        structlog.contextvars.bind_contextvars(
            job_id=str(job_obj.id),
            job_type=job_obj.job_type,
            attempt=job_obj.attempt,
        )
        try:
            definition = get_definition(job_obj.job_type)
        except UnknownJobType as exc:
            # A queued row whose handler no longer exists. Terminal by
            # definition: retrying cannot make the code reappear.
            await queue.fail(session, job_obj, exc, definition=None)
            structlog.contextvars.unbind_contextvars("job_id", "job_type", "attempt")
            return JobStatus.FAILED.value

        ctx = JobContext(
            job_id=job_obj.id,
            job_type=job_obj.job_type,
            attempt=job_obj.attempt,
            max_attempts=job_obj.max_attempts,
            payload=job_obj.payload or {},
            session=session,
            logger=log.bind(job_id=str(job_obj.id), job_type=job_obj.job_type),
            project_id=job_obj.project_id,
        )

        log.info("job.started", timeout_seconds=job_obj.timeout_seconds)
        timed_out = False
        try:
            result = await asyncio.wait_for(
                definition.func(ctx), timeout=job_obj.timeout_seconds
            )
            await queue.complete(session, job_obj, result)
            status = JobStatus.SUCCEEDED.value

        except (TimeoutError, asyncio.TimeoutError) as exc:  # noqa: UP041
            timed_out = True
            failure: BaseException = JobTimeoutError(
                f"exceeded timeout of {job_obj.timeout_seconds}s"
            )
            failure.__cause__ = exc
            status = await _record_failure(session, job_id, failure, definition, timed_out)

        except asyncio.CancelledError:
            # Worker shutdown. Leave the row RUNNING so the reaper requeues it —
            # do NOT consume a retry for an operator-initiated stop.
            log.warning("job.cancelled_by_shutdown")
            raise

        except BaseException as exc:  # noqa: BLE001 - classified by the definition
            status = await _record_failure(session, job_id, exc, definition, timed_out)

        finally:
            structlog.contextvars.unbind_contextvars("job_id", "job_type", "attempt")

        return status


async def _record_failure(
    session: AsyncSession,
    job_id: uuid.UUID,
    exc: BaseException,
    definition: JobDefinition | None,
    timed_out: bool,
) -> str:
    """Roll back the handler's partial work, then record the failure.

    The rollback matters: a handler that raised mid-transaction leaves the
    session unusable, and the failure record must not be lost to it.
    """
    await session.rollback()
    job_obj = await session.get(BackgroundJob, job_id)
    if job_obj is None:  # pragma: no cover - defensive
        return "MISSING"
    if timed_out:
        await queue.record_event(session, job_obj, JobEventType.TIMED_OUT)
    await queue.fail(session, job_obj, exc, definition=definition)
    return job_obj.status
