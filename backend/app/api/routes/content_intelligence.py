"""Operational APIs for content features and observational learning records."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.db.session import get_db
from app.jobs.tasks.analytics import enqueue_video_sync
from app.models.analytics import YouTubeVideoWindowMetric
from app.models.channel import Channel
from app.models.content import ContentProject, PublishedVideo
from app.models.content_intelligence import ContentFeatureProfile
from app.models.enums import AnalyticsWindowType
from app.schemas.content_intelligence import EditorialProfileUpdate
from app.services import content_intelligence

router = APIRouter(prefix="/content-intelligence", tags=["content-intelligence"])


async def _project(db: AsyncSession, project_id: uuid.UUID) -> ContentProject:
    project = await db.get(ContentProject, project_id)
    if project is None:
        raise NotFoundError(f"Content project {project_id} not found")
    return project


async def _video(db: AsyncSession, published_video_id: uuid.UUID) -> PublishedVideo:
    video = await db.get(PublishedVideo, published_video_id)
    if video is None:
        raise NotFoundError(f"Published video {published_video_id} not found")
    return video


@router.get("/records", summary="List learning-ready observational records")
async def records(
    db: Annotated[AsyncSession, Depends(get_db)],
    topic_category: str | None = None,
    hook_type: str | None = None,
    window_available: AnalyticsWindowType | None = None,
) -> dict:
    items = await content_intelligence.learning_records(
        db,
        topic_category=topic_category,
        hook_type=hook_type,
        window_available=window_available,
    )
    return {"items": items, "total": len(items)}


@router.get("/projects/{project_id}/profile", summary="Get one content feature profile")
async def profile(
    project_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    await _project(db, project_id)
    row = (
        await db.execute(
            select(ContentFeatureProfile).where(ContentFeatureProfile.project_id == project_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Content feature profile has not been derived yet")
    return content_intelligence.profile_payload(row)


@router.patch("/projects/{project_id}/profile", summary="Review editorial content features")
async def update_profile(
    project_id: uuid.UUID,
    body: EditorialProfileUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    await _project(db, project_id)
    row = await content_intelligence.update_editorial_profile(
        db,
        project_id,
        body.model_dump(exclude_unset=True),
    )
    return content_intelligence.profile_payload(row)


@router.get("/videos/{published_video_id}/windows", summary="Authoritative lifecycle windows")
async def windows(
    published_video_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    video = await _video(db, published_video_id)
    rows = list(
        (
            await db.execute(
                select(YouTubeVideoWindowMetric)
                .where(YouTubeVideoWindowMetric.published_video_id == video.id)
                .order_by(YouTubeVideoWindowMetric.window_end_date)
            )
        ).scalars()
    )
    return {"items": [content_intelligence.window_payload(row) for row in rows]}


@router.get("/videos/{published_video_id}/record", summary="Combined feature/performance record")
async def record(
    published_video_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    return await content_intelligence.learning_record(
        db,
        await _video(db, published_video_id),
    )


@router.post("/videos/{published_video_id}/refresh", summary="Queue a full learning-data refresh")
async def refresh(
    published_video_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    video = await _video(db, published_video_id)
    channel = (
        await db.execute(select(Channel).order_by(Channel.created_at).limit(1))
    ).scalar_one_or_none()
    if channel is None or not channel.analytics_enabled:
        raise ConflictError("Analytics collection is disabled")
    job_obj, created = await enqueue_video_sync(db, video, force_windows=True)
    return {"job_id": job_obj.id, "created": created, "status": job_obj.status}


@router.get("/comparisons", summary="Descriptive content grouping without recommendations")
async def comparisons(
    db: Annotated[AsyncSession, Depends(get_db)],
    group_by: Annotated[
        Literal[
            "topic_category",
            "hook_type",
            "duration_band",
            "technical_depth",
            "explanation_pattern",
        ],
        Query(),
    ] = "topic_category",
    window: AnalyticsWindowType = AnalyticsWindowType.D7,
) -> dict:
    items = await content_intelligence.descriptive_comparison(db, group_by, window)
    return {
        "group_by": group_by,
        "window": window,
        "observational_only": True,
        "items": items,
    }
