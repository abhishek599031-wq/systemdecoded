"""Human-controlled Phase 5 experiment endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.state_machine import transition
from app.db.session import get_db
from app.jobs import queue
from app.models.content import ContentProject
from app.models.enums import ExperimentStatus, ProjectStatus
from app.schemas.experiments import ExperimentCreate, ExperimentPatch
from app.services import experiments

router = APIRouter(prefix="/experiments", tags=["experiments"])


@router.post("", summary="Create a DRAFT content experiment", status_code=201)
async def create(
    body: ExperimentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    row = await experiments.create_experiment(db, body)
    return experiments.serialize_experiment(row)


@router.get("", summary="List content experiments")
async def list_all(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    items = await experiments.list_experiments(db)
    return {
        "items": items,
        "total": len(items),
        "evidence": await experiments._evidence_counts(db),
        "observational_only": True,
    }


@router.get("/{experiment_id}/comparison", summary="Compare planned and actual features")
async def comparison(
    experiment_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await experiments.comparison(db, experiment_id)


@router.get("/{experiment_id}/outcome", summary="Get experiment outcome")
async def outcome(
    experiment_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    return await experiments.outcome(db, experiment_id)


@router.patch("/{experiment_id}", summary="Edit a DRAFT experiment")
async def update(
    experiment_id: uuid.UUID,
    body: ExperimentPatch,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    row = await experiments.update_draft(db, experiment_id, body)
    return experiments.serialize_experiment(row)


@router.post("/{experiment_id}/plan", summary="Lock an experiment plan")
async def plan(
    experiment_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    row = await experiments.plan_experiment(db, experiment_id)
    return experiments.serialize_experiment(row)


@router.post("/{experiment_id}/launch", summary="Start the existing production pipeline")
async def launch(
    experiment_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    experiment = await experiments.get_experiment(db, experiment_id)
    if experiment.status != ExperimentStatus.PLANNED.value:
        raise ConflictError("Only PLANNED experiments can launch production.")
    project = await db.get(ContentProject, experiment.project_id)
    if project is None:
        raise ConflictError("The experiment project no longer exists.")
    if project.status != ProjectStatus.SCRIPT_APPROVED.value:
        raise ConflictError(
            f"Project is {project.status}; research and script approval must finish first."
        )
    await experiments.mark_in_production(db, project.id)
    await transition(
        db,
        project,
        ProjectStatus.PRODUCTION_PLANNING,
        actor="HUMAN",
        reason="Approved experiment launched into production",
    )
    job, created = await queue.enqueue(
        db,
        "content.produce_video",
        {"project_id": str(project.id)},
        project_id=project.id,
    )
    return {
        "experiment_id": experiment.id,
        "project_id": project.id,
        "job_id": job.id,
        "created": created,
        "experiment_status": experiment.status,
        "project_status": project.status,
    }


@router.get("/{experiment_id}", summary="Retrieve one content experiment")
async def get_one(
    experiment_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    row = await experiments.get_experiment(db, experiment_id)
    return experiments.serialize_experiment(row)
