"""Human-triggered Phase 6A autonomous draft planning endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.db.session import get_db
from app.jobs import queue
from app.models.planner import PlannerRun
from app.schemas.planner import PlannerStart
from app.services import planner

router = APIRouter(prefix="/planner", tags=["planner"])


@router.post("/runs", status_code=201, summary="Plan the next video into a human-review draft")
async def start(
    db: Annotated[AsyncSession, Depends(get_db)],
    body: PlannerStart | None = None,
) -> dict[str, Any]:
    """Create one durable planning run and hand execution to the worker.

    LLM inference and source retrieval can take minutes.  Returning a job
    reference keeps an HTTP disconnect from creating an ambiguous run.
    """
    payload = body or PlannerStart()
    run = await planner.start_run(db, trigger=payload.trigger)
    job_obj, _ = await queue.enqueue(
        db,
        "planner.prepare_next_video",
        {"planner_run_id": str(run.id)},
        idempotency_key=f"planner-run:{run.id}",
    )
    response = await planner.serialize_run(db, run)
    response["job_id"] = str(job_obj.id)
    return response


@router.get("/runs", summary="List autonomous planning history")
async def list_runs(db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, Any]:
    statement = select(PlannerRun).order_by(PlannerRun.created_at.desc())
    runs = list((await db.execute(statement)).scalars())
    return {"items": [await planner.serialize_run(db, run) for run in runs]}


@router.get("/runs/{run_id}", summary="Retrieve planning rationale and review package")
async def get_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    run = await db.get(PlannerRun, run_id)
    if run is None:
        raise NotFoundError(f"Planner run {run_id} not found")
    return await planner.serialize_run(db, run)


@router.post("/runs/{run_id}/enqueue", status_code=202, summary="Queue a pending planner run")
async def enqueue_pending_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Recover a durable PENDING run left before a worker was available."""
    run = await db.get(PlannerRun, run_id)
    if run is None:
        raise NotFoundError(f"Planner run {run_id} not found")
    if run.status != "PENDING":
        raise ConflictError(f"Planner run {run_id} is {run.status}, not PENDING.")
    job_obj, _ = await queue.enqueue(
        db,
        "planner.prepare_next_video",
        {"planner_run_id": str(run.id)},
        idempotency_key=f"planner-run:{run.id}:recovery",
    )
    response = await planner.serialize_run(db, run)
    response["job_id"] = str(job_obj.id)
    return response


@router.post("/runs/{run_id}/resume", status_code=202, summary="Resume a failed planner run")
async def resume_run(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    current = await db.get(PlannerRun, run_id)
    if current is None:
        raise NotFoundError(f"Planner run {run_id} not found")
    checkpoint = current.completed_at.isoformat() if current.completed_at else "pending"
    run = await planner.resume_failed_run(db, run_id=run_id)
    job_obj, _ = await queue.enqueue(
        db,
        "planner.prepare_next_video",
        {"planner_run_id": str(run.id), "resume": True},
        idempotency_key=f"planner-run:{run.id}:resume:{checkpoint}",
    )
    response = await planner.serialize_run(db, run)
    response["job_id"] = str(job_obj.id)
    return response


@router.post(
    "/runs/{run_id}/proposals/{proposal_id}/select",
    status_code=202,
    summary="Use a candidate and resume draft preparation",
)
async def select_candidate(
    run_id: uuid.UUID,
    proposal_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    run, chosen = await planner.select_proposal(
        db, run_id=run_id, proposal_id=proposal_id
    )
    job_obj, _ = await queue.enqueue(
        db,
        "planner.prepare_next_video",
        {"planner_run_id": str(run.id), "resume": True},
        idempotency_key=f"planner-run:{run.id}:selection:{chosen.id}",
    )
    response = await planner.serialize_run(db, run)
    response["job_id"] = str(job_obj.id)
    return response
