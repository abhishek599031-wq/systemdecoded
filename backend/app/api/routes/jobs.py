"""Job inspection and control.

Backs the dashboard's job panels (`PHASE-1-ARCHITECTURE.md` §12.1) and gives an
operator a way to requeue or cancel work without touching SQL.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.session import get_db
from app.jobs import queue
from app.jobs.registry import all_definitions, is_registered
from app.models.enums import JobStatus
from app.models.job import BackgroundJob
from app.schemas.job import (
    EnqueueJobRequest,
    EnqueueJobResponse,
    JobDetail,
    JobListResponse,
    JobSummary,
    JobTypeOut,
    RequeueJobRequest,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/types", response_model=list[JobTypeOut], summary="List registered job types")
async def list_job_types() -> list[JobTypeOut]:
    return [
        JobTypeOut(
            name=d.name,
            description=d.description,
            max_attempts=d.max_attempts,
            timeout_seconds=d.timeout_seconds,
            default_priority=d.default_priority,
            retry_on=[e.__name__ for e in d.retry_on],
        )
        for d in sorted(all_definitions().values(), key=lambda d: d.name)
    ]


@router.get("", response_model=JobListResponse, summary="List jobs")
async def list_jobs(
    db: Annotated[AsyncSession, Depends(get_db)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    job_type: str | None = None,
    project_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> JobListResponse:
    conditions = []
    if status_filter:
        valid = {s.value for s in JobStatus}
        if status_filter not in valid:
            raise ValidationError(
                f"Unknown status {status_filter!r}", detail={"valid": sorted(valid)}
            )
        conditions.append(BackgroundJob.status == status_filter)
    if job_type:
        conditions.append(BackgroundJob.job_type == job_type)
    if project_id:
        conditions.append(BackgroundJob.project_id == project_id)

    total = (
        await db.execute(select(func.count()).select_from(BackgroundJob).where(*conditions))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(BackgroundJob)
                .where(*conditions)
                .order_by(BackgroundJob.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return JobListResponse(
        items=[JobSummary.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _get_job_or_404(db: AsyncSession, job_id: uuid.UUID) -> BackgroundJob:
    job_obj = await db.get(BackgroundJob, job_id)
    if job_obj is None:
        raise NotFoundError(f"Job {job_id} not found")
    return job_obj


@router.get("/{job_id}", response_model=JobDetail, summary="Get a job with its full history")
async def get_job(
    job_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> JobDetail:
    return JobDetail.model_validate(await _get_job_or_404(db, job_id))


@router.post("", response_model=EnqueueJobResponse, summary="Enqueue a job")
async def enqueue_job(
    body: EnqueueJobRequest, db: Annotated[AsyncSession, Depends(get_db)]
) -> EnqueueJobResponse:
    if not is_registered(body.job_type):
        raise ValidationError(
            f"Unknown job type {body.job_type!r}",
            detail={"registered": sorted(all_definitions())},
        )
    job_obj, created = await queue.enqueue(
        db,
        body.job_type,
        body.payload,
        priority=body.priority,
        delay_seconds=body.delay_seconds,
        max_attempts=body.max_attempts,
        idempotency_key=body.idempotency_key,
    )
    return EnqueueJobResponse(job=JobSummary.model_validate(job_obj), created=created)


@router.post("/{job_id}/requeue", response_model=JobSummary, summary="Requeue a job")
async def requeue_job(
    job_id: uuid.UUID,
    body: RequeueJobRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobSummary:
    job_obj = await _get_job_or_404(db, job_id)
    if job_obj.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
        raise ConflictError(
            f"Job is already {job_obj.status}; only terminal jobs can be requeued"
        )
    await queue.requeue(db, job_obj, reset_attempts=body.reset_attempts)
    return JobSummary.model_validate(job_obj)


@router.post("/{job_id}/cancel", response_model=JobSummary, summary="Cancel a queued job")
async def cancel_job(
    job_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> JobSummary:
    job_obj = await _get_job_or_404(db, job_id)
    if job_obj.status != JobStatus.QUEUED:
        # Cancelling a RUNNING job would need cooperative cancellation in the
        # worker. Not built, and pretending otherwise would be worse.
        raise ConflictError(
            f"Only QUEUED jobs can be cancelled; this job is {job_obj.status}"
        )
    await queue.cancel(db, job_obj)
    return JobSummary.model_validate(job_obj)
