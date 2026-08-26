"""Channel and application settings.

Only the foundation subset is modelled in Phase 0. The full content hierarchy
(strategy, pillars, formats, ideas, projects, scripts, scenes, renders,
publishing, analytics) arrives in Phases 2-7 per `PHASE-1-ARCHITECTURE.md` §7.

`Channel` exists now because it is the anchor every later entity hangs off, and
because the Settings screen and health checks need something real to read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ConnectionStatus


class Channel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The single channel this instance operates. Single-user, single-channel."""

    __tablename__ = "channel"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tagline: Mapped[str | None] = mapped_column(String(300))
    handle: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)

    language: Mapped[str] = mapped_column(String(10), nullable=False, server_default="en")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    niche: Mapped[str | None] = mapped_column(String(200))
    target_audience: Mapped[str | None] = mapped_column(Text)
    brand_voice: Mapped[str | None] = mapped_column(Text)

    # Populated from channels.list(mine=true) in Phase 1 — never typed by hand.
    youtube_channel_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))

    # contentDetails.relatedPlaylists.uploads. Captured now because Phase 2's
    # MANUAL_HANDOFF reconciliation scans this playlist to match a manually
    # uploaded video back to its project (ARCH §13.5), and it costs nothing to
    # store while we are already calling channels.list.
    uploads_playlist_id: Mapped[str | None] = mapped_column(String(64))

    # Snapshot of channel statistics from the last sync. Point-in-time only —
    # historical analytics get their own age-bucketed tables in Phase 3.
    subscriber_count: Mapped[int | None] = mapped_column(BigInteger)
    video_count: Mapped[int | None] = mapped_column(BigInteger)
    view_count: Mapped[int | None] = mapped_column(BigInteger)
    connection_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ConnectionStatus.NOT_CONNECTED,
        server_default=ConnectionStatus.NOT_CONNECTED,
    )
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Kill switches. Both default to false: a fresh install can never publish
    # by accident (ARCH §17, "always provide a kill switch").
    publishing_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    analytics_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Channel {self.name} status={self.connection_status}>"


class AppSetting(TimestampMixin, Base):
    """Runtime key/value settings that must survive a restart.

    For operational flags that change at runtime. Anything that belongs to
    deployment configuration belongs in `app.config` instead.
    """

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    description: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AppSetting {self.key}>"
