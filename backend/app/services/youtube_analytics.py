"""YouTube daily analytics orchestration and query-layer derivations."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.ids import uuid7
from app.integrations.youtube import analytics_api, data_api
from app.integrations.youtube.errors import (
    GoogleAPIError,
    QuotaExceededError,
    YouTubeVideoNotFoundError,
    YouTubeVideoOwnershipError,
)
from app.models.analytics import YouTubeVideoDailyMetric, YouTubeVideoWindowMetric
from app.models.channel import Channel
from app.models.content import ContentProject, PublishedVideo
from app.models.enums import (
    AnalyticsFreshness,
    AnalyticsWindowStatus,
    AnalyticsWindowType,
    ConnectionStatus,
    JobStatus,
)
from app.models.job import BackgroundJob
from app.services import youtube_connection

REPORTING_ZONE = ZoneInfo("America/Los_Angeles")
RECENT_REFRESH_DAYS = 14
PENDING_REPORTING_DAYS = 3
WINDOW_DAYS = (1, 3, 7, 28, 90)
WINDOW_RECENT_REFRESH_DAYS = 14

_PROVIDER_TO_COLUMN = {
    "views": "views",
    "engagedViews": "engaged_views",
    "likes": "likes",
    "comments": "comments",
    "shares": "shares",
    "subscribersGained": "subscribers_gained",
    "subscribersLost": "subscribers_lost",
    "estimatedMinutesWatched": "estimated_minutes_watched",
    "averageViewDuration": "average_view_duration",
    "averageViewPercentage": "average_view_percentage",
}
ADDITIVE_FIELDS = (
    "views",
    "engaged_views",
    "likes",
    "comments",
    "shares",
    "subscribers_gained",
    "subscribers_lost",
    "estimated_minutes_watched",
)


def provider_today() -> date:
    """Current YouTube reporting date, independent of the host timezone."""
    return utcnow().astimezone(REPORTING_ZONE).date()


def publication_reporting_date(video: PublishedVideo) -> date:
    """Publication boundary expressed in YouTube's documented Pacific day."""
    return video.published_at.astimezone(REPORTING_ZONE).date()


