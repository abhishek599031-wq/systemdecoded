"""YouTube Data API v3 client.

Phase 1 needs exactly one call: `channels.list(mine=true)`. The channel ID is
always fetched from Google, never typed by a human (ARCH §13.1).

Quota note (verified, ARCH §3.2): `channels.list` costs 1 unit against the
shared 10,000/day pool. The scarce buckets are `search.list` (100 calls/day) and
`videos.insert` (100 calls/day), neither of which Phase 1 touches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.logging import get_logger
from app.integrations.youtube.errors import (
    NoChannelError,
    TransientGoogleError,
    classify_api_error,
)

log = get_logger("youtube.data_api")

API_BASE = "https://www.googleapis.com/youtube/v3"
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


@dataclass(frozen=True, slots=True)
class ChannelSnapshot:
    """The subset of channels.list we persist."""

    youtube_channel_id: str
    title: str
    description: str | None
    handle: str | None
    thumbnail_url: str | None
    uploads_playlist_id: str | None
    subscriber_count: int | None
    video_count: int | None
    view_count: int | None
    country: str | None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _get(path: str, access_token: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                f"{API_BASE}/{path}",
                params=params,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        raise TransientGoogleError(f"YouTube API unreachable: {type(exc).__name__}") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code >= 400:
        raise classify_api_error(response.status_code, payload)
    return payload


async def fetch_my_channel(access_token: str) -> ChannelSnapshot:
    """Fetch the authorised account's channel.

    Raises NoChannelError when the Google account has no YouTube channel — a
    real and confusing case, since consent succeeds and only this call reveals it.
    """
    payload = await _get(
        "channels",
        access_token,
        {"part": "snippet,statistics,contentDetails", "mine": "true"},
    )

    items = payload.get("items") or []
    if not items:
        raise NoChannelError()

    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    related = item.get("contentDetails", {}).get("relatedPlaylists", {})
    thumbnails = snippet.get("thumbnails", {})
    best = thumbnails.get("high") or thumbnails.get("medium") or thumbnails.get("default") or {}

    snapshot = ChannelSnapshot(
        youtube_channel_id=item["id"],
        title=snippet.get("title", ""),
        description=snippet.get("description") or None,
        handle=snippet.get("customUrl") or None,
        thumbnail_url=best.get("url"),
        uploads_playlist_id=related.get("uploads"),
        # hiddenSubscriberCount channels omit subscriberCount entirely.
        subscriber_count=_int_or_none(stats.get("subscriberCount")),
        video_count=_int_or_none(stats.get("videoCount")),
        view_count=_int_or_none(stats.get("viewCount")),
        country=snippet.get("country"),
    )
    log.info(
        "youtube.channel_fetched",
        youtube_channel_id=snapshot.youtube_channel_id,
        handle=snapshot.handle,
        video_count=snapshot.video_count,
    )
    return snapshot
