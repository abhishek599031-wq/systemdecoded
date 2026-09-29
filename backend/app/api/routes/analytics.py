"""Read APIs and background-sync trigger for YouTube analytics."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.db.session import get_db
from app.jobs import queue
from app.jobs.tasks.analytics import enqueue_video_sync
from app.models.analytics import YouTubeVideoDailyMetric
from app.models.channel import Channel
from app.models.content import PublishedVideo
from app.schemas.analytics import AnalyticsSyncRequest
from app.services import youtube_analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


async def _channel(db: AsyncSession) -> Channel:
    channel = (
        await db.execute(select(Channel).order_by(Channel.created_at).limit(1))
    ).scalar_one_or_none()
    if channel is None:
        raise NotFoundError("Channel not found")
    return channel


async def _video(db: AsyncSession, published_video_id: uuid.UUID) -> PublishedVideo:
    video = await db.get(PublishedVideo, published_video_id)
    if video is None:
        raise NotFoundError(f"Published video {published_video_id} not found")
    return video


async def _rows(db: AsyncSession, published_video_id: uuid.UUID) -> list[YouTubeVideoDailyMetric]:
    return list(
        (
            await db.execute(
                select(YouTubeVideoDailyMetric)
                .where(YouTubeVideoDailyMetric.published_video_id == published_video_id)
                .order_by(YouTubeVideoDailyMetric.metric_date)
            )
        ).scalars()
    )


def _json_number(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


def _metrics_json(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_number(value) for key, value in metrics.items()}


def _daily_json(row: YouTubeVideoDailyMetric) -> dict[str, Any]:
    return {
        "metric_date": row.metric_date,
        **_metrics_json(
            {field: getattr(row, field) for field in youtube_analytics.ADDITIVE_FIELDS}
        ),
        "average_view_duration": _json_number(row.average_view_duration),
        "average_view_percentage": _json_number(row.average_view_percentage),
        "synced_at": row.synced_at,
    }


@router.get("/overview", summary="Tracked-channel analytics overview")
async def overview(db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, Any]:
    channel = await _channel(db)
    videos = await youtube_analytics.eligible_videos(db)
    all_rows = list((await db.execute(select(YouTubeVideoDailyMetric))).scalars())
    latest = max((row.synced_at for row in all_rows), default=None)
    return {
        "analytics_enabled": channel.analytics_enabled,
        "published_videos": len(videos),
        "videos_with_analytics": len({row.published_video_id for row in all_rows}),
        "metrics": _metrics_json(youtube_analytics.aggregate_rows(all_rows)),
        "latest_successful_sync": latest,
        "scope": "tracked_published_videos",
    }


@router.get("/videos", summary="Published videos with analytics summaries")
async def list_videos(db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, Any]:
    channel = await _channel(db)
    items = []
    for video in reversed(await youtube_analytics.eligible_videos(db)):
        rows = await _rows(db, video.id)
        freshness = await youtube_analytics.video_freshness(db, video, channel=channel)
        items.append(
            {
                "published_video_id": video.id,
                "project_id": video.project_id,
                "youtube_video_id": video.youtube_video_id,
                "title": video.title,
                "published_at": video.published_at,
                "privacy_status": video.privacy_status,
                "metrics": _metrics_json(youtube_analytics.aggregate_rows(rows)),
                "freshness": freshness,
            }
        )
    return {"items": items, "total": len(items)}


@router.get("/videos/{published_video_id}/daily", summary="One video's daily metrics")
async def daily_metrics(
    published_video_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    video = await _video(db, published_video_id)
    rows = await _rows(db, video.id)
    return {
        "published_video_id": video.id,
        "youtube_video_id": video.youtube_video_id,
        "items": [_daily_json(row) for row in rows],
        "total": len(rows),
    }


@router.get("/videos/{published_video_id}/windows", summary="Derived D1-D90 windows")
async def performance_windows(
    published_video_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    video = await _video(db, published_video_id)
    windows = youtube_analytics.derive_windows(await _rows(db, video.id))
    return {
        "published_video_id": video.id,
        "anchor_semantics": "first_api_returned_reporting_day",
        "average_weight": "engaged_views",
        "items": [_metrics_json(window) for window in windows],
    }


@router.get("/videos/{published_video_id}/freshness", summary="Analytics freshness")
async def freshness(
    published_video_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    video = await _video(db, published_video_id)
    return await youtube_analytics.video_freshness(db, video)


@router.post("/sync", summary="Queue analytics sync work")
async def sync(
    body: AnalyticsSyncRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    channel = await _channel(db)
    if not channel.analytics_enabled:
        raise ConflictError(
            "Analytics collection is disabled. Enable the channel analytics kill switch first."
        )

    if body.published_video_id is not None:
        video = await _video(db, body.published_video_id)
        job_obj, created = await enqueue_video_sync(db, video)
    else:
        hour_slot = int(utcnow().timestamp()) // 3600 * 3600
        job_obj, created = await queue.enqueue(
            db,
            "analytics.sync_all",
            {"manual": True},
            idempotency_key=f"analytics:all:{hour_slot}",
        )
    return {"job_id": job_obj.id, "created": created, "status": job_obj.status}
