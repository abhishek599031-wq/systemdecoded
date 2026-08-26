"""Google OAuth 2.0 authorization-code flow with PKCE.

See ADR 0002 for why this is httpx rather than google-auth-oauthlib.

Security properties, each deliberate:
  - PKCE (S256) even though we are a confidential client, so an intercepted
    authorization code is useless without the verifier.
  - `state` is generated server-side, stored server-side, and consumed exactly
    once — it is not a cookie, so nothing that can set cookies can forge a callback.
  - `access_type=offline` + `prompt=consent` so Google always returns a refresh
    token; without `prompt=consent` it only sends one on the *first* authorization,
    and a re-connect would silently produce a connection that cannot refresh.
  - The code exchange happens server-side only. The client secret never reaches
    the browser.

Nothing in this module logs a token, a code, or the client secret.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.core.clock import utcnow
from app.core.logging import get_logger
from app.integrations.youtube.errors import (
    TransientGoogleError,
    classify_token_error,
)

log = get_logger("youtube.oauth")

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"

# Requested up front, including upload, so that passing the compliance audit
# later does not require a second consent round trip (ARCH §13.1).
SCOPES = (
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
)
# Identifies which Google account authorised us, for display only.
IDENTITY_SCOPES = ("openid", "email")

OAUTH_STATE_TTL = timedelta(minutes=15)
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    authorization_url: str
    state: str
    code_verifier: str
    redirect_uri: str


@dataclass(frozen=True, slots=True)
class TokenBundle:
    access_token: str
    refresh_token: str | None
    expires_in: int
    scopes: list[str]
    token_type: str

    @property
    def expires_at(self):
        return utcnow() + timedelta(seconds=self.expires_in)

    def __repr__(self) -> str:  # pragma: no cover - guards accidental logging
        return (
            f"<TokenBundle scopes={len(self.scopes)} "
            f"expires_in={self.expires_in} has_refresh={self.refresh_token is not None}>"
        )


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def build_authorization_request(redirect_uri: str | None = None) -> AuthorizationRequest:
    """Build the Google consent URL plus the state/verifier to store alongside it."""
    redirect_uri = redirect_uri or settings.GOOGLE_REDIRECT_URI
    state = secrets.token_urlsafe(32)
    verifier, challenge = _pkce_pair()

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join((*SCOPES, *IDENTITY_SCOPES)),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    return AuthorizationRequest(
        authorization_url=f"{AUTH_ENDPOINT}?{urlencode(params)}",
        state=state,
        code_verifier=verifier,
        redirect_uri=redirect_uri,
    )


async def _post_token(data: dict[str, str]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(
                TOKEN_ENDPOINT,
                data=data,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        # Network-level: retryable. Never include `data` in the message.
        raise TransientGoogleError(f"Token endpoint unreachable: {type(exc).__name__}") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code >= 400:
        raise classify_token_error(response.status_code, payload)
    return payload


def _to_bundle(payload: dict[str, Any], *, fallback_refresh: str | None = None) -> TokenBundle:
    return TokenBundle(
        access_token=payload["access_token"],
        # Google omits refresh_token on a refresh response; keep the existing one.
        refresh_token=payload.get("refresh_token") or fallback_refresh,
        expires_in=int(payload.get("expires_in", 3600)),
        scopes=(payload.get("scope") or "").split(),
        token_type=payload.get("token_type", "Bearer"),
    )


async def exchange_code(code: str, code_verifier: str, redirect_uri: str) -> TokenBundle:
    """Exchange an authorization code for tokens."""
    payload = await _post_token(
        {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            # Must byte-for-byte match the value sent to the auth endpoint.
            "redirect_uri": redirect_uri,
        }
    )
    bundle = _to_bundle(payload)
    log.info(
        "youtube.oauth.code_exchanged",
        has_refresh_token=bundle.refresh_token is not None,
        scope_count=len(bundle.scopes),
    )
    return bundle


async def refresh_access_token(refresh_token: str) -> TokenBundle:
    """Exchange a refresh token for a fresh access token.

    Raises InvalidGrantError (terminal) when the refresh token is dead.
    """
    payload = await _post_token(
        {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    )
    bundle = _to_bundle(payload, fallback_refresh=refresh_token)
    log.info("youtube.oauth.token_refreshed", expires_in=bundle.expires_in)
    return bundle


async def fetch_account_email(access_token: str) -> str | None:
    """Best-effort: which Google account authorised us. Never fatal."""
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
            )
        if response.status_code == 200:
            return response.json().get("email")
    except (httpx.HTTPError, ValueError):
        log.warning("youtube.oauth.userinfo_failed")
    return None


async def revoke(token: str) -> bool:
    """Ask Google to revoke a token. Returns True if Google accepted it.

    Failure is not fatal — we delete our copy regardless, since the operator's
    intent was to disconnect.
    """
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(REVOKE_ENDPOINT, data={"token": token})
        return response.status_code == 200
    except httpx.HTTPError:
        log.warning("youtube.oauth.revoke_failed")
        return False


def missing_scopes(granted: list[str]) -> list[str]:
    """Scopes we asked for but did not get.

    The consent screen lets a user untick individual permissions, so a
    "successful" connection can still be unable to upload. Better to detect that
    at connect time than at publish time.
    """
    return [s for s in SCOPES if s not in set(granted)]
