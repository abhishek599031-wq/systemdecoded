"""Background jobs for canonical YouTube daily analytics ingestion."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.core.clock import utcnow
from app.core.errors import RetryableError
from app.integrations.youtube.errors import (
    GoogleAPIError,
    GoogleAuthError,
    InvalidGrantError,
    QuotaExceededError,
    RateLimitedError,
    TransientGoogleError,
)
from app.jobs import queue
from app.jobs.context import JobContext
from app.jobs.registry import job
from app.models.channel import Channel
from app.models.content import PublishedVideo
from app.models.enums import AnalyticsFreshness, ConnectionStatus, JobStatus
from app.models.job import BackgroundJob
from app.services import youtube_analytics, youtube_connection

ANALYTICS_RETRYABLE = (RetryableError, TransientGoogleError, RateLimitedError)


async def enqueue_video_sync(
    session,
    published_video,
    *,
    force_windows: bool = False,
) -> tuple[BackgroundJob, bool]:
    """Enqueue at most one active sync per project, race-safe within an hour."""
    existing = (
        await session.execute(
            select(BackgroundJob)
            .where(
                BackgroundJob.project_id == published_video.project_id,
                BackgroundJob.job_type == "analytics.sync_video",
                BackgroundJob.status.in_(JobStatus.active()),
            )
            .order_by(BackgroundJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    hour_slot = int(utcnow().timestamp()) // 3600 * 3600
    return await queue.enqueue(
        session,
        "analytics.sync_video",
        {
            "published_video_id": str(published_video.id),
            "force_windows": force_windows,
        },
        idempotency_key=(
            f"analytics:video:{published_video.id}:{hour_slot}"
            f"{':force' if force_windows else ''}"
        ),
        project_id=published_video.project_id,
    )


@job(
    "analytics.sync_video",
    max_attempts=3,
    timeout_seconds=180,
    retry_on=ANALYTICS_RETRYABLE,
    default_priority=2,
    description="Refresh one published video's canonical daily YouTube metrics.",
)
async def sync_video(ctx: JobContext) -> dict[str, Any]:
    channel = (
        await ctx.session.execute(select(Channel).order_by(Channel.created_at).limit(1))
    ).scalar_one_or_none()
    if channel is None or not channel.analytics_enabled:
        return {"skipped": "analytics_disabled", "status": "UNAVAILABLE"}

    try:
        published_video_id = uuid.UUID(str(ctx.payload["published_video_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("analytics.sync_video requires a valid published_video_id") from exc

    try:
        result = await youtube_analytics.sync_video(
            ctx.session,
            published_video_id,
            force_windows=bool(ctx.payload.get("force_windows", False)),
        )
        from app.services import content_intelligence

        video = await ctx.session.get(PublishedVideo, published_video_id)
        if video is not None:
            profile = await content_intelligence.refresh_profile(ctx.session, video.project_id)
            result["feature_profile_id"] = str(profile.id)
            from app.services import experiments

            await experiments.mark_measurement_progress(ctx.session, video.project_id)
        return result
    except InvalidGrantError as exc:
        return {
            "status": AnalyticsFreshness.AUTH_EXPIRED.value,
            "reason": str(exc)[:500],
        }
    except GoogleAuthError as exc:
        if exc.status == 401:
            connection = await youtube_connection.get_connection(ctx.session)
            if connection is not None:
                connection.status = ConnectionStatus.EXPIRED
                connection.last_error = str(exc)[:2000]
                connection.last_error_at = utcnow()
            channel.connection_status = ConnectionStatus.EXPIRED
            return {
                "status": AnalyticsFreshness.AUTH_EXPIRED.value,
                "reason": str(exc)[:500],
            }
        return {
            "status": AnalyticsFreshness.ERROR.value,
            "reason": str(exc)[:500],
            "provider_status": exc.status,
        }
    except (QuotaExceededError, GoogleAPIError, ValueError) as exc:
        return {
            "status": AnalyticsFreshness.ERROR.value,
            "reason": str(exc)[:500],
        }


@job(
    "analytics.sync_all",
    max_attempts=2,
    timeout_seconds=60,
    retry_on=ANALYTICS_RETRYABLE,
    default_priority=1,
    description="Fan out daily analytics sync jobs for every published video.",
)
async def sync_all(ctx: JobContext) -> dict[str, Any]:
    channel = (
        await ctx.session.execute(select(Channel).order_by(Channel.created_at).limit(1))
    ).scalar_one_or_none()
    if channel is None or not channel.analytics_enabled:
        return {"skipped": "analytics_disabled", "enqueued": 0}

    videos = await youtube_analytics.eligible_videos(ctx.session)
    enqueued = 0
    deduplicated = 0
    job_ids: list[str] = []
    for video in videos:
        job_obj, created = await enqueue_video_sync(ctx.session, video)
        job_ids.append(str(job_obj.id))
        if created:
            enqueued += 1
        else:
            deduplicated += 1
    return {
        "eligible": len(videos),
        "enqueued": enqueued,
        "deduplicated": deduplicated,
        "job_ids": job_ids,
    }
