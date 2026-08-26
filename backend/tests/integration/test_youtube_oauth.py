"""End-to-end OAuth against a mocked Google.

Everything except Google itself is real: the routes, the service layer, the
encryption, the database, and the job handlers. Google's token and Data API
endpoints are replaced with an httpx MockTransport, which is what lets the
callback, refresh and invalid_grant paths be tested without a human clicking a
consent screen.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.core import crypto
from app.core.clock import utcnow
from app.integrations.youtube import data_api, oauth
from app.integrations.youtube.errors import InvalidGrantError
from app.models.channel import Channel
from app.models.enums import ConnectionStatus
from app.models.youtube import OAuthState, YouTubeConnection
from app.services import youtube_connection as service

CHANNEL_PAYLOAD = {
    "items": [
        {
            "id": "UC_systemdecoded_test",
            "snippet": {
                "title": "SystemDecoded",
                "description": "Complex Technology. Decoded.",
                "customUrl": "@systemdecoded",
                "country": "IN",
                "thumbnails": {"high": {"url": "https://yt3.test/avatar.jpg"}},
            },
            "statistics": {
                "subscriberCount": "1234",
                "videoCount": "42",
                "viewCount": "987654",
            },
            "contentDetails": {"relatedPlaylists": {"uploads": "UU_systemdecoded_test"}},
        }
    ]
}


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the app a working YouTube configuration for the duration of a test."""
    from app.config import settings

    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(settings, "YOUTUBE_API_ENABLED", True)
    monkeypatch.setattr(settings, "SECRETS_KEY", crypto.generate_key())
    monkeypatch.setattr(
        settings, "GOOGLE_REDIRECT_URI", "http://localhost:8080/api/v1/youtube/oauth/callback"
    )
    crypto.reset_cipher_cache()
    yield
    crypto.reset_cipher_cache()


class FakeGoogle:
    """A stand-in for Google's token, userinfo and Data API endpoints."""

    def __init__(self) -> None:
        self.token_requests: list[dict[str, str]] = []
        self.refresh_response: dict[str, Any] | None = None
        self.token_status = 200
        self.token_error: dict[str, Any] | None = None
        self.channel_payload: dict[str, Any] = CHANNEL_PAYLOAD
        self.channel_status = 200
        self.revoked: list[str] = []
        self.omit_refresh_token = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        if url.startswith(oauth.TOKEN_ENDPOINT):
            form = dict(httpx.QueryParams(request.content.decode()))
            self.token_requests.append(form)
            if self.token_error is not None:
                return httpx.Response(self.token_status, json=self.token_error)
            if form.get("grant_type") == "refresh_token":
                return httpx.Response(
                    200,
                    json=self.refresh_response
                    or {
                        "access_token": "ya29.refreshed-access",
                        "expires_in": 3599,
                        "scope": " ".join(oauth.SCOPES),
                        "token_type": "Bearer",
                    },
                )
            initial = {
                "access_token": "ya29.initial-access",
                "refresh_token": "1//initial-refresh",
                "expires_in": 3599,
                "scope": " ".join((*oauth.SCOPES, "openid", "email")),
                "token_type": "Bearer",
            }
            if self.omit_refresh_token:
                initial.pop("refresh_token")
            return httpx.Response(200, json=initial)

        if url.startswith(oauth.USERINFO_ENDPOINT):
            return httpx.Response(200, json={"email": "creator@example.test"})

        if url.startswith(oauth.REVOKE_ENDPOINT):
            self.revoked.append("token")
            return httpx.Response(200)

        if url.startswith(f"{data_api.API_BASE}/channels"):
            return httpx.Response(self.channel_status, json=self.channel_payload)

        return httpx.Response(404, json={"error": {"message": f"unmocked {url}"}})


@pytest.fixture
def google(monkeypatch: pytest.MonkeyPatch) -> FakeGoogle:
    """Route every outbound httpx call in the integration to the fake."""
    fake = FakeGoogle()
    transport = httpx.MockTransport(fake.handler)
    real_client = httpx.AsyncClient

    def patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(oauth.httpx, "AsyncClient", patched)
    monkeypatch.setattr(data_api.httpx, "AsyncClient", patched)
    return fake


# ------------------------------------------------------------------ 1. start ---
async def test_oauth_start_persists_state_and_returns_google_url(client, session) -> None:
    response = await client.get("/api/v1/youtube/oauth/start?json=true")
    assert response.status_code == 200

    url = response.json()["authorization_url"]
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")

    rows = (await session.execute(select(OAuthState))).scalars().all()
    assert len(rows) == 1
    assert rows[0].state in url
    assert rows[0].redirect_uri.endswith("/api/v1/youtube/oauth/callback")


