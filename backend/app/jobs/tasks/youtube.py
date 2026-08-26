"""YouTube background jobs.

These are what make the connection survive without the user present: tokens are
refreshed hourly and channel metadata is re-synced daily, both with the queue's
normal retry, timeout and history treatment.

Retry classification matters here more than anywhere else so far. `InvalidGrant`
is terminal — retrying a dead refresh token cannot revive it and only burns
attempts — while a 5xx or a network blip is retryable.
"""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.core.clock import utcnow
from app.core.errors import RetryableError
from app.integrations.youtube.errors import (
    InvalidGrantError,
    RateLimitedError,
    TransientGoogleError,
)
from app.jobs.context import JobContext
from app.jobs.registry import job
from app.models.enums import ConnectionStatus
from app.services import youtube_connection as service

# Network-ish failures worth another attempt. InvalidGrantError and
# QuotaExceededError are deliberately absent: both are terminal.
YOUTUBE_RETRYABLE = (RetryableError, TransientGoogleError, RateLimitedError)


@job(
    "youtube.refresh_tokens",
    max_attempts=3,
    timeout_seconds=60,
    retry_on=YOUTUBE_RETRYABLE,
    default_priority=5,
    description="Refresh the access token before it expires.",
)
async def refresh_tokens(ctx: JobContext) -> dict[str, Any]:
    connection = await service.get_connection(ctx.session)
    if connection is None:
        return {"skipped": "not_connected"}
    if connection.status != ConnectionStatus.ACTIVE:
        # Already known-bad; a scheduled job must not keep hammering Google.
        return {"skipped": f"status_{connection.status}"}

    expires_at = connection.access_token_expires_at
    if expires_at is not None:
        remaining = (expires_at - utcnow()).total_seconds()
        if remaining > settings.OAUTH_REFRESH_SKEW_SECONDS:
            return {"skipped": "not_due", "seconds_remaining": int(remaining)}

    try:
        await service.refresh_connection(ctx.session, connection)
    except InvalidGrantError as exc:
        # Expected, not exceptional. The connection is already marked EXPIRED by
        # the service; report it as a result so the job history reads honestly
        # instead of showing a red failure the operator cannot act on differently.
        ctx.logger.warning("youtube.refresh.invalid_grant")
        return {"refreshed": False, "status": "EXPIRED", "reason": str(exc)[:300]}

    return {
        "refreshed": True,
        "expires_at": (
            connection.access_token_expires_at.isoformat()
            if connection.access_token_expires_at
            else None
        ),
    }


@job(
    "youtube.sync_channel",
    max_attempts=3,
    timeout_seconds=120,
    retry_on=YOUTUBE_RETRYABLE,
    description="Re-sync channel metadata and statistics from YouTube.",
)
async def sync_channel(ctx: JobContext) -> dict[str, Any]:
    connection = await service.get_connection(ctx.session)
    if connection is None:
        return {"skipped": "not_connected"}
    if connection.status != ConnectionStatus.ACTIVE:
        return {"skipped": f"status_{connection.status}"}

    try:
        channel = await service.sync_channel_metadata(ctx.session, connection)
    except InvalidGrantError:
        ctx.logger.warning("youtube.sync.invalid_grant")
        return {"synced": False, "status": "EXPIRED"}

    return {
        "synced": True,
        "youtube_channel_id": channel.youtube_channel_id,
        "subscriber_count": channel.subscriber_count,
        "video_count": channel.video_count,
    }


@job(
    "youtube.purge_oauth_states",
    max_attempts=1,
    timeout_seconds=30,
    description="Delete expired OAuth state rows.",
)
async def purge_oauth_states(ctx: JobContext) -> dict[str, Any]:
    purged = await service.purge_expired_states(ctx.session)
    return {"purged": purged}
