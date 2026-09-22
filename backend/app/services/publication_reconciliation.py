"""Verified, idempotent reconciliation for manual YouTube uploads.

Publishing remains ``MANUAL_HANDOFF``. A human supplies the YouTube video ID;
this service validates it against the connected account, verifies ownership,
persists authoritative metadata, and advances only the matching project.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.errors import ConflictError, ValidationError
from app.core.state_machine import transition
from app.integrations.youtube import data_api
from app.integrations.youtube.errors import (
    GoogleAuthError,
    YouTubeNotConnectedError,
    YouTubeVideoNotFoundError,
    YouTubeVideoOwnershipError,
)
from app.models.channel import Channel
from app.models.content import ContentProject, PublishedVideo
from app.models.enums import ConnectionStatus, ProjectStatus
from app.services import publishing, youtube_connection

YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
RECONCILIATION_METHOD = "manual_confirmation_verified"


def validate_youtube_video_id(value: str) -> str:
    """Return a canonical video ID or raise a stable validation error."""
    video_id = value.strip()
    if not YOUTUBE_VIDEO_ID_RE.fullmatch(video_id):
        raise ValidationError(
            "youtube_video_id must be an 11-character YouTube video ID.",
            code="invalid_youtube_video_id",
        )
    return video_id


async def _mark_auth_failure(
    session: AsyncSession,
    channel: Channel,
    connection: Any,
    exc: GoogleAuthError,
) -> None:
    """Make a Google auth rejection visible and convert it to an API-safe error."""
    if connection.status != ConnectionStatus.EXPIRED:
        connection.status = (
            ConnectionStatus.EXPIRED if exc.status == 401 else ConnectionStatus.ERROR
        )
    connection.last_error = str(exc)[:2000]
    connection.last_error_at = utcnow()
    channel.connection_status = connection.status
    await session.flush()


async def reconcile_manual_publication(
    session: AsyncSession,
    project: ContentProject,
    youtube_video_id: str,
) -> PublishedVideo:
    """Verify and attach one manual upload, atomically and idempotently."""
    video_id = validate_youtube_video_id(youtube_video_id)

    # Serialize confirmations for both this project and this external video.
    # The advisory lock closes the only race not covered by the project row
    # lock: two different projects confirming the same YouTube ID concurrently.
    locked_project = await session.get(ContentProject, project.id, with_for_update=True)
    if locked_project is None:  # pragma: no cover - route already resolved it
        raise ConflictError("The project no longer exists.")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:video_id))"),
        {"video_id": video_id},
    )

    if locked_project.status not in {
        ProjectStatus.AWAITING_HUMAN_UPLOAD,
        ProjectStatus.PUBLISHED,
    }:
        raise ConflictError(
            f"Project is {locked_project.status}; manual publication can only be "
            "confirmed from AWAITING_HUMAN_UPLOAD.",
            code="project_not_awaiting_manual_upload",
        )

    connection = await youtube_connection.get_connection(session)
    if connection is None or connection.channel_id != locked_project.channel_id:
        raise YouTubeNotConnectedError()

    channel = await session.get(Channel, locked_project.channel_id)
    if channel is None:  # pragma: no cover - project FK makes this unreachable
        raise YouTubeNotConnectedError("The project's channel no longer exists.")

    try:
        access_token = await youtube_connection.get_access_token(session, connection)
        if not channel.youtube_channel_id:
            channel = await youtube_connection.sync_channel_metadata(session, connection)
        video = await data_api.fetch_video(access_token, video_id)
    except GoogleAuthError as exc:
        await _mark_auth_failure(session, channel, connection, exc)
        raise YouTubeNotConnectedError(
            "YouTube authorization expired or was rejected. Reconnect the channel and try again."
        ) from exc

    if video is None:
        raise YouTubeVideoNotFoundError()
    if video.youtube_video_id != video_id:
        raise YouTubeVideoNotFoundError("YouTube returned an unexpected video identifier.")
    if video.youtube_channel_id != channel.youtube_channel_id:
        raise YouTubeVideoOwnershipError(
            detail={
                "connected_channel_id": channel.youtube_channel_id,
                "video_channel_id": video.youtube_channel_id,
            }
        )

    published = await publishing.record_published_video(
        session,
        locked_project,
        video_id,
        published_at=video.published_at,
        method=RECONCILIATION_METHOD,
        title=video.title,
        privacy_status=video.privacy_status,
    )

    if locked_project.status == ProjectStatus.AWAITING_HUMAN_UPLOAD:
        await transition(
            session,
            locked_project,
            ProjectStatus.PUBLISHED,
            actor="HUMAN",
            reason=f"Verified manual YouTube upload {video_id}",
        )
    return published
