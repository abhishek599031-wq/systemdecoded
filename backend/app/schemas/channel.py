"""Channel API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    tagline: str | None = None
    handle: str | None = None
    description: str | None = None
    language: str
    timezone: str
    niche: str | None = None
    target_audience: str | None = None
    brand_voice: str | None = None
    youtube_channel_id: str | None = None
    connection_status: str
    connected_at: datetime | None = None
    last_sync_at: datetime | None = None
    publishing_enabled: bool
    analytics_enabled: bool
    created_at: datetime
    updated_at: datetime


class ChannelUpdate(BaseModel):
    """Editable channel fields.

    `youtube_channel_id` is deliberately absent: it is fetched from
    channels.list(mine=true) during OAuth and never typed by hand
    (`PHASE-1-ARCHITECTURE.md` §13.1).
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    tagline: str | None = Field(default=None, max_length=300)
    handle: str | None = Field(default=None, max_length=100)
    description: str | None = None
    language: str | None = Field(default=None, max_length=10)
    timezone: str | None = Field(default=None, max_length=64)
    niche: str | None = Field(default=None, max_length=200)
    target_audience: str | None = None
    brand_voice: str | None = None
    publishing_enabled: bool | None = None
    analytics_enabled: bool | None = None
