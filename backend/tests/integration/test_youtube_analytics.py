"""PostgreSQL-backed Phase 3 analytics ingestion and API tests."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core import crypto
from app.core.clock import utcnow
from app.db.session import get_sessionmaker
from app.integrations.youtube import analytics_api, data_api
from app.integrations.youtube.errors import (
    GoogleAuthError,
    RateLimitedError,
    TransientGoogleError,
    YouTubeVideoOwnershipError,
)
from app.jobs import queue
from app.jobs.registry import get_definition
from app.jobs.runner import run_job
from app.models.analytics import YouTubeVideoDailyMetric, YouTubeVideoWindowMetric
from app.models.channel import Channel
from app.models.content import ContentProject, PublishedVideo
from app.models.enums import (
    AnalyticsWindowStatus,
    ConnectionStatus,
    JobStatus,
    ProjectStatus,
)
from app.models.youtube import YouTubeConnection
from app.services import youtube_analytics

VIDEO_ID = "Q-CJWCG90qI"
CHANNEL_ID = "UC_systemdecoded"
PUBLISHED_AT = datetime(2026, 9, 8, 8, 3, 27, tzinfo=UTC)
TODAY = date(2026, 9, 20)


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "SECRETS_KEY", crypto.generate_key())
    monkeypatch.setattr(settings, "YOUTUBE_API_ENABLED", True)
    monkeypatch.setattr(youtube_analytics, "provider_today", lambda: TODAY)
    crypto.reset_cipher_cache()
    yield
    crypto.reset_cipher_cache()


async def _setup(session, *, enabled: bool = True, connected: bool = True):
    channel = (await session.execute(select(Channel))).scalars().one()
    channel.youtube_channel_id = CHANNEL_ID
    channel.connection_status = ConnectionStatus.ACTIVE if connected else ConnectionStatus.EXPIRED
    channel.analytics_enabled = enabled
    project = ContentProject(
        channel_id=channel.id,
        topic="Analytics integration test",
        status=ProjectStatus.PUBLISHED,
    )
    session.add(project)
    await session.flush()
    video = PublishedVideo(
        project_id=project.id,
        youtube_video_id=VIDEO_ID,
        title="Nobody sent you that authenticator code",
        published_at=PUBLISHED_AT,
        privacy_status="public",
        reconciliation_method="manual_confirmation_verified",
    )
    session.add(video)
    connection = YouTubeConnection(
        channel_id=channel.id,
        access_token_enc=crypto.encrypt("access-token"),
        refresh_token_enc=crypto.encrypt("refresh-token"),
        access_token_expires_at=utcnow() + timedelta(hours=1),
        status=ConnectionStatus.ACTIVE if connected else ConnectionStatus.EXPIRED,
        scopes=[],
    )
    session.add(connection)
    await session.commit()
    return channel, project, video, connection


def _snapshot(channel_id: str = CHANNEL_ID) -> data_api.VideoSnapshot:
    return data_api.VideoSnapshot(
        youtube_video_id=VIDEO_ID,
        youtube_channel_id=channel_id,
        channel_title="SystemDecoded",
        title="Nobody sent you that authenticator code",
        published_at=PUBLISHED_AT,
        privacy_status="public",
        description="Verified publication",
        duration="PT31S",
        view_count=100,
        like_count=5,
        comment_count=2,
    )


def _daily(day: int, *, views: int = 10, likes: int | None = 1):
    return analytics_api.DailyMetric(
        metric_date=date(2026, 9, day),
        views=views,
        engaged_views=views - 2,
        likes=likes,
        comments=1,
        shares=1,
        subscribers_gained=1,
        subscribers_lost=0,
        estimated_minutes_watched=Decimal("4.5"),
        average_view_duration=Decimal("20"),
        average_view_percentage=Decimal("64.5"),
        returned_metrics=frozenset(analytics_api.METRICS),
    )


def _mock_provider(
    monkeypatch,
    rows,
    *,
    channel_id: str = CHANNEL_ID,
    window_values=None,
):
    async def fetch_video(access_token, youtube_video_id):
        assert access_token == "access-token"
        assert youtube_video_id == VIDEO_ID
        return _snapshot(channel_id)

    async def fetch_metrics(access_token, youtube_video_id, start_date, end_date):
        return [row for row in rows if start_date <= row.metric_date <= end_date]

    async def fetch_window(access_token, youtube_video_id, start_date, end_date):
        if window_values and (start_date, end_date) in window_values:
            return window_values[(start_date, end_date)]
        selected = [row for row in rows if start_date <= row.metric_date <= end_date]
        if not selected:
            return None
        return analytics_api.AggregateMetric(
            views=sum(row.views or 0 for row in selected),
            engaged_views=sum(row.engaged_views or 0 for row in selected),
            likes=sum(row.likes or 0 for row in selected),
            comments=sum(row.comments or 0 for row in selected),
            shares=sum(row.shares or 0 for row in selected),
            subscribers_gained=sum(row.subscribers_gained or 0 for row in selected),
            subscribers_lost=sum(row.subscribers_lost or 0 for row in selected),
            estimated_minutes_watched=sum(
                (row.estimated_minutes_watched or Decimal("0")) for row in selected
            ),
            average_view_duration=Decimal("20"),
            average_view_percentage=Decimal("64.5"),
            returned_metrics=frozenset(analytics_api.METRICS),
        )

    monkeypatch.setattr(data_api, "fetch_video", fetch_video)
    monkeypatch.setattr(analytics_api, "fetch_daily_video_metrics", fetch_metrics)
    monkeypatch.setattr(analytics_api, "fetch_video_window_metrics", fetch_window)


async def test_initial_backfill_and_partial_availability(session, monkeypatch) -> None:
    _, _, video, _ = await _setup(session)
    _mock_provider(monkeypatch, [_daily(8), _daily(9)])

    result = await youtube_analytics.sync_video(session, video.id)

    assert result["requested_ranges"] == [{"start_date": "2026-09-08", "end_date": "2026-09-20"}]
    assert result["rows_returned"] == 2
    assert result["total_rows"] == 2
    assert result["latest_metric_date"] == "2026-09-09"
    dates = list(
        (
            await session.execute(
                select(YouTubeVideoDailyMetric.metric_date).order_by(
                    YouTubeVideoDailyMetric.metric_date
                )
            )
        ).scalars()
    )
    assert dates == [date(2026, 9, 8), date(2026, 9, 9)]


async def test_late_first_provider_day_does_not_redefine_d1(session, monkeypatch) -> None:
    _, _, video, _ = await _setup(session)
    _mock_provider(monkeypatch, [_daily(9), _daily(10)])

    await youtube_analytics.sync_video(session, video.id)

    rows = {
        row.window_type: row
        for row in (await session.execute(select(YouTubeVideoWindowMetric))).scalars()
    }
    assert rows["D1"].window_start_date == date(2026, 9, 8)
    assert rows["D1"].window_end_date == date(2026, 9, 8)
    assert rows["D1"].status == AnalyticsWindowStatus.PENDING
    assert rows["D3"].window_start_date == date(2026, 9, 8)
    assert rows["D3"].status == AnalyticsWindowStatus.PENDING


async def test_repeated_sync_is_idempotent_and_updates_existing_day(session, monkeypatch) -> None:
    _, _, video, _ = await _setup(session)
    _mock_provider(monkeypatch, [_daily(8, views=10)])
    await youtube_analytics.sync_video(session, video.id)
    _mock_provider(monkeypatch, [_daily(8, views=25)])
    await youtube_analytics.sync_video(session, video.id)

    assert await session.scalar(select(func.count()).select_from(YouTubeVideoDailyMetric)) == 1
    row = (await session.execute(select(YouTubeVideoDailyMetric))).scalars().one()
    assert row.views == 25


async def test_authoritative_windows_are_idempotent_and_update_revisions(
    session, monkeypatch
) -> None:
    _, _, video, _ = await _setup(session)
    rows = [
        analytics_api.DailyMetric(
            metric_date=date(2026, 9, day),
            views=12 if day == 8 else 1,
            engaged_views=6 if day == 8 else 1,
            estimated_minutes_watched=Decimal("1") if day == 8 else Decimal("0"),
            average_view_duration=Decimal("19") if day == 8 else Decimal("10"),
            returned_metrics=frozenset(analytics_api.METRICS),
        )
        for day in range(8, 15)
    ]
    ranges = {
        (date(2026, 9, 8), date(2026, 9, 8)): analytics_api.AggregateMetric(
            views=12,
            engaged_views=6,
            estimated_minutes_watched=Decimal("1"),
            average_view_duration=Decimal("19"),
            returned_metrics=frozenset(analytics_api.METRICS),
        ),
        (date(2026, 9, 8), date(2026, 9, 10)): analytics_api.AggregateMetric(
            views=14,
            engaged_views=8,
            estimated_minutes_watched=Decimal("2"),
            average_view_duration=Decimal("18"),
            returned_metrics=frozenset(analytics_api.METRICS),
        ),
        (date(2026, 9, 8), date(2026, 9, 14)): analytics_api.AggregateMetric(
            views=18,
            engaged_views=10,
            estimated_minutes_watched=Decimal("2"),
            average_view_duration=Decimal("16"),
            returned_metrics=frozenset(analytics_api.METRICS),
        ),
    }
    _mock_provider(monkeypatch, rows, window_values=ranges)
    await youtube_analytics.sync_video(session, video.id)
    await youtube_analytics.sync_video(session, video.id)

    stored = list(
        (
            await session.execute(
                select(YouTubeVideoWindowMetric).order_by(YouTubeVideoWindowMetric.window_end_date)
            )
        ).scalars()
    )
    assert len(stored) == 5
    by_type = {row.window_type: row for row in stored}
    assert by_type["D1"].status == AnalyticsWindowStatus.AVAILABLE
    assert by_type["D3"].status == AnalyticsWindowStatus.AVAILABLE
    assert by_type["D7"].status == AnalyticsWindowStatus.AVAILABLE
    assert by_type["D28"].status == AnalyticsWindowStatus.PENDING
    assert by_type["D90"].status == AnalyticsWindowStatus.PENDING
    # Daily integers sum to 1, but the provider's dimensionless D7 value is 2.
    assert sum(row.estimated_minutes_watched or 0 for row in rows) == Decimal("1")
    assert by_type["D7"].estimated_minutes_watched == Decimal("2")
    assert by_type["D7"].average_view_duration == Decimal("16")

    ranges[(date(2026, 9, 8), date(2026, 9, 14))] = analytics_api.AggregateMetric(
        views=19,
        engaged_views=11,
        estimated_minutes_watched=Decimal("3"),
        average_view_duration=Decimal("17"),
        returned_metrics=frozenset(analytics_api.METRICS),
    )
    await youtube_analytics.sync_video(session, video.id, force_windows=True)
    await session.refresh(by_type["D7"])
    assert by_type["D7"].views == 19
    assert by_type["D7"].estimated_minutes_watched == Decimal("3")
    assert await session.scalar(select(func.count()).select_from(YouTubeVideoWindowMetric)) == 5


async def test_missing_metric_remains_null_not_zero(session, monkeypatch) -> None:
    _, _, video, _ = await _setup(session)
    row = analytics_api.DailyMetric(
        metric_date=date(2026, 9, 8),
        views=3,
        returned_metrics=frozenset({"views"}),
    )
    _mock_provider(monkeypatch, [row])
    await youtube_analytics.sync_video(session, video.id)
    stored = (await session.execute(select(YouTubeVideoDailyMetric))).scalars().one()
    assert stored.views == 3
    assert stored.likes is None
    assert stored.engaged_views is None


async def test_no_data_yet_creates_no_fake_rows(session, monkeypatch) -> None:
    _, _, video, _ = await _setup(session)
    _mock_provider(monkeypatch, [])
    result = await youtube_analytics.sync_video(session, video.id)
    assert result["status"] == "UNAVAILABLE"
    assert result["rows_returned"] == 0
    assert await session.scalar(select(func.count()).select_from(YouTubeVideoDailyMetric)) == 0


async def test_video_must_belong_to_connected_channel(session, monkeypatch) -> None:
    _, _, video, _ = await _setup(session)
    _mock_provider(monkeypatch, [], channel_id="UC_someone_else")
    with pytest.raises(YouTubeVideoOwnershipError):
        await youtube_analytics.sync_video(session, video.id)


async def test_expired_oauth_surfaces_auth_expired_freshness(session) -> None:
    _, _, video, _ = await _setup(session, connected=False)
    result = await youtube_analytics.video_freshness(session, video)
    assert result["status"] == "AUTH_EXPIRED"


async def test_daily_unique_constraint_and_published_video_fk(session) -> None:
    _, _, video, _ = await _setup(session)
    now = utcnow()
    session.add_all(
        [
            YouTubeVideoDailyMetric(
                published_video_id=video.id,
                metric_date=date(2026, 9, 8),
                synced_at=now,
            ),
            YouTubeVideoDailyMetric(
                published_video_id=video.id,
                metric_date=date(2026, 9, 8),
                synced_at=now,
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    session.add(
        YouTubeVideoDailyMetric(
            published_video_id=uuid.uuid4(),
            metric_date=date(2026, 9, 8),
            synced_at=now,
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_window_unique_constraint_and_published_video_fk(session) -> None:
    _, _, video, _ = await _setup(session)
    values = {
        "published_video_id": video.id,
        "window_type": "D7",
        "status": AnalyticsWindowStatus.PENDING,
        "window_start_date": date(2026, 9, 8),
        "window_end_date": date(2026, 9, 14),
        "provider": "youtube_analytics",
    }
    session.add_all([YouTubeVideoWindowMetric(**values), YouTubeVideoWindowMetric(**values)])
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    session.add(
        YouTubeVideoWindowMetric(
            **{
                **values,
                "published_video_id": uuid.uuid4(),
            }
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_manual_sync_honors_kill_switch(client, session) -> None:
    await _setup(session, enabled=False)
    response = await client.post("/api/v1/analytics/sync", json={})
    assert response.status_code == 409
    assert "disabled" in response.json()["error"]["message"].lower()


async def test_manual_video_sync_prevents_active_duplicate(client, session) -> None:
    _, _, video, _ = await _setup(session)
    first = await client.post("/api/v1/analytics/sync", json={"published_video_id": str(video.id)})
    second = await client.post("/api/v1/analytics/sync", json={"published_video_id": str(video.id)})
    assert first.status_code == second.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["job_id"] == second.json()["job_id"]


async def test_analytics_read_apis_return_daily_windows_and_freshness(
    client, session, monkeypatch
) -> None:
    _, _, video, _ = await _setup(session)
    _mock_provider(monkeypatch, [_daily(day) for day in range(8, 15)])
    await youtube_analytics.sync_video(session, video.id)
    await session.commit()

    listing = (await client.get("/api/v1/analytics/videos")).json()
    assert listing["total"] == 1
    assert listing["items"][0]["metrics"]["views"] == 70
    daily = (await client.get(f"/api/v1/analytics/videos/{video.id}/daily")).json()
    assert daily["total"] == 7
    windows = (await client.get(f"/api/v1/analytics/videos/{video.id}/windows")).json()
    d7 = next(item for item in windows["items"] if item["window"] == "D7")
    assert d7["views"] == 70
    assert d7["complete"] is True
    freshness = (await client.get(f"/api/v1/analytics/videos/{video.id}/freshness")).json()
    assert freshness["status"] == "AVAILABLE"


async def test_postgresql_queue_worker_runs_real_sync_pipeline(session, monkeypatch) -> None:
    _, _, video, _ = await _setup(session)
    _mock_provider(monkeypatch, [_daily(8)])
    job_obj, _ = await queue.enqueue(
        session,
        "analytics.sync_video",
        {"published_video_id": str(video.id)},
        project_id=video.project_id,
    )
    await session.commit()

    async with get_sessionmaker()() as claim_session:
        claimed = await queue.claim(claim_session, "analytics-test-worker")
        await claim_session.commit()
    assert claimed is not None
    assert await run_job(job_obj.id, "analytics-test-worker") == JobStatus.SUCCEEDED

    async with get_sessionmaker()() as verify:
        stored_job = await verify.get(type(job_obj), job_obj.id)
        assert stored_job.result["rows_upserted"] == 1
        assert await verify.scalar(select(func.count()).select_from(YouTubeVideoDailyMetric)) == 1


def test_analytics_job_retries_rate_limits_and_transient_failures() -> None:
    definition = get_definition("analytics.sync_video")
    assert definition.is_retryable(analytics_api.TransientGoogleError("temporary"))
    assert definition.is_retryable(analytics_api.classify_api_error(429, {}))


async def test_provider_401_marks_oauth_expired(session, monkeypatch) -> None:
    channel, _, video, connection = await _setup(session)

    async def rejected(*_args, **_kwargs):
        raise GoogleAuthError("expired token", status=401)

    monkeypatch.setattr(youtube_analytics, "sync_video", rejected)
    job_obj, _ = await queue.enqueue(
        session,
        "analytics.sync_video",
        {"published_video_id": str(video.id)},
        project_id=video.project_id,
    )
    await session.commit()
    async with get_sessionmaker()() as claim_session:
        await queue.claim(claim_session, "analytics-401-worker")
        await claim_session.commit()

    assert await run_job(job_obj.id, "analytics-401-worker") == JobStatus.SUCCEEDED
    async with get_sessionmaker()() as verify:
        stored = await verify.get(type(job_obj), job_obj.id)
        stored_channel = await verify.get(Channel, channel.id)
        stored_connection = await verify.get(YouTubeConnection, connection.id)
        assert stored.result["status"] == "AUTH_EXPIRED"
        assert stored_channel.connection_status == ConnectionStatus.EXPIRED
        assert stored_connection.status == ConnectionStatus.EXPIRED


async def test_provider_403_surfaces_error_without_expiring_token(session, monkeypatch) -> None:
    _, _, video, connection = await _setup(session)

    async def forbidden(*_args, **_kwargs):
        raise GoogleAuthError("permission denied", status=403)

    monkeypatch.setattr(youtube_analytics, "sync_video", forbidden)
    job_obj, _ = await queue.enqueue(
        session,
        "analytics.sync_video",
        {"published_video_id": str(video.id)},
        project_id=video.project_id,
    )
    await session.commit()
    async with get_sessionmaker()() as claim_session:
        await queue.claim(claim_session, "analytics-403-worker")
        await claim_session.commit()

    assert await run_job(job_obj.id, "analytics-403-worker") == JobStatus.SUCCEEDED
    async with get_sessionmaker()() as verify:
        stored = await verify.get(type(job_obj), job_obj.id)
        stored_connection = await verify.get(YouTubeConnection, connection.id)
        assert stored.result["status"] == "ERROR"
        assert stored.result["provider_status"] == 403
        assert stored_connection.status == ConnectionStatus.ACTIVE


@pytest.mark.parametrize(
    "provider_error",
    [RateLimitedError("slow down"), TransientGoogleError("provider unavailable")],
    ids=["429", "5xx"],
)
async def test_retryable_provider_failure_requeues_analytics_job(
    session, monkeypatch, provider_error
) -> None:
    _, _, video, _ = await _setup(session)

    async def rejected(*_args, **_kwargs):
        raise provider_error

    monkeypatch.setattr(youtube_analytics, "sync_video", rejected)
    job_obj, _ = await queue.enqueue(
        session,
        "analytics.sync_video",
        {"published_video_id": str(video.id)},
        project_id=video.project_id,
    )
    await session.commit()
    async with get_sessionmaker()() as claim_session:
        await queue.claim(claim_session, "analytics-retry-worker")
        await claim_session.commit()

    assert await run_job(job_obj.id, "analytics-retry-worker") == JobStatus.QUEUED
    async with get_sessionmaker()() as verify:
        stored = await verify.get(type(job_obj), job_obj.id)
        assert stored.attempt == 1
        assert stored.error_class == type(provider_error).__name__
