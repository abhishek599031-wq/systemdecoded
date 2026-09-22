"""Pure validation and mocked YouTube Data API coverage for Phase 2.5."""

from __future__ import annotations

import httpx
import pytest

from app.core.errors import ValidationError
from app.integrations.youtube import data_api
from app.services.publication_reconciliation import validate_youtube_video_id


@pytest.mark.parametrize(
    "value",
    ["", "short", "has spaces!", "abcdefghijkL", "https://youtu.be/abcdefghijk"],
)
def test_invalid_youtube_video_id(value: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        validate_youtube_video_id(value)
    assert exc_info.value.code == "invalid_youtube_video_id"


def test_valid_youtube_video_id_is_trimmed() -> None:
    assert validate_youtube_video_id("  Ab_cd-12345  ") == "Ab_cd-12345"


async def test_fetch_video_uses_authenticated_videos_list(monkeypatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers["Authorization"]
        seen["part"] = request.url.params["part"]
        seen["id"] = request.url.params["id"]
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "Ab_cd-12345",
                        "snippet": {
                            "channelId": "UC_systemdecoded",
                            "channelTitle": "SystemDecoded",
                            "title": "Nobody sent you that authenticator code",
                            "publishedAt": "2026-09-10T12:30:00Z",
                        },
                        "status": {"privacyStatus": "public"},
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(data_api.httpx, "AsyncClient", patched)
    video = await data_api.fetch_video("access-token", "Ab_cd-12345")

    assert video is not None
    assert video.youtube_channel_id == "UC_systemdecoded"
    assert video.title == "Nobody sent you that authenticator code"
    assert video.published_at.isoformat() == "2026-09-10T12:30:00+00:00"
    assert video.privacy_status == "public"
    assert seen == {
        "authorization": "Bearer access-token",
        "part": "snippet,status",
        "id": "Ab_cd-12345",
    }


async def test_fetch_video_returns_none_when_youtube_has_no_item(monkeypatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"items": []}))
    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(data_api.httpx, "AsyncClient", patched)
    assert await data_api.fetch_video("access-token", "Ab_cd-12345") is None
