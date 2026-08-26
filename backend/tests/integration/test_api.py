"""API surface tests against the real ASGI app and a real database."""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from app.jobs import queue


# ------------------------------------------------------------------- health ---
async def test_liveness_never_touches_the_database(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"
    assert response.json()["app"] == "SystemDecoded"


async def test_readiness_reports_database_and_migrations(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["migrations"]["ok"] is True
    assert body["checks"]["migrations"]["revision"] is not None


async def test_every_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.headers["X-Request-ID"]


async def test_supplied_request_id_is_echoed_back(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"X-Request-ID": "trace-me"})
    assert response.headers["X-Request-ID"] == "trace-me"


# ------------------------------------------------------------------- system ---
async def test_system_status_aggregates_the_dashboard_view(client: AsyncClient) -> None:
    body = (await client.get("/api/v1/system/status")).json()
    assert body["status"] == "healthy"
    assert body["database"]["connected"] is True
    assert body["database"]["migration_revision"] is not None
    assert "QUEUED" in body["jobs"]
    assert "RETRY_PENDING" in body["jobs"]
    assert body["heartbeat"]["stale"] is True  # nothing has run yet in a clean database


async def test_system_status_reports_integrations_honestly(client: AsyncClient) -> None:
    """Each integration reports its real state, never an aspirational one."""
    integrations = (await client.get("/api/v1/system/status")).json()["integrations"]
    # YouTube is implemented as of Phase 1.
    assert integrations["youtube"]["status"] == "implemented"
    assert integrations["youtube"]["connection_status"] == "NOT_CONNECTED"
    assert integrations["youtube"]["connected"] is False
    # The LLM providers genuinely are not built yet, and still say so.
    assert integrations["llm"]["status"] == "not_implemented_until_phase_4"


async def test_system_info_declares_no_capabilities_yet(client: AsyncClient) -> None:
    body = (await client.get("/api/v1/system/info")).json()
    assert body["phase"] == 0
    assert not any(body["capabilities"].values())


# --------------------------------------------------------------------- jobs ---
async def test_job_types_are_discoverable(client: AsyncClient) -> None:
    types = (await client.get("/api/v1/jobs/types")).json()
    names = {t["name"] for t in types}
    assert "system.ping" in names
    ping = next(t for t in types if t["name"] == "system.ping")
    assert ping["max_attempts"] == 1
    assert isinstance(ping["retry_on"], list)


async def test_enqueue_then_fetch_a_job(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/jobs", json={"job_type": "system.ping", "payload": {"x": 1}}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    job_id = body["job"]["id"]

    detail = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert detail["status"] == "QUEUED"
    assert detail["payload"] == {"x": 1}
    assert [e["event_type"] for e in detail["events"]] == ["ENQUEUED"]


async def test_enqueue_is_idempotent_over_the_api(client: AsyncClient) -> None:
    payload = {"job_type": "system.ping", "payload": {}, "idempotency_key": "api-key-1"}
    first = (await client.post("/api/v1/jobs", json=payload)).json()
    second = (await client.post("/api/v1/jobs", json=payload)).json()

    assert first["created"] is True
    assert second["created"] is False
    assert first["job"]["id"] == second["job"]["id"]


async def test_unknown_job_type_is_rejected_with_the_valid_set(client: AsyncClient) -> None:
    response = await client.post("/api/v1/jobs", json={"job_type": "nope.nope"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert "system.ping" in error["detail"]["registered"]


async def test_list_jobs_filters_and_paginates(client: AsyncClient, session) -> None:
    for i in range(3):
        await queue.enqueue(session, "system.ping", {"i": i})
    await queue.enqueue(session, "system.sleep", {"seconds": 1})
    await session.commit()

    all_jobs = (await client.get("/api/v1/jobs")).json()
    assert all_jobs["total"] == 4

    pings = (await client.get("/api/v1/jobs", params={"job_type": "system.ping"})).json()
    assert pings["total"] == 3

    queued = (await client.get("/api/v1/jobs", params={"status": "QUEUED"})).json()
    assert queued["total"] == 4

    page = (await client.get("/api/v1/jobs", params={"limit": 2, "offset": 0})).json()
    assert len(page["items"]) == 2
    assert page["total"] == 4


async def test_list_jobs_rejects_an_invalid_status(client: AsyncClient) -> None:
    response = await client.get("/api/v1/jobs", params={"status": "BANANA"})
    assert response.status_code == 422
    assert "QUEUED" in response.json()["error"]["detail"]["valid"]


async def test_missing_job_returns_a_structured_404(client: AsyncClient) -> None:
    response = await client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"]


async def test_cancel_a_queued_job(client: AsyncClient) -> None:
    job_id = (
        await client.post("/api/v1/jobs", json={"job_type": "system.ping"})
    ).json()["job"]["id"]

    cancelled = (await client.post(f"/api/v1/jobs/{job_id}/cancel")).json()
    assert cancelled["status"] == "CANCELLED"


async def test_cannot_requeue_a_job_that_is_still_queued(client: AsyncClient) -> None:
    job_id = (
        await client.post("/api/v1/jobs", json={"job_type": "system.ping"})
    ).json()["job"]["id"]

    response = await client.post(f"/api/v1/jobs/{job_id}/requeue", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


# ------------------------------------------------------------------ channel ---
async def test_channel_is_seeded_by_the_migration(client: AsyncClient) -> None:
    body = (await client.get("/api/v1/channel")).json()
    assert body["name"] == "SystemDecoded"
    assert body["tagline"] == "Complex Technology. Decoded."
    assert body["niche"] == "AI + Technology Edutainment"
    assert body["connection_status"] == "NOT_CONNECTED"
    # A fresh install must never be able to publish by accident.
    assert body["publishing_enabled"] is False


async def test_channel_can_be_updated(client: AsyncClient) -> None:
    response = await client.patch(
        "/api/v1/channel", json={"handle": "@systemdecoded", "timezone": "Asia/Kolkata"}
    )
    assert response.status_code == 200
    assert response.json()["handle"] == "@systemdecoded"
    assert response.json()["timezone"] == "Asia/Kolkata"

    # Persisted, not just echoed.
    assert (await client.get("/api/v1/channel")).json()["handle"] == "@systemdecoded"


async def test_channel_update_rejects_an_empty_name(client: AsyncClient) -> None:
    assert (await client.patch("/api/v1/channel", json={"name": ""})).status_code == 422


# ------------------------------------------------------------------ youtube ---
async def test_youtube_status_answers_when_not_connected(client: AsyncClient) -> None:
    """/status must always answer, connected or not — it backs the dashboard."""
    body = (await client.get("/api/v1/youtube/status")).json()
    assert body["implemented"] is True
    assert body["connected"] is False
    assert body["connection_status"] == "NOT_CONNECTED"
    # The audit constraint is surfaced permanently because it governs Phase 6.
    assert "locked to private" in body["known_limitation"]


async def test_youtube_status_never_returns_credentials(client: AsyncClient) -> None:
    """A safety net independent of the OAuth suite: /status is screenshot-safe.

    Checks secret *values*, not field names — `access_token_expires_at` is a
    legitimate, non-sensitive field and must not trip this.
    """
    from app.config import settings

    body = (await client.get("/api/v1/youtube/status")).text

    if settings.GOOGLE_CLIENT_SECRET:
        assert settings.GOOGLE_CLIENT_SECRET not in body
    # Google's token value prefixes: access tokens start "ya29.", refresh
    # tokens "1//". Neither may ever appear in a response body.
    assert "ya29." not in body
    assert '"1//' not in body
