"""Database-backed manual publication reconciliation tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core import crypto
from app.core.clock import utcnow
from app.integrations.youtube import data_api
from app.models.channel import Channel
from app.models.content import (
    ContentProject,
    ProjectTransition,
    PublishedVideo,
    PublishingJob,
)
from app.models.enums import ConnectionStatus, ProjectStatus, PublishState
from app.models.youtube import YouTubeConnection

VIDEO_ID = "Ab_cd-12345"
OTHER_VIDEO_ID = "Zy_xw-98765"
CHANNEL_ID = "UC_systemdecoded"
PUBLISHED_AT = datetime(2026, 9, 10, 12, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "SECRETS_KEY", crypto.generate_key())
    monkeypatch.setattr(settings, "YOUTUBE_API_ENABLED", True)
    crypto.reset_cipher_cache()
    yield
    crypto.reset_cipher_cache()


def _video(
    *, video_id: str = VIDEO_ID, channel_id: str = CHANNEL_ID
) -> data_api.VideoSnapshot:
    return data_api.VideoSnapshot(
        youtube_video_id=video_id,
        youtube_channel_id=channel_id,
        channel_title="SystemDecoded",
        title="Nobody sent you that authenticator code",
        published_at=PUBLISHED_AT,
        privacy_status="public",
    )


async def _setup(session, *, with_job: bool = True) -> tuple[ContentProject, YouTubeConnection]:
    channel = (await session.execute(select(Channel))).scalars().one()
    channel.youtube_channel_id = CHANNEL_ID
    channel.connection_status = ConnectionStatus.ACTIVE

    project = ContentProject(
        channel_id=channel.id,
        topic="Manual publication reconciliation test",
        status=ProjectStatus.AWAITING_HUMAN_UPLOAD,
    )
    session.add(project)
    await session.flush()

    if with_job:
        session.add(
            PublishingJob(
                project_id=project.id,
                state=PublishState.AWAITING_HUMAN_UPLOAD,
                idempotency_key=f"test:{project.id}",
            )
        )

    connection = YouTubeConnection(
        channel_id=channel.id,
        access_token_enc=crypto.encrypt("access-token"),
        refresh_token_enc=crypto.encrypt("refresh-token"),
        access_token_expires_at=utcnow() + timedelta(hours=1),
        status=ConnectionStatus.ACTIVE,
        scopes=[],
    )
    session.add(connection)
    await session.commit()
    return project, connection


def _mock_video(monkeypatch, snapshot):
    async def fetch_video(access_token: str, youtube_video_id: str):
        assert access_token == "access-token"
        assert youtube_video_id in {VIDEO_ID, OTHER_VIDEO_ID}
        return snapshot

    monkeypatch.setattr(data_api, "fetch_video", fetch_video)


async def test_successful_manual_publication_confirmation(
    client, session, monkeypatch
) -> None:
    project, _ = await _setup(session)
    _mock_video(monkeypatch, _video())

    response = await client.post(
        f"/api/v1/projects/{project.id}/published",
        json={"youtube_video_id": VIDEO_ID},
    )

    assert response.status_code == 200
    assert response.json()["status"] == ProjectStatus.PUBLISHED
    assert response.json()["published_at"] == PUBLISHED_AT.isoformat()
    published = (await session.execute(select(PublishedVideo))).scalars().one()
    assert published.youtube_video_id == VIDEO_ID
    assert published.title == "Nobody sent you that authenticator code"
    assert published.privacy_status == "public"

    job = (await session.execute(select(PublishingJob))).scalars().one()
    assert job.state == PublishState.DONE
    assert job.youtube_video_id == VIDEO_ID


async def test_invalid_youtube_video_id_is_rejected(client, session) -> None:
    project, _ = await _setup(session)
    response = await client.post(
        f"/api/v1/projects/{project.id}/published",
        json={"youtube_video_id": "too-short"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_youtube_video_id"


async def test_video_not_found(client, session, monkeypatch) -> None:
    project, _ = await _setup(session)
    _mock_video(monkeypatch, None)
    response = await client.post(
        f"/api/v1/projects/{project.id}/published",
        json={"youtube_video_id": VIDEO_ID},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "youtube_video_not_found"


async def test_video_belongs_to_another_channel(client, session, monkeypatch) -> None:
    project, _ = await _setup(session)
    _mock_video(monkeypatch, _video(channel_id="UC_someone_else"))
    response = await client.post(
        f"/api/v1/projects/{project.id}/published",
        json={"youtube_video_id": VIDEO_ID},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "youtube_video_wrong_channel"
    await session.refresh(project)
    assert project.status == ProjectStatus.AWAITING_HUMAN_UPLOAD


async def test_expired_oauth_connection(client, session, monkeypatch) -> None:
    project, connection = await _setup(session)
    connection.status = ConnectionStatus.EXPIRED
    await session.commit()
    _mock_video(monkeypatch, _video())

    response = await client.post(
        f"/api/v1/projects/{project.id}/published",
        json={"youtube_video_id": VIDEO_ID},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "youtube_not_connected"


async def test_repeated_confirmation_is_idempotent(client, session, monkeypatch) -> None:
    project, _ = await _setup(session)
    _mock_video(monkeypatch, _video())

    first = await client.post(
        f"/api/v1/projects/{project.id}/published", json={"youtube_video_id": VIDEO_ID}
    )
    second = await client.post(
        f"/api/v1/projects/{project.id}/published", json={"youtube_video_id": VIDEO_ID}
    )

    assert first.status_code == second.status_code == 200
    assert await session.scalar(select(func.count()).select_from(PublishedVideo)) == 1
    transition_count = await session.scalar(
        select(func.count())
        .select_from(ProjectTransition)
        .where(
            ProjectTransition.project_id == project.id,
            ProjectTransition.to_status == ProjectStatus.PUBLISHED,
        )
    )
    assert transition_count == 1


async def test_video_already_attached_to_same_project(client, session, monkeypatch) -> None:
    project, _ = await _setup(session)
    _mock_video(monkeypatch, _video())
    await client.post(
        f"/api/v1/projects/{project.id}/published", json={"youtube_video_id": VIDEO_ID}
    )

    response = await client.post(
        f"/api/v1/projects/{project.id}/published", json={"youtube_video_id": VIDEO_ID}
    )
    assert response.status_code == 200
    assert response.json()["youtube_video_id"] == VIDEO_ID


async def test_video_already_attached_to_different_project(
    client, session, monkeypatch
) -> None:
    first_project, _ = await _setup(session)
    _mock_video(monkeypatch, _video())
    assert (
        await client.post(
            f"/api/v1/projects/{first_project.id}/published",
            json={"youtube_video_id": VIDEO_ID},
        )
    ).status_code == 200

    channel = (await session.execute(select(Channel))).scalars().one()
    second_project = ContentProject(
        channel_id=channel.id,
        topic="Second publication project",
        status=ProjectStatus.AWAITING_HUMAN_UPLOAD,
    )
    session.add(second_project)
    await session.commit()

    response = await client.post(
        f"/api/v1/projects/{second_project.id}/published",
        json={"youtube_video_id": VIDEO_ID},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "youtube_video_already_attached"
    await session.refresh(second_project)
    assert second_project.status == ProjectStatus.AWAITING_HUMAN_UPLOAD


async def test_published_at_is_persisted(client, session, monkeypatch) -> None:
    project, _ = await _setup(session)
    _mock_video(monkeypatch, _video())
    await client.post(
        f"/api/v1/projects/{project.id}/published", json={"youtube_video_id": VIDEO_ID}
    )
    published = (await session.execute(select(PublishedVideo))).scalars().one()
    assert published.published_at == PUBLISHED_AT


async def test_correct_project_state_transition(client, session, monkeypatch) -> None:
    project, _ = await _setup(session)
    _mock_video(monkeypatch, _video())
    await client.post(
        f"/api/v1/projects/{project.id}/published", json={"youtube_video_id": VIDEO_ID}
    )

    await session.refresh(project)
    assert project.status == ProjectStatus.PUBLISHED
    transitions = (
        await session.execute(
            select(ProjectTransition).where(ProjectTransition.project_id == project.id)
        )
    ).scalars().all()
    assert [(row.from_status, row.to_status) for row in transitions] == [
        (ProjectStatus.AWAITING_HUMAN_UPLOAD, ProjectStatus.PUBLISHED)
    ]
