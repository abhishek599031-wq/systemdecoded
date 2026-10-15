"""Human-controlled Phase 5 experiment endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.db.session import get_db
from app.models.content import ContentProject, Script
from app.models.experiments import ContentExperiment
from app.schemas.experiments import ExperimentCreate, ExperimentPatch
from app.services import experiments, production_lifecycle

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
    # Serialize production authorization for this experiment. Without the row
    # lock, two double-clicked requests can both observe PLANNED and enqueue
    # separate jobs before either transaction commits.
    experiment = await db.get(ContentExperiment, experiment_id, with_for_update=True)
    if experiment is None:
        raise NotFoundError(f"Experiment {experiment_id} not found")
    project = await db.get(ContentProject, experiment.project_id, with_for_update=True)
    if project is None:
        raise ConflictError("The experiment project no longer exists.")

    script = await db.get(Script, project.current_script_id, with_for_update=True)
    if script is None:
        raise ConflictError("The project has no approved current script.")
    job, created = await production_lifecycle.authorize_production(
        db, project=project, experiment=experiment, script=script
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
