"""Revision-aware production authorization and status evidence."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.state_machine import transition
from app.jobs import queue
from app.models.content import ContentProject, Script, VideoRender
from app.models.enums import ExperimentStatus, JobStatus, ProjectStatus, RenderStatus
from app.models.experiments import ContentExperiment
from app.models.job import BackgroundJob
from app.services import experiments

ACTIVE_JOB_STATUSES = frozenset({JobStatus.QUEUED.value, JobStatus.RUNNING.value})
ALLOWED_EXPERIMENT_STATUSES = frozenset(
    {
        ExperimentStatus.PLANNED.value,
        ExperimentStatus.IN_PRODUCTION.value,
        # A produced experiment describes the locked intent. A revised script
        # is another production attempt under that same intent, not a new
        # experiment and not a reason to rewrite its historical lifecycle.
        ExperimentStatus.PRODUCED.value,
    }
)


def job_script_id(job: BackgroundJob) -> uuid.UUID | None:
    """Return a job's immutable script revision, including legacy jobs."""
    raw = (job.payload or {}).get("script_id")
    if raw:
        try:
            return uuid.UUID(str(raw))
        except ValueError:
            return None
    # Production keys before revision-aware payloads ended in the script UUID.
    key = job.idempotency_key or ""
    for part in reversed(key.split(":")):
        try:
            return uuid.UUID(part)
        except ValueError:
            continue
    return None


async def project_production_jobs(
    session: AsyncSession, project_id: uuid.UUID
) -> list[BackgroundJob]:
    return list(
        (
            await session.execute(
                select(BackgroundJob)
                .where(
                    BackgroundJob.project_id == project_id,
                    BackgroundJob.job_type == "content.produce_video",
                )
                .order_by(BackgroundJob.created_at.desc())
            )
        ).scalars()
    )


async def successful_renders(
    session: AsyncSession, project_id: uuid.UUID
) -> list[VideoRender]:
    return list(
        (
            await session.execute(
                select(VideoRender)
                .where(
                    VideoRender.project_id == project_id,
                    VideoRender.status == RenderStatus.SUCCEEDED.value,
                )
                .order_by(VideoRender.created_at.desc())
            )
        ).scalars()
    )


async def production_snapshot(
    session: AsyncSession,
    project: ContentProject,
    script: Script | None,
    experiment: ContentExperiment | None,
) -> dict[str, Any]:
    jobs = await project_production_jobs(session, project.id)
    renders = await successful_renders(session, project.id)
    script_id = script.id if script else None
    revision_jobs = [job for job in jobs if job_script_id(job) == script_id]
    revision_render = next((render for render in renders if render.script_id == script_id), None)
    latest_render = renders[0] if renders else None
    active = next((job for job in revision_jobs if job.status in ACTIVE_JOB_STATUSES), None)
    production_required = script is not None and revision_render is None
    allowed_project = project.status in {
        ProjectStatus.SCRIPT_APPROVED.value,
        ProjectStatus.FAILED.value,
    } or (
        project.status == ProjectStatus.NEEDS_REVISION.value
        and bool(revision_jobs)
    )
    allowed_experiment = experiment is not None and experiment.status in ALLOWED_EXPERIMENT_STATUSES
    return {
        "current_script_id": str(script.id) if script else None,
        "current_script_version": script.version if script else None,
        "latest_rendered_script_id": str(latest_render.script_id) if latest_render else None,
        "current_script_render_id": str(revision_render.id) if revision_render else None,
        "current_script_rendered": revision_render is not None,
        "production_required": production_required,
        "active_job_id": str(active.id) if active else None,
        "active_job_status": active.status if active else None,
        "can_start_production": (
            production_required
            and active is None
            and allowed_project
            and allowed_experiment
        ),
        "action_label": (
            f"Produce Approved Revision — Script v{script.version}"
            if production_required and latest_render is not None and script is not None
            else f"Start Production — Script v{script.version}"
            if production_required and script is not None
            else None
        ),
        "latest_revision_job": revision_jobs[0] if revision_jobs else None,
        "latest_job": jobs[0] if jobs else None,
        "latest_render": latest_render,
    }


async def authorize_production(
    session: AsyncSession,
    *,
    project: ContentProject,
    experiment: ContentExperiment,
    script: Script,
) -> tuple[BackgroundJob, bool]:
    """Authorize exactly one active attempt for an approved script revision."""
    if project.current_script_id != script.id or not script.is_current:
        raise ConflictError("The requested script is no longer the approved current revision.")
    if experiment.status not in ALLOWED_EXPERIMENT_STATUSES:
        raise ConflictError(
            f"Experiment is {experiment.status}; its locked intent cannot start production."
        )

    snapshot = await production_snapshot(session, project, script, experiment)
    if snapshot["current_script_rendered"]:
        raise ConflictError(f"Script v{script.version} already has a successful render.")
    active_id = snapshot["active_job_id"]
    if active_id:
        active = await session.get(BackgroundJob, uuid.UUID(active_id))
        if active is None:  # pragma: no cover - protected by the row lock
            raise ConflictError("The active production job disappeared.")
        return active, False

    if project.status == ProjectStatus.SCRIPT_APPROVED.value:
        await transition(
            session,
            project,
            ProjectStatus.PRODUCTION_PLANNING,
            actor="HUMAN",
            reason=f"Production requested for approved script v{script.version}",
        )
    elif project.status == ProjectStatus.FAILED.value:
        await transition(
            session,
            project,
            ProjectStatus.NEEDS_REVISION,
            actor="HUMAN",
            reason=f"Retry requested for approved script v{script.version}",
        )
        await transition(
            session,
            project,
            ProjectStatus.RENDERING,
            actor="HUMAN",
            reason=f"Retrying production for script v{script.version}",
        )
    elif project.status == ProjectStatus.NEEDS_REVISION.value:
        if not snapshot["latest_revision_job"]:
            raise ConflictError("A revised script must be approved before production.")
        await transition(
            session,
            project,
            ProjectStatus.RENDERING,
            actor="HUMAN",
            reason=f"Retrying production for script v{script.version}",
        )
    elif project.status not in {
        ProjectStatus.PRODUCTION_PLANNING.value,
        ProjectStatus.ASSETS_READY.value,
        ProjectStatus.RENDERING.value,
    }:
        raise ConflictError(f"Project is {project.status}; production is not authorized.")

    await experiments.mark_in_production(session, project.id)
    revision_jobs = [
        job
        for job in await project_production_jobs(session, project.id)
        if job_script_id(job) == script.id
    ]
    attempt = len(revision_jobs) + 1
    job_obj, created = await queue.enqueue(
        session,
        "content.produce_video",
        {
            "project_id": str(project.id),
            "script_id": str(script.id),
            "script_version": script.version,
        },
        project_id=project.id,
        idempotency_key=(
            f"content.produce_video:{project.id}:{script.id}:attempt:{attempt}"
        ),
    )
    return job_obj, created
