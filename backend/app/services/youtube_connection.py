"""YouTube connection lifecycle.

Owns the transaction boundaries for connecting, refreshing, syncing and
disconnecting. The API routes and job handlers are both thin wrappers around
this module, so the behaviour is identical whether a human clicked a button or
the scheduler fired.

Token loss is treated as an expected event, not an error path (ARCH §3.3): a
dead refresh token moves the connection to EXPIRED and surfaces in the UI, it
does not crash a job or wedge the pipeline.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.clock import utcnow
from app.core.crypto import decrypt, encrypt
from app.core.logging import get_logger
from app.integrations.youtube import data_api, oauth
from app.integrations.youtube.errors import (
    GoogleAuthError,
    InvalidGrantError,
    YouTubeNotConnectedError,
)
from app.models.channel import Channel
from app.models.enums import ConnectionStatus
from app.models.youtube import OAuthState, YouTubeConnection

log = get_logger("youtube.service")


# ------------------------------------------------------------------ helpers ---
async def get_channel(session: AsyncSession) -> Channel:
    channel = (
        await session.execute(select(Channel).order_by(Channel.created_at).limit(1))
    ).scalar_one_or_none()
    if channel is None:
        raise YouTubeNotConnectedError(
            "No channel row found. Run migrations: alembic upgrade head"
        )
    return channel


async def get_connection(session: AsyncSession) -> YouTubeConnection | None:
    return (
        await session.execute(select(YouTubeConnection).limit(1))
    ).scalar_one_or_none()


async def _mark_error(
    session: AsyncSession,
    connection: YouTubeConnection,
    status: ConnectionStatus,
    message: str,
) -> None:
    connection.status = status
    connection.last_error = message[:2000]
    connection.last_error_at = utcnow()
    channel = await session.get(Channel, connection.channel_id)
    if channel is not None:
        channel.connection_status = status
    await session.flush()
    log.warning("youtube.connection_degraded", status=status.value, reason=message[:200])


# ------------------------------------------------------------ oauth: start ---
async def start_authorization(session: AsyncSession) -> str:
    """Create an OAuth round trip and return the Google consent URL."""
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise GoogleAuthError(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not configured."
        )

    request = oauth.build_authorization_request()
    session.add(
        OAuthState(
            state=request.state,
            code_verifier=request.code_verifier,
            redirect_uri=request.redirect_uri,
            expires_at=utcnow() + oauth.OAUTH_STATE_TTL,
        )
    )
    await session.flush()
    log.info("youtube.oauth.started", redirect_uri=request.redirect_uri)
    return request.authorization_url


async def _consume_state(session: AsyncSession, state: str) -> OAuthState:
    """Fetch and delete a state row. Single-use by construction."""
    row = await session.get(OAuthState, state)
    if row is None:
        raise GoogleAuthError(
            "Unknown or already-used OAuth state. Start the connection again."
        )
    expired = row.expires_at < utcnow()
    await session.delete(row)
    await session.flush()
    if expired:
        raise GoogleAuthError("The OAuth request expired. Start the connection again.")
    return row


# --------------------------------------------------------- oauth: callback ---
async def complete_authorization(
    session: AsyncSession, code: str, state: str
) -> YouTubeConnection:
    """Exchange the code, store tokens, and sync channel metadata."""
    state_row = await _consume_state(session, state)
    # Detached copies: the row is deleted, but the exchange needs its values.
    verifier, redirect_uri = state_row.code_verifier, state_row.redirect_uri

    bundle = await oauth.exchange_code(code, verifier, redirect_uri)
    if not bundle.refresh_token:
        # Without a refresh token the connection dies in an hour and background
        # jobs cannot work at all, so this is a hard failure rather than a warning.
        raise GoogleAuthError(
            "Google did not return a refresh token. Remove this app's access at "
            "https://myaccount.google.com/permissions and connect again."
        )

    email = await oauth.fetch_account_email(bundle.access_token)
    channel = await get_channel(session)

    connection = await get_connection(session)
    if connection is None:
        connection = YouTubeConnection(channel_id=channel.id)
        session.add(connection)

    connection.access_token_enc = encrypt(bundle.access_token)
    connection.refresh_token_enc = encrypt(bundle.refresh_token)
    connection.access_token_expires_at = bundle.expires_at
    connection.token_type = bundle.token_type
    connection.scopes = bundle.scopes
    connection.google_account_email = email
    connection.status = ConnectionStatus.ACTIVE
    connection.granted_at = utcnow()
    connection.last_refreshed_at = utcnow()
    connection.last_error = None
    connection.last_error_at = None
    await session.flush()

    missing = oauth.missing_scopes(bundle.scopes)
    if missing:
        # Connected, but not fully capable. Recorded rather than raised so the
        # user keeps a working read-only connection.
        log.warning("youtube.oauth.partial_scopes", missing_count=len(missing))
        connection.last_error = (
            "Some permissions were not granted: "
            + ", ".join(s.rsplit("/", 1)[-1] for s in missing)
        )
        connection.last_error_at = utcnow()

    await sync_channel_metadata(session, connection)
    log.info("youtube.connected", account=email, scope_count=len(bundle.scopes))
    return connection


# --------------------------------------------------------------- tokens ---
async def get_access_token(session: AsyncSession, connection: YouTubeConnection) -> str:
    """Return a usable access token, refreshing it if it is close to expiry.

    This is the single entry point for every authenticated call, so no caller
    ever has to think about expiry.
    """
    if connection.status != ConnectionStatus.ACTIVE:
        raise YouTubeNotConnectedError(
            f"YouTube connection is {connection.status}. Reconnect the channel."
        )

    skew = timedelta(seconds=settings.OAUTH_REFRESH_SKEW_SECONDS)
    expires_at = connection.access_token_expires_at
    if expires_at is None or expires_at - skew <= utcnow():
        await refresh_connection(session, connection)

    token = decrypt(connection.access_token_enc)
    if token is None:
        raise YouTubeNotConnectedError("No access token stored. Reconnect the channel.")
    return token


async def refresh_connection(
    session: AsyncSession, connection: YouTubeConnection
) -> YouTubeConnection:
    """Refresh the access token. Marks the connection EXPIRED on invalid_grant."""
    refresh_token = decrypt(connection.refresh_token_enc)
    if not refresh_token:
        await _mark_error(
            session, connection, ConnectionStatus.EXPIRED, "No refresh token stored."
        )
        raise YouTubeNotConnectedError("No refresh token stored. Reconnect the channel.")

    try:
        bundle = await oauth.refresh_access_token(refresh_token)
    except InvalidGrantError as exc:
        # The expected failure: consent-screen 7-day expiry, or revoked access.
        await _mark_error(session, connection, ConnectionStatus.EXPIRED, str(exc))
        raise
    except GoogleAuthError as exc:
        await _mark_error(session, connection, ConnectionStatus.ERROR, str(exc))
        raise

    connection.access_token_enc = encrypt(bundle.access_token)
    if bundle.refresh_token and bundle.refresh_token != refresh_token:
        connection.refresh_token_enc = encrypt(bundle.refresh_token)
    connection.access_token_expires_at = bundle.expires_at
    connection.status = ConnectionStatus.ACTIVE
    connection.last_refreshed_at = utcnow()
    connection.last_error = None
    connection.last_error_at = None

    channel = await session.get(Channel, connection.channel_id)
    if channel is not None:
        channel.connection_status = ConnectionStatus.ACTIVE
    await session.flush()
    return connection


# ---------------------------------------------------------- channel sync ---
async def sync_channel_metadata(
    session: AsyncSession, connection: YouTubeConnection | None = None
) -> Channel:
    """Pull channel metadata from YouTube into our Channel row."""
    connection = connection or await get_connection(session)
    if connection is None:
        raise YouTubeNotConnectedError()

    access_token = await get_access_token(session, connection)
    snapshot = await data_api.fetch_my_channel(access_token)

    channel = await session.get(Channel, connection.channel_id)
    if channel is None:  # pragma: no cover - FK makes this unreachable
        raise YouTubeNotConnectedError("Channel row missing for this connection.")

    # YouTube owns identity and statistics. We own positioning — `tagline`,
    # `niche`, `target_audience` and `brand_voice` are ours and are never
    # overwritten by a sync.
    channel.youtube_channel_id = snapshot.youtube_channel_id
    channel.name = snapshot.title or channel.name
    channel.description = snapshot.description or channel.description
    channel.handle = snapshot.handle or channel.handle
    channel.thumbnail_url = snapshot.thumbnail_url
    channel.uploads_playlist_id = snapshot.uploads_playlist_id
    channel.subscriber_count = snapshot.subscriber_count
    channel.video_count = snapshot.video_count
    channel.view_count = snapshot.view_count
    channel.connection_status = ConnectionStatus.ACTIVE
    channel.connected_at = channel.connected_at or utcnow()
    channel.last_sync_at = utcnow()
    await session.flush()

    log.info(
        "youtube.channel_synced",
        youtube_channel_id=snapshot.youtube_channel_id,
        subscriber_count=snapshot.subscriber_count,
    )
    return channel


# ------------------------------------------------------------- disconnect ---
async def disconnect(session: AsyncSession, *, revoke_remote: bool = True) -> bool:
    """Revoke and delete the stored connection. Idempotent."""
    connection = await get_connection(session)
    if connection is None:
        return False

    if revoke_remote:
        token = decrypt(connection.refresh_token_enc) or decrypt(connection.access_token_enc)
        if token:
            await oauth.revoke(token)

    channel = await session.get(Channel, connection.channel_id)
    if channel is not None:
        channel.connection_status = ConnectionStatus.NOT_CONNECTED
        channel.connected_at = None
        # Deliberately keep youtube_channel_id and statistics: they are history,
        # not credentials, and Phase 3 analytics reference past videos by them.
        channel.publishing_enabled = False

    await session.delete(connection)
    await session.flush()
    log.info("youtube.disconnected")
    return True


async def purge_expired_states(session: AsyncSession) -> int:
    result = await session.execute(
        delete(OAuthState).where(OAuthState.expires_at < utcnow())
    )
    await session.flush()
    return result.rowcount or 0


# ----------------------------------------------------------------- status ---
async def get_status(session: AsyncSession) -> dict[str, Any]:
    """Connection status for the dashboard. Never returns token material."""
    credentials_present = bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)
    connection = await get_connection(session)
    channel = await get_channel(session)

    testing_consent = settings.GOOGLE_CONSENT_PUBLISHING_STATUS == "testing"
    warnings: list[str] = []
    if testing_consent:
        warnings.append(
            "The OAuth consent screen is marked 'testing', so Google expires refresh "
            "tokens after 7 days. Set it to 'In Production' in Google Cloud Console "
            "and update GOOGLE_CONSENT_PUBLISHING_STATUS."
        )
    if connection is not None and connection.status == ConnectionStatus.EXPIRED:
        warnings.append("The connection expired. Reconnect the channel.")

    status: dict[str, Any] = {
        "implemented": True,
        "enabled": settings.YOUTUBE_API_ENABLED,
        "credentials_present": credentials_present,
        "connected": connection is not None and connection.status == ConnectionStatus.ACTIVE,
        "connection_status": (
            connection.status if connection else ConnectionStatus.NOT_CONNECTED.value
        ),
        "google_account_email": connection.google_account_email if connection else None,
        "granted_scopes": list(connection.scopes) if connection else [],
        "missing_scopes": (
            oauth.missing_scopes(list(connection.scopes)) if connection else list(oauth.SCOPES)
        ),
        "access_token_expires_at": (
            connection.access_token_expires_at.isoformat()
            if connection and connection.access_token_expires_at
            else None
        ),
        "last_refreshed_at": (
            connection.last_refreshed_at.isoformat()
            if connection and connection.last_refreshed_at
            else None
        ),
        "last_error": connection.last_error if connection else None,
        "consent_publishing_status": settings.GOOGLE_CONSENT_PUBLISHING_STATUS,
        "audit_status": connection.audit_status if connection else "UNAUDITED",
        "warnings": warnings,
        "channel": {
            "youtube_channel_id": channel.youtube_channel_id,
            "name": channel.name,
            "handle": channel.handle,
            "thumbnail_url": channel.thumbnail_url,
            "subscriber_count": channel.subscriber_count,
            "video_count": channel.video_count,
            "view_count": channel.view_count,
            "last_sync_at": channel.last_sync_at.isoformat() if channel.last_sync_at else None,
        },
        # ARCH §3.1 — surfaced permanently because it governs what Phase 6 can do.
        "known_limitation": (
            "Videos uploaded through the API from an unaudited Google Cloud project are "
            "permanently locked to private and cannot be appealed. Until the compliance "
            "audit passes, publishing uses MANUAL_HANDOFF."
        ),
    }
    return status