def _merge_ranges(ranges: Iterable[tuple[date, date]]) -> list[tuple[date, date]]:
    ordered = sorted((start, end) for start, end in ranges if start <= end)
    merged: list[tuple[date, date]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def plan_sync_ranges(
    publication_date: date,
    end_date: date,
    existing_dates: Iterable[date],
) -> list[tuple[date, date]]:
    """Plan initial backfill or recent refresh plus historical gap recovery."""
    if publication_date > end_date:
        return []
    known = set(existing_dates)
    if not known:
        return [(publication_date, end_date)]

    recent_start = max(publication_date, end_date - timedelta(days=RECENT_REFRESH_DAYS - 1))
    ranges: list[tuple[date, date]] = [(recent_start, end_date)]

    historical_end = recent_start - timedelta(days=1)
    cursor = publication_date
    first_gap: date | None = None
    while cursor <= historical_end:
        if cursor not in known:
            first_gap = cursor
            break
        cursor += timedelta(days=1)
    if first_gap is not None:
        # One broad request is cheaper and more robust than one request per
        # absent day. Existing rows in the span are harmlessly refreshed.
        ranges.append((first_gap, historical_end))
    return _merge_ranges(ranges)


async def _upsert_rows(
    session: AsyncSession,
    published_video_id: uuid.UUID,
    rows: Iterable[analytics_api.DailyMetric],
) -> int:
    synced_at = utcnow()
    count = 0
    for row in rows:
        returned = (
            row.returned_metrics
            if row.returned_metrics is not None
            else frozenset(_PROVIDER_TO_COLUMN)
        )
        values: dict[str, Any] = {
            "id": uuid7(),
            "published_video_id": published_video_id,
            "metric_date": row.metric_date,
            "synced_at": synced_at,
        }
        for provider_name in returned:
            column = _PROVIDER_TO_COLUMN.get(provider_name)
            if column:
                values[column] = getattr(row, column)

        stmt = pg_insert(YouTubeVideoDailyMetric).values(**values)
        updates = {
            key: getattr(stmt.excluded, key)
            for key in values
            if key not in {"id", "published_video_id", "metric_date"}
        }
        updates["updated_at"] = func.now()
        await session.execute(
            stmt.on_conflict_do_update(
                constraint="uq_youtube_video_daily_metric_video_date",
                set_=updates,
            )
        )
        count += 1
    await session.flush()
    return count


def _window_payload(metric: analytics_api.AggregateMetric) -> dict[str, Any]:
    returned = metric.returned_metrics or frozenset(_PROVIDER_TO_COLUMN)
    return {
        column: getattr(metric, column)
        for provider_name, column in _PROVIDER_TO_COLUMN.items()
        if provider_name in returned
    }


async def sync_windows(
    session: AsyncSession,
    video: PublishedVideo,
    access_token: str,
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Persist direct provider aggregates for complete fixed windows."""
    dates = set(
        (
            await session.execute(
                select(YouTubeVideoDailyMetric.metric_date).where(
                    YouTubeVideoDailyMetric.published_video_id == video.id
                )
            )
        ).scalars()
    )
    # Lifecycle windows are comparable only when the anchor is stable. Provider
    # latency can omit the publication day from an early response, so the first
    # returned daily row must never redefine D1.
    anchor = publication_reporting_date(video)
    existing = {
        row.window_type: row
        for row in (
            await session.execute(
                select(YouTubeVideoWindowMetric).where(
                    YouTubeVideoWindowMetric.published_video_id == video.id
                )
            )
        ).scalars()
    }
    today = provider_today()
    results: list[dict[str, Any]] = []

    for window in AnalyticsWindowType:
        end = anchor + timedelta(days=window.days - 1)
        expected = {anchor + timedelta(days=offset) for offset in range(window.days)}
        complete = expected.issubset(dates)
        row = existing.get(window.value)
        if row is None:
            row = YouTubeVideoWindowMetric(
                published_video_id=video.id,
                window_type=window.value,
                status=AnalyticsWindowStatus.PENDING.value,
                window_start_date=anchor,
                window_end_date=end,
                provider="youtube_analytics",
            )
            session.add(row)
            existing[window.value] = row
        else:
            row.window_start_date = anchor
            row.window_end_date = end

        recently_completed = end >= today - timedelta(days=WINDOW_RECENT_REFRESH_DAYS)
        should_fetch = complete and (
            force or row.status != AnalyticsWindowStatus.AVAILABLE.value or recently_completed
        )
        fetched = False
        if should_fetch:
            try:
                metric = await analytics_api.fetch_video_window_metrics(
                    access_token,
                    video.youtube_video_id,
                    anchor,
                    end,
                )
            except (GoogleAPIError, QuotaExceededError, ValueError) as exc:
                row.status = AnalyticsWindowStatus.ERROR.value
                row.last_error = str(exc)[:1000]
                row.synced_at = utcnow()
                await session.flush()
                raise
            if metric is not None:
                for field, value in _window_payload(metric).items():
                    setattr(row, field, value)
                row.status = AnalyticsWindowStatus.AVAILABLE.value
                row.provider = "youtube_analytics"
                row.synced_at = utcnow()
                row.last_error = None
                fetched = True
            else:
                row.status = AnalyticsWindowStatus.PENDING.value
                row.last_error = "Provider returned no aggregate row for a covered window."
        elif not complete and row.status != AnalyticsWindowStatus.AVAILABLE.value:
            row.status = AnalyticsWindowStatus.PENDING.value
            row.last_error = None

        results.append(
            {
                "window": window.value,
                "status": row.status,
                "start_date": anchor.isoformat(),
                "end_date": end.isoformat(),
                "coverage_complete": complete,
                "fetched": fetched,
            }
        )

    await session.flush()
    return results


async def sync_video(
    session: AsyncSession,
    published_video_id: uuid.UUID,
    *,
    force_windows: bool = False,
) -> dict[str, Any]:
    """Refresh metadata, canonical daily history and fixed provider windows."""
    video = await session.get(PublishedVideo, published_video_id)
    if video is None:
        raise YouTubeVideoNotFoundError("Published video record not found.")

    project = await session.get(ContentProject, video.project_id)
    channel = await session.get(Channel, project.channel_id) if project else None
    connection = await youtube_connection.get_connection(session)
    if channel is None or connection is None:
        from app.integrations.youtube.errors import YouTubeNotConnectedError

        raise YouTubeNotConnectedError()

    access_token = await youtube_connection.get_access_token(session, connection)
    snapshot = await data_api.fetch_video(access_token, video.youtube_video_id)
    if snapshot is None:
        raise YouTubeVideoNotFoundError()
    if not channel.youtube_channel_id or snapshot.youtube_channel_id != channel.youtube_channel_id:
        raise YouTubeVideoOwnershipError()

    # Metadata is authoritative but cumulative Data API statistics are not
    # persisted as history; Analytics API daily rows remain canonical.
    video.title = snapshot.title
    video.published_at = snapshot.published_at
    video.privacy_status = snapshot.privacy_status

    known_dates = list(
        (
            await session.execute(
                select(YouTubeVideoDailyMetric.metric_date).where(
                    YouTubeVideoDailyMetric.published_video_id == video.id
                )
            )
        ).scalars()
    )
    end_date = provider_today()
    ranges = plan_sync_ranges(publication_reporting_date(video), end_date, known_dates)

    provider_rows: dict[date, analytics_api.DailyMetric] = {}
    for start_date, range_end in ranges:
        rows = await analytics_api.fetch_daily_video_metrics(
            access_token,
            video.youtube_video_id,
            start_date,
            range_end,
        )
        for row in rows:
            if row.metric_date < start_date or row.metric_date > range_end:
                raise ValueError(
                    f"YouTube returned {row.metric_date} outside requested range "
                    f"{start_date}..{range_end}"
                )
            provider_rows[row.metric_date] = row

    upserted = await _upsert_rows(session, video.id, provider_rows.values())
    windows = await sync_windows(
        session,
        video,
        access_token,
        force=force_windows,
    )
    total_rows = await session.scalar(
        select(func.count())
        .select_from(YouTubeVideoDailyMetric)
        .where(YouTubeVideoDailyMetric.published_video_id == video.id)
    )
    latest_date = await session.scalar(
        select(func.max(YouTubeVideoDailyMetric.metric_date)).where(
            YouTubeVideoDailyMetric.published_video_id == video.id
        )
    )
    age_days = (end_date - publication_reporting_date(video)).days
    status = (
        AnalyticsFreshness.AVAILABLE
        if total_rows
        else AnalyticsFreshness.PENDING
        if age_days <= PENDING_REPORTING_DAYS
        else AnalyticsFreshness.UNAVAILABLE
    )
    return {
        "status": status.value,
        "published_video_id": str(video.id),
        "youtube_video_id": video.youtube_video_id,
        "requested_ranges": [
            {"start_date": start.isoformat(), "end_date": end.isoformat()} for start, end in ranges
        ],
        "rows_returned": len(provider_rows),
        "rows_upserted": upserted,
        "total_rows": int(total_rows or 0),
        "latest_metric_date": latest_date.isoformat() if latest_date else None,
        "windows": windows,
        "data_api_public_statistics": {
            "views": snapshot.view_count,
            "likes": snapshot.like_count,
            "comments": snapshot.comment_count,
        },
    }


async def eligible_videos(session: AsyncSession) -> list[PublishedVideo]:
    return list(
        (
            await session.execute(select(PublishedVideo).order_by(PublishedVideo.published_at))
        ).scalars()
    )


def _sum_known(rows: list[YouTubeVideoDailyMetric], field: str) -> int | Decimal | None:
    values = [getattr(row, field) for row in rows if getattr(row, field) is not None]
    # A partial sum looks exact in JSON and is therefore more dangerous than
    # returning unavailable. Missing provider values never become implicit 0s.
    return sum(values) if rows and len(values) == len(rows) else None


def _weighted_average(rows: list[YouTubeVideoDailyMetric], field: str) -> Decimal | None:
    """Weight Shorts playback averages by engaged views when fully available."""
    pairs = [
        (Decimal(str(getattr(row, field))), row.engaged_views)
        for row in rows
        if getattr(row, field) is not None and row.engaged_views is not None
    ]
    if len(pairs) != len(rows):
        return None
    total_weight = sum(weight for _, weight in pairs)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in pairs) / Decimal(total_weight)


def aggregate_rows(rows: list[YouTubeVideoDailyMetric]) -> dict[str, Any]:
    result: dict[str, Any] = {field: _sum_known(rows, field) for field in ADDITIVE_FIELDS}
    result["average_view_duration"] = _weighted_average(rows, "average_view_duration")
    result["average_view_percentage"] = _weighted_average(rows, "average_view_percentage")
    return result


def derive_windows(
    rows: list[YouTubeVideoDailyMetric],
    publication_date: date | None = None,
) -> list[dict[str, Any]]:
    """Project diagnostic windows from a stable publication reporting date."""
    if not rows:
        return [
            {
                "window": f"D{days}",
                "status": AnalyticsFreshness.PENDING.value,
                "start_date": None,
                "end_date": None,
                "days_present": 0,
                "days_expected": days,
                "complete": False,
                **{
                    field: None
                    for field in (
                        *ADDITIVE_FIELDS,
                        "average_view_duration",
                        "average_view_percentage",
                    )
                },
            }
            for days in WINDOW_DAYS
        ]

    ordered = sorted(rows, key=lambda row: row.metric_date)
    anchor = publication_date or ordered[0].metric_date
    latest = ordered[-1].metric_date
    windows: list[dict[str, Any]] = []
    for days in WINDOW_DAYS:
        end = anchor + timedelta(days=days - 1)
        selected = [row for row in ordered if anchor <= row.metric_date <= end]
        expected_dates = {anchor + timedelta(days=offset) for offset in range(days)}
        present_dates = {row.metric_date for row in selected}
        mature = latest >= end
        complete = mature and expected_dates.issubset(present_dates)
        windows.append(
            {
                "window": f"D{days}",
                "status": (
                    AnalyticsFreshness.AVAILABLE.value
                    if mature
                    else AnalyticsFreshness.PENDING.value
                ),
                "start_date": anchor,
                "end_date": end,
                "days_present": len(present_dates),
                "days_expected": days,
                "complete": complete,
                **aggregate_rows(selected),
            }
        )
    return windows


async def video_freshness(
    session: AsyncSession,
    video: PublishedVideo,
    *,
    channel: Channel | None = None,
) -> dict[str, Any]:
    channel = (
        channel
        or (
            await session.execute(select(Channel).order_by(Channel.created_at).limit(1))
        ).scalar_one_or_none()
    )
    connection = await youtube_connection.get_connection(session)

    jobs = list(
        (
            await session.execute(
                select(BackgroundJob)
                .where(
                    BackgroundJob.project_id == video.project_id,
                    BackgroundJob.job_type == "analytics.sync_video",
                )
                .order_by(BackgroundJob.created_at.desc())
            )
        ).scalars()
    )
    latest_job = jobs[0] if jobs else None
    successful = next(
        (
            job
            for job in jobs
            if job.status == JobStatus.SUCCEEDED
            and not (job.result or {}).get("skipped")
            and (job.result or {}).get("status")
            not in {
                AnalyticsFreshness.AUTH_EXPIRED.value,
                AnalyticsFreshness.ERROR.value,
            }
        ),
        None,
    )
    row_count = await session.scalar(
        select(func.count())
        .select_from(YouTubeVideoDailyMetric)
        .where(YouTubeVideoDailyMetric.published_video_id == video.id)
    )
    latest_metric_date = await session.scalar(
        select(func.max(YouTubeVideoDailyMetric.metric_date)).where(
            YouTubeVideoDailyMetric.published_video_id == video.id
        )
    )

    reason = "not_synced"
    if connection and connection.status in {
        ConnectionStatus.EXPIRED,
        ConnectionStatus.REVOKED,
    }:
        status = AnalyticsFreshness.AUTH_EXPIRED
        reason = "oauth_reconnect_required"
    elif latest_job and latest_job.status in JobStatus.active():
        status = AnalyticsFreshness.PENDING
        reason = "sync_queued_or_running"
    elif latest_job and (latest_job.result or {}).get("status") == "AUTH_EXPIRED":
        status = AnalyticsFreshness.AUTH_EXPIRED
        reason = "oauth_reconnect_required"
    elif latest_job and (
        latest_job.status == JobStatus.FAILED or (latest_job.result or {}).get("status") == "ERROR"
    ):
        status = AnalyticsFreshness.ERROR
        reason = (
            latest_job.error_message or (latest_job.result or {}).get("reason") or "sync_failed"
        )
    elif row_count:
        status = AnalyticsFreshness.AVAILABLE
        reason = (
            "daily_metrics_available"
            if channel is None or channel.analytics_enabled
            else "daily_metrics_available_collection_disabled"
        )
    elif channel is not None and not channel.analytics_enabled:
        status = AnalyticsFreshness.UNAVAILABLE
        reason = "analytics_disabled"
    elif successful:
        age_days = (provider_today() - publication_reporting_date(video)).days
        status = (
            AnalyticsFreshness.PENDING
            if age_days <= PENDING_REPORTING_DAYS
            else AnalyticsFreshness.UNAVAILABLE
        )
        reason = (
            "provider_data_pending" if status == AnalyticsFreshness.PENDING else "no_provider_data"
        )
    else:
        status = AnalyticsFreshness.PENDING

    return {
        "status": status.value,
        "reason": reason,
        "latest_metric_date": latest_metric_date,
        "latest_successful_sync": successful.finished_at if successful else None,
        "latest_job_id": latest_job.id if latest_job else None,
        "latest_job_status": latest_job.status if latest_job else None,
        "collection_enabled": bool(channel and channel.analytics_enabled),
    }
