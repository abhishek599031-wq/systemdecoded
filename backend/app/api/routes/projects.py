"""Content project and review endpoints.

Backs the Review UI (ARCH §12.1): everything needed to judge a video —
the video itself, the script, the scenes with measured timings, the factual
sources, and the QC report — reachable from one place.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.state_machine import transition
from app.db.session import get_db
from app.jobs import queue
from app.models.content import (
    ContentProject,
    ProductionAsset,
    ProjectTransition,
    PublishedVideo,
    PublishingJob,
    QualityCheck,
    ResearchNote,
    Scene,
    Script,
    VideoRender,
)
from app.models.enums import ProjectStatus
from app.services import publishing

router = APIRouter(prefix="/projects", tags=["projects"])


async def _get_project(db: AsyncSession, project_id: uuid.UUID) -> ContentProject:
    project = await db.get(ContentProject, project_id)
    if project is None:
        raise NotFoundError(f"Project {project_id} not found")
    return project


def _scene_json(scene: Scene) -> dict[str, Any]:
    return {
        "scene_number": scene.scene_number,
        "narration": scene.narration,
        "on_screen_text": scene.on_screen_text,
        "visual_instruction": scene.visual_instruction,
        "template_id": scene.template_id,
        "template_props": scene.template_props,
        "start_seconds": float(scene.start_seconds) if scene.start_seconds is not None else None,
        "end_seconds": float(scene.end_seconds) if scene.end_seconds is not None else None,
        "duration_seconds": scene.duration_seconds,
    }


@router.get("", summary="List content projects")
async def list_projects(
    db: Annotated[AsyncSession, Depends(get_db)],
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    stmt = select(ContentProject).order_by(ContentProject.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(ContentProject.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "items": [
            {
                "id": str(p.id),
                "topic": p.topic,
                "working_title": p.working_title,
                "status": p.status,
                "content_pillar": p.content_pillar,
                "target_duration_seconds": p.target_duration_seconds,
                "failure_reason": p.failure_reason,
                "created_at": p.created_at.isoformat(),
            }
            for p in rows
        ],
        "total": len(rows),
    }


@router.get("/{project_id}", summary="Full project detail for review")
async def get_project(
    project_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    project = await _get_project(db, project_id)

    script = (
        await db.execute(
            select(Script).where(Script.project_id == project.id, Script.is_current.is_(True))
        )
    ).scalar_one_or_none()

    scenes: list[Scene] = []
    if script is not None:
        scenes = list(
            (
                await db.execute(
                    select(Scene).where(Scene.script_id == script.id).order_by(Scene.scene_number)
                )
            ).scalars().all()
        )

    notes = (
        await db.execute(select(ResearchNote).where(ResearchNote.project_id == project.id))
    ).scalars().all()

    render = None
    if project.current_render_id:
        render = await db.get(VideoRender, project.current_render_id)

    checks: list[QualityCheck] = []
    if render is not None:
        checks = list(
            (
                await db.execute(
                    select(QualityCheck)
                    .where(QualityCheck.render_id == render.id)
                    .order_by(QualityCheck.created_at.desc())
                )
            ).scalars().all()
        )

    assets = (
        await db.execute(
            select(ProductionAsset).where(ProductionAsset.project_id == project.id)
        )
    ).scalars().all()

    transitions = (
        await db.execute(
            select(ProjectTransition)
            .where(ProjectTransition.project_id == project.id)
            .order_by(ProjectTransition.created_at)
        )
    ).scalars().all()

    pub_job = (
        await db.execute(select(PublishingJob).where(PublishingJob.project_id == project.id))
    ).scalars().first()
    published = (
        await db.execute(select(PublishedVideo).where(PublishedVideo.project_id == project.id))
    ).scalars().first()

    return {
        "id": str(project.id),
        "topic": project.topic,
        "working_title": project.working_title,
        "status": project.status,
        "status_detail": project.status_detail,
        "failure_reason": project.failure_reason,
        "content_pillar": project.content_pillar,
        "content_format": project.content_format,
        "curiosity_gap": project.curiosity_gap,
        "target_duration_seconds": project.target_duration_seconds,
        "script": (
            {
                "id": str(script.id),
                "version": script.version,
                "selected_title": script.selected_title,
                "title_candidates": list(script.title_candidates or []),
                "selected_hook": script.selected_hook,
                "hook_candidates": list(script.hook_candidates or []),
                "narration": script.narration,
                "description": script.description,
                "hashtags": list(script.hashtags or []),
                "word_count": script.word_count,
                "authoring_mode": script.authoring_mode,
            }
            if script
            else None
        ),
        "scenes": [_scene_json(s) for s in scenes],
        "research": [
            {
                "claim": n.claim,
                "claim_type": n.claim_type,
                "confidence": n.confidence,
                "verification_status": n.verification_status,
                "source": (
                    {"title": n.source.title, "url": n.source.url, "publisher": n.source.publisher}
                    if n.source
                    else None
                ),
            }
            for n in notes
        ],
        "render": (
            {
                "id": str(render.id),
                "status": render.status,
                "output_path": render.output_path,
                "filename": Path(render.output_path).name if render.output_path else None,
                "width": render.width,
                "height": render.height,
                "fps": render.fps,
                "duration_seconds": float(render.duration_seconds)
                if render.duration_seconds
                else None,
                "bytes": render.bytes,
                "loudness_lufs": float(render.loudness_lufs) if render.loudness_lufs else None,
                "peak_dbfs": float(render.peak_dbfs) if render.peak_dbfs else None,
                "error_message": render.error_message,
            }
            if render
            else None
        ),
        "quality": (
            {
                "verdict": checks[0].verdict,
                "checks": checks[0].checks,
                "blocking_issues": list(checks[0].blocking_issues or []),
                "warnings": list(checks[0].warnings or []),
            }
            if checks
            else None
        ),
        "assets": [
            {
                "asset_type": a.asset_type,
                "origin": a.origin,
                "license": a.license,
                "file_path": a.file_path,
                "provider": a.provider,
                "bytes": a.bytes,
            }
            for a in assets
        ],
        "publishing": (
            publishing.package_as_dict(pub_job, render) if pub_job and render else None
        ),
        "published_video": (
            {
                "youtube_video_id": published.youtube_video_id,
                "url": f"https://www.youtube.com/watch?v={published.youtube_video_id}",
                "reconciled_at": published.reconciled_at.isoformat()
                if published.reconciled_at
                else None,
                "method": published.reconciliation_method,
            }
            if published
            else None
        ),
        "timeline": [
            {
                "from": t.from_status,
                "to": t.to_status,
                "actor": t.actor,
                "reason": t.reason,
                "at": t.created_at.isoformat(),
            }
            for t in transitions
        ],
    }


@router.get("/{project_id}/video", summary="Stream the rendered video")
async def get_video(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    r: Annotated[str | None, Query()] = None,
):
    """Stream the current render.

    `r` (the render id, passed by the frontend) is not used to look anything
    up — it exists purely to make the URL change whenever the render changes.
    `FileResponse` sets no explicit Cache-Control, so browsers apply heuristic
    caching keyed on the URL; without a cache-busting parameter, re-rendering a
    project (same `project_id`, same URL) served the browser's stale cached
    copy of the *previous* video, correct QC report and script text
    notwithstanding. With the render id in the URL, each render gets a URL of
    its own and is safe to cache aggressively and correctly.
    """
    project = await _get_project(db, project_id)
    if not project.current_render_id:
        raise NotFoundError("This project has no render yet")
    render = await db.get(VideoRender, project.current_render_id)
    if render is None or not render.output_path:
        raise NotFoundError("Render output not found")
    path = Path(render.output_path)
    if not path.exists():
        raise NotFoundError(f"Render file is missing from disk: {path.name}")

    headers = (
        {"Cache-Control": "public, max-age=31536000, immutable"}
        if r == str(render.id)
        # Caller didn't pass the current render id (an older client, or a
        # direct link) — never cache, so a stale copy can't be served here either.
        else {"Cache-Control": "no-store"}
    )
    return FileResponse(path, media_type="video/mp4", filename=path.name, headers=headers)


@router.post("/{project_id}/produce", summary="Queue a production run")
async def produce(
    project_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    project = await _get_project(db, project_id)

    # Walk the project into a state production can legally start from. FAILED
    # goes via NEEDS_REVISION because the state machine has no FAILED->RENDERING
    # edge: a failed render is retriaged, not silently retried.
    if project.status == ProjectStatus.SCRIPT_APPROVED:
        await transition(db, project, ProjectStatus.PRODUCTION_PLANNING,
                         actor="HUMAN", reason="Production requested")
    elif project.status == ProjectStatus.FAILED:
        await transition(db, project, ProjectStatus.NEEDS_REVISION,
                         actor="HUMAN", reason="Retrying after failure")
        await transition(db, project, ProjectStatus.RENDERING,
                         actor="HUMAN", reason="Retrying production")
    elif project.status == ProjectStatus.NEEDS_REVISION:
        await transition(db, project, ProjectStatus.RENDERING,
                         actor="HUMAN", reason="Retrying production")

    job_obj, created = await queue.enqueue(
        db, "content.produce_video", {"project_id": str(project.id)}, project_id=project.id
    )
    return {"job_id": str(job_obj.id), "created": created, "status": project.status}


@router.post("/{project_id}/approve-script", summary="Approve the current script")
async def approve_script(
    project_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    project = await _get_project(db, project_id)
    # Walk the project up from IDEA in one step for the Phase 2 manual flow.
    path = [
        ProjectStatus.IDEA_APPROVED,
        ProjectStatus.RESEARCHING,
        ProjectStatus.RESEARCH_READY,
        ProjectStatus.SCRIPT_GENERATING,
        ProjectStatus.SCRIPT_REVIEW,
        ProjectStatus.SCRIPT_APPROVED,
    ]
    for target in path:
        if project.status == target:
            continue
        try:
            await transition(db, project, target, actor="HUMAN", reason="Phase 2 manual approval")
        except Exception:  # noqa: BLE001 - already past this step
            continue
    return {"status": project.status}


@router.post("/{project_id}/review", summary="Approve, revise or reject a rendered video")
async def review(
    project_id: uuid.UUID,
    body: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    project = await _get_project(db, project_id)
    decision = str(body.get("decision", "")).lower()
    notes = body.get("notes")

    if project.status != ProjectStatus.VIDEO_REVIEW:
        raise ConflictError(f"Project is {project.status}; only VIDEO_REVIEW can be reviewed")

    if decision == "approve":
        await transition(db, project, ProjectStatus.APPROVED_FOR_PUBLISHING,
                         actor="HUMAN", reason=notes or "Approved by reviewer")
        job_obj, _ = await queue.enqueue(
            db, "content.build_publishing_package", {"project_id": str(project.id)},
            project_id=project.id,
        )
        return {"status": project.status, "publishing_job_id": str(job_obj.id)}

    if decision == "revise":
        await transition(db, project, ProjectStatus.NEEDS_REVISION,
                         actor="HUMAN", reason=notes or "Revision requested")
        return {"status": project.status}

    if decision == "reject":
        await transition(db, project, ProjectStatus.REJECTED,
                         actor="HUMAN", reason=notes or "Rejected by reviewer")
        return {"status": project.status}

    raise ValidationError("decision must be one of: approve, revise, reject")


@router.post("/{project_id}/published", summary="Record the manually uploaded video")
async def record_published(
    project_id: uuid.UUID,
    body: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Associate a YouTube video ID with this project after a manual upload."""
    project = await _get_project(db, project_id)
    video_id = str(body.get("youtube_video_id", "")).strip()
    if not video_id:
        raise ValidationError("youtube_video_id is required")

    published = await publishing.record_published_video(
        db, project, video_id, method="manual_confirmation"
    )
    if project.status == ProjectStatus.AWAITING_HUMAN_UPLOAD:
        await transition(db, project, ProjectStatus.PUBLISHED, actor="HUMAN",
                         reason=f"Manually uploaded as {video_id}")
    return {
        "status": project.status,
        "youtube_video_id": published.youtube_video_id,
        "url": f"https://www.youtube.com/watch?v={published.youtube_video_id}",
    }
