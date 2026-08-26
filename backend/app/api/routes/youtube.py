"""YouTube OAuth and connection endpoints.

Security posture: no endpoint here ever returns, echoes or logs a token, an
authorization code, or the client secret. `/status` is safe to poll from the
dashboard and safe to screenshot.
"""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import NotConfiguredError
from app.core.logging import get_logger
from app.db.session import get_db
from app.integrations.youtube import oauth
from app.integrations.youtube.errors import GoogleAPIError, YouTubeNotConnectedError
from app.services import youtube_connection as service

log = get_logger("api.youtube")

router = APIRouter(prefix="/youtube", tags=["youtube"])


def _require_configured() -> None:
    problems = settings.validate_runtime()
    blocking = [p for p in problems if "GOOGLE_" in p or "SECRETS_KEY" in p]
    if not settings.YOUTUBE_API_ENABLED:
        raise NotConfiguredError(
            "YouTube integration is disabled. Set YOUTUBE_API_ENABLED=true.",
            code="youtube_disabled",
            detail={"problems": blocking},
        )
    if blocking:
        raise NotConfiguredError(
            "YouTube integration is not fully configured.",
            code="youtube_misconfigured",
            detail={"problems": blocking},
        )


def _frontend_redirect(**params: str) -> RedirectResponse:
    """Send the browser back to the studio with a result flag.

    The callback is a top-level browser navigation, not an XHR, so the outcome
    has to travel in the URL rather than a response body.
    """
    base = settings.FRONTEND_URL.rstrip("/")
    return RedirectResponse(url=f"{base}/?{urlencode(params)}", status_code=303)


@router.get("/status", summary="YouTube connection status")
async def youtube_status(db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, Any]:
    """Always answerable, connected or not. Never raises, never leaks tokens."""
    status = await service.get_status(db)
    status["required_scopes"] = list(oauth.SCOPES)
    status["redirect_uri"] = settings.GOOGLE_REDIRECT_URI
    status["config_problems"] = [
        p for p in settings.validate_runtime() if "GOOGLE_" in p or "SECRETS_KEY" in p
    ]
    return status


@router.get("/oauth/start", summary="Begin the OAuth flow")
async def oauth_start(
    db: Annotated[AsyncSession, Depends(get_db)],
    json_response: Annotated[bool, Query(alias="json")] = False,
) -> Any:
    """Redirect the browser to Google's consent screen.

    `?json=true` returns the URL instead of redirecting, which is what the
    dashboard uses so it can surface configuration errors in-place rather than
    bouncing the user to a Google error page.
    """
    _require_configured()
    authorization_url = await service.start_authorization(db)
    if json_response:
        return {"authorization_url": authorization_url}
    return RedirectResponse(url=authorization_url, status_code=307)


@router.get("/oauth/callback", summary="OAuth redirect target")
async def oauth_callback(
    db: Annotated[AsyncSession, Depends(get_db)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Google redirects the browser here after consent.

    Always redirects back to the studio rather than rendering JSON — a human is
    looking at this, not a script. Failures are reported by flag, never with the
    raw exception, so nothing sensitive lands in the address bar or browser
    history.
    """
    if error:
        # The user pressed "Cancel", or Google rejected the request.
        log.warning("youtube.oauth.callback_error", error=error)
        return _frontend_redirect(youtube="error", reason=error)

    if not code or not state:
        return _frontend_redirect(youtube="error", reason="missing_code_or_state")

    try:
        await service.complete_authorization(db, code=code, state=state)
    except GoogleAPIError as exc:
        log.warning("youtube.oauth.callback_failed", reason=type(exc).__name__)
        return _frontend_redirect(youtube="error", reason=type(exc).__name__)
    except Exception:
        log.exception("youtube.oauth.callback_unexpected")
        return _frontend_redirect(youtube="error", reason="unexpected_error")

    return _frontend_redirect(youtube="connected")


@router.post("/sync", summary="Re-sync channel metadata from YouTube")
async def sync_channel(db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, Any]:
    _require_configured()
    channel = await service.sync_channel_metadata(db)
    return {
        "synced": True,
        "youtube_channel_id": channel.youtube_channel_id,
        "name": channel.name,
        "handle": channel.handle,
        "subscriber_count": channel.subscriber_count,
        "video_count": channel.video_count,
        "last_sync_at": channel.last_sync_at.isoformat() if channel.last_sync_at else None,
    }


@router.post("/refresh", summary="Force an access-token refresh")
async def force_refresh(db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, Any]:
    """Exposed for operators to verify refresh works without waiting an hour."""
    _require_configured()
    connection = await service.get_connection(db)
    if connection is None:
        raise YouTubeNotConnectedError()
    await service.refresh_connection(db, connection)
    return {
        "refreshed": True,
        "status": connection.status,
        "access_token_expires_at": (
            connection.access_token_expires_at.isoformat()
            if connection.access_token_expires_at
            else None
        ),
    }


@router.post("/disconnect", summary="Revoke and remove the connection")
async def disconnect(db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, Any]:
    removed = await service.disconnect(db)
    return {"disconnected": removed}