async def test_oauth_start_redirects_by_default(client) -> None:
    response = await client.get("/api/v1/youtube/oauth/start", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].startswith("https://accounts.google.com/")


async def test_oauth_start_refuses_when_disabled(client, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "YOUTUBE_API_ENABLED", False)
    response = await client.get("/api/v1/youtube/oauth/start?json=true")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "youtube_disabled"


# --------------------------------------------------------------- 3. callback ---
async def _start_flow(client, session) -> str:
    await client.get("/api/v1/youtube/oauth/start?json=true")
    row = (await session.execute(select(OAuthState))).scalars().one()
    return row.state


async def test_callback_completes_the_connection(client, session, google) -> None:
    state = await _start_flow(client, session)

    response = await client.get(
        f"/api/v1/youtube/oauth/callback?code=auth-code-123&state={state}",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "youtube=connected" in response.headers["location"]

    connection = (await session.execute(select(YouTubeConnection))).scalars().one()
    assert connection.status == ConnectionStatus.ACTIVE
    assert connection.google_account_email == "creator@example.test"
    assert connection.access_token_expires_at > utcnow()


async def test_callback_sends_pkce_verifier_and_exact_redirect_uri(
    client, session, google
) -> None:
    """Google rejects the exchange if either differs from the authorize step."""
    state = await _start_flow(client, session)
    state_row = await session.get(OAuthState, state)
    verifier = state_row.code_verifier

    await client.get(
        f"/api/v1/youtube/oauth/callback?code=auth-code-123&state={state}",
        follow_redirects=False,
    )

    exchange = google.token_requests[0]
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["code_verifier"] == verifier
    assert exchange["redirect_uri"] == "http://localhost:8080/api/v1/youtube/oauth/callback"


# ------------------------------------------------------- 4. token storage ---
async def test_tokens_are_encrypted_at_rest(client, session, google) -> None:
    state = await _start_flow(client, session)
    await client.get(
        f"/api/v1/youtube/oauth/callback?code=c&state={state}", follow_redirects=False
    )

    connection = (await session.execute(select(YouTubeConnection))).scalars().one()
    # Stored ciphertext must not contain the plaintext token anywhere.
    assert b"1//initial-refresh" not in bytes(connection.refresh_token_enc)
    assert b"ya29.initial-access" not in bytes(connection.access_token_enc)
    # ...but must decrypt back to it.
    assert crypto.decrypt(connection.refresh_token_enc) == "1//initial-refresh"
    assert crypto.decrypt(connection.access_token_enc) == "ya29.initial-access"


async def test_status_endpoint_never_exposes_tokens(client, session, google) -> None:
    state = await _start_flow(client, session)
    await client.get(
        f"/api/v1/youtube/oauth/callback?code=c&state={state}", follow_redirects=False
    )

    body = (await client.get("/api/v1/youtube/status")).text
    assert "1//initial-refresh" not in body
    assert "ya29" not in body
    assert "test-client-secret" not in body


async def test_state_is_single_use(client, session, google) -> None:
    """A replayed callback must not create a second connection."""
    state = await _start_flow(client, session)
    first = await client.get(
        f"/api/v1/youtube/oauth/callback?code=c&state={state}", follow_redirects=False
    )
    assert "youtube=connected" in first.headers["location"]

    replay = await client.get(
        f"/api/v1/youtube/oauth/callback?code=c&state={state}", follow_redirects=False
    )
    assert "youtube=error" in replay.headers["location"]


async def test_unknown_state_is_rejected(client, google) -> None:
    response = await client.get(
        "/api/v1/youtube/oauth/callback?code=c&state=forged-state", follow_redirects=False
    )
    assert "youtube=error" in response.headers["location"]


async def test_expired_state_is_rejected(client, session, google) -> None:
    state = await _start_flow(client, session)
    row = await session.get(OAuthState, state)
    row.expires_at = utcnow() - timedelta(minutes=1)
    await session.commit()

    response = await client.get(
        f"/api/v1/youtube/oauth/callback?code=c&state={state}", follow_redirects=False
    )
    assert "youtube=error" in response.headers["location"]


async def test_user_denied_consent_is_handled(client) -> None:
    response = await client.get(
        "/api/v1/youtube/oauth/callback?error=access_denied", follow_redirects=False
    )
    assert response.status_code == 303
    assert "youtube=error" in response.headers["location"]
    assert "access_denied" in response.headers["location"]


async def test_missing_refresh_token_is_a_hard_failure(client, session, google) -> None:
    """Without a refresh token background jobs die in an hour."""
    google.omit_refresh_token = True
    state = await _start_flow(client, session)

    response = await client.get(
        f"/api/v1/youtube/oauth/callback?code=c&state={state}", follow_redirects=False
    )
    assert "youtube=error" in response.headers["location"]
    assert (await session.execute(select(YouTubeConnection))).scalars().first() is None


# ------------------------------------------------- 5. channel metadata sync ---
async def test_channel_metadata_is_synced_from_google(client, session, google) -> None:
    state = await _start_flow(client, session)
    await client.get(
        f"/api/v1/youtube/oauth/callback?code=c&state={state}", follow_redirects=False
    )

    channel = (await session.execute(select(Channel))).scalars().first()
    await session.refresh(channel)
    assert channel.youtube_channel_id == "UC_systemdecoded_test"
    assert channel.handle == "@systemdecoded"
    assert channel.subscriber_count == 1234
    assert channel.video_count == 42
    # Captured now because Phase 2 reconciliation needs it.
    assert channel.uploads_playlist_id == "UU_systemdecoded_test"
    assert channel.connection_status == ConnectionStatus.ACTIVE
    assert channel.last_sync_at is not None


async def test_sync_preserves_locally_authored_positioning(client, session, google) -> None:
    """YouTube owns identity; we own strategy. A sync must not clobber ours."""
    channel = (await session.execute(select(Channel))).scalars().first()
    original_niche = channel.niche
    original_voice = channel.brand_voice

    state = await _start_flow(client, session)
    await client.get(
        f"/api/v1/youtube/oauth/callback?code=c&state={state}", follow_redirects=False
    )

    await session.refresh(channel)
    assert channel.niche == original_niche
    assert channel.brand_voice == original_voice


async def test_account_without_a_channel_is_reported_clearly(client, session, google) -> None:
    google.channel_payload = {"items": []}
    state = await _start_flow(client, session)
    response = await client.get(
        f"/api/v1/youtube/oauth/callback?code=c&state={state}", follow_redirects=False
    )
    assert "youtube=error" in response.headers["location"]


# ------------------------------------------------------------- 6. UI status ---
async def test_status_before_connecting(client) -> None:
    body = (await client.get("/api/v1/youtube/status")).json()
    assert body["implemented"] is True
    assert body["connected"] is False
    assert body["connection_status"] == "NOT_CONNECTED"
    assert body["missing_scopes"] == list(oauth.SCOPES)


async def test_status_after_connecting(client, session, google) -> None:
    state = await _start_flow(client, session)
    await client.get(
        f"/api/v1/youtube/oauth/callback?code=c&state={state}", follow_redirects=False
    )

    body = (await client.get("/api/v1/youtube/status")).json()
    assert body["connected"] is True
    assert body["connection_status"] == "ACTIVE"
    assert body["google_account_email"] == "creator@example.test"
    assert body["missing_scopes"] == []
    assert body["channel"]["youtube_channel_id"] == "UC_systemdecoded_test"
    assert body["channel"]["subscriber_count"] == 1234


async def test_status_warns_while_consent_screen_is_in_testing(client, monkeypatch) -> None:
    """ARCH §3.3 — this is the 7-day refresh-token expiry trap."""
    from app.config import settings

    monkeypatch.setattr(settings, "GOOGLE_CONSENT_PUBLISHING_STATUS", "testing")
    warnings = (await client.get("/api/v1/youtube/status")).json()["warnings"]
    assert any("7 days" in w for w in warnings)


async def test_status_has_no_testing_warning_in_production(client, monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "GOOGLE_CONSENT_PUBLISHING_STATUS", "production")
    warnings = (await client.get("/api/v1/youtube/status")).json()["warnings"]
    assert not any("7 days" in w for w in warnings)


async def test_status_surfaces_the_private_upload_limitation(client) -> None:
    body = (await client.get("/api/v1/youtube/status")).json()
    assert "locked to private" in body["known_limitation"]


# ------------------------------------------------------- 7. refresh handling ---
async def _connect(client, session) -> YouTubeConnection:
    state = await _start_flow(client, session)
    await client.get(
        f"/api/v1/youtube/oauth/callback?code=c&state={state}", follow_redirects=False
    )
    return (await session.execute(select(YouTubeConnection))).scalars().one()


async def test_refresh_replaces_the_access_token(client, session, google) -> None:
    connection = await _connect(client, session)
    assert crypto.decrypt(connection.access_token_enc) == "ya29.initial-access"

    response = await client.post("/api/v1/youtube/refresh")
    assert response.status_code == 200
    assert response.json()["refreshed"] is True

    await session.refresh(connection)
    assert crypto.decrypt(connection.access_token_enc) == "ya29.refreshed-access"
    assert connection.status == ConnectionStatus.ACTIVE


async def test_refresh_keeps_the_existing_refresh_token(client, session, google) -> None:
    """Google omits refresh_token on a refresh response; we must not lose ours."""
    connection = await _connect(client, session)
    await client.post("/api/v1/youtube/refresh")
    await session.refresh(connection)
    assert crypto.decrypt(connection.refresh_token_enc) == "1//initial-refresh"


async def test_expired_access_token_is_refreshed_automatically(client, session, google) -> None:
    connection = await _connect(client, session)
    connection.access_token_expires_at = utcnow() - timedelta(minutes=5)
    await session.commit()

    token = await service.get_access_token(session, connection)
    assert token == "ya29.refreshed-access"


async def test_invalid_grant_marks_the_connection_expired(client, session, google) -> None:
    """The 7-day expiry in practice: degrade visibly, do not crash."""
    connection = await _connect(client, session)
    google.token_status = 400
    google.token_error = {"error": "invalid_grant"}

    with pytest.raises(InvalidGrantError):
        await service.refresh_connection(session, connection)
    # The API request below runs on its own session and connection, so the
    # degraded status has to be committed before it can be observed.
    await session.commit()

    await session.refresh(connection)
    assert connection.status == ConnectionStatus.EXPIRED
    assert "Reconnect" in connection.last_error

    body = (await client.get("/api/v1/youtube/status")).json()
    assert body["connected"] is False
    assert any("expired" in w.lower() for w in body["warnings"])


async def test_refresh_job_reports_invalid_grant_without_failing(client, session, google) -> None:
    """A dead token is expected, so the job must not show as a red failure."""
    from app.jobs import queue
    from app.jobs.runner import run_job

    connection = await _connect(client, session)
    # The job deliberately no-ops unless the token is near expiry, so age it.
    connection.access_token_expires_at = utcnow() - timedelta(minutes=1)
    await session.commit()

    google.token_status = 400
    google.token_error = {"error": "invalid_grant"}

    job_obj, _ = await queue.enqueue(session, "youtube.refresh_tokens", {})
    await session.commit()

    claimed = await queue.claim(session, "test-worker")
    await session.commit()
    status = await run_job(claimed.id, "test-worker")

    assert status == "SUCCEEDED"
    await session.refresh(job_obj)
    assert job_obj.result["refreshed"] is False
    assert job_obj.result["status"] == "EXPIRED"


async def test_refresh_job_skips_when_not_connected(session) -> None:
    from app.jobs import queue
    from app.jobs.runner import run_job

    await queue.enqueue(session, "youtube.refresh_tokens", {})
    await session.commit()
    claimed = await queue.claim(session, "test-worker")
    await session.commit()

    assert await run_job(claimed.id, "test-worker") == "SUCCEEDED"
    await session.refresh(claimed)
    assert claimed.result == {"skipped": "not_connected"}


async def test_refresh_job_skips_when_token_is_still_fresh(client, session, google) -> None:
    """Avoids pointlessly calling Google every 30 minutes."""
    from app.jobs import queue
    from app.jobs.runner import run_job

    await _connect(client, session)
    await queue.enqueue(session, "youtube.refresh_tokens", {})
    await session.commit()
    claimed = await queue.claim(session, "test-worker")
    await session.commit()

    await run_job(claimed.id, "test-worker")
    await session.refresh(claimed)
    assert claimed.result["skipped"] == "not_due"


async def test_invalid_grant_is_not_in_the_retry_set() -> None:
    """Guards the classification that keeps us from hammering Google."""
    from app.jobs.registry import get_definition

    definition = get_definition("youtube.refresh_tokens")
    assert not definition.is_retryable(InvalidGrantError("dead"))


# ---------------------------------------------------------------- disconnect ---
async def test_disconnect_removes_credentials_and_revokes(client, session, google) -> None:
    await _connect(client, session)

    response = await client.post("/api/v1/youtube/disconnect")
    assert response.json()["disconnected"] is True
    assert google.revoked

    assert (await session.execute(select(YouTubeConnection))).scalars().first() is None
    channel = (await session.execute(select(Channel))).scalars().first()
    await session.refresh(channel)
    assert channel.connection_status == ConnectionStatus.NOT_CONNECTED
    # A fresh disconnect must never leave publishing armed.
    assert channel.publishing_enabled is False


async def test_disconnect_is_idempotent(client) -> None:
    assert (await client.post("/api/v1/youtube/disconnect")).json()["disconnected"] is False


async def test_expired_oauth_states_are_purged(session) -> None:
    session.add(
        OAuthState(
            state="old",
            code_verifier="v",
            redirect_uri="http://x/cb",
            expires_at=utcnow() - timedelta(hours=1),
        )
    )
    session.add(
        OAuthState(
            state="fresh",
            code_verifier="v",
            redirect_uri="http://x/cb",
            expires_at=utcnow() + timedelta(hours=1),
        )
    )
    await session.commit()

    assert await service.purge_expired_states(session) == 1
    remaining = (await session.execute(select(OAuthState))).scalars().all()
    assert [r.state for r in remaining] == ["fresh"]
