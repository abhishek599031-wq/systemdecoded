"""Canonical daily YouTube analytics models (ADR 0003)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AnalyticsWindowStatus

if TYPE_CHECKING:  # pragma: no cover
    from app.models.content import PublishedVideo


class YouTubeVideoDailyMetric(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One provider reporting day for one published YouTube video."""

    __tablename__ = "youtube_video_daily_metric"

    published_video_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "published_video.id",
            name="fk_yt_daily_metric_published_video",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)

    views: Mapped[int | None] = mapped_column(BigInteger)
    engaged_views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    subscribers_gained: Mapped[int | None] = mapped_column(BigInteger)
    subscribers_lost: Mapped[int | None] = mapped_column(BigInteger)
    estimated_minutes_watched: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    average_view_duration: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    average_view_percentage: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    published_video: Mapped[PublishedVideo] = relationship(back_populates="daily_metrics")

    __table_args__ = (
        UniqueConstraint(
            "published_video_id",
            "metric_date",
            name="uq_youtube_video_daily_metric_video_date",
        ),
        Index(
            "ix_youtube_video_daily_metric_date_video",
            "metric_date",
            "published_video_id",
        ),
    )


class YouTubeVideoWindowMetric(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Provider-authoritative aggregate for one fixed lifecycle window."""

    __tablename__ = "youtube_video_window_metric"

    published_video_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "published_video.id",
            name="fk_yt_window_metric_published_video",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    window_type: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(
        nullable=False,
        default=AnalyticsWindowStatus.PENDING,
    )
    window_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    window_end_date: Mapped[date] = mapped_column(Date, nullable=False)

    views: Mapped[int | None] = mapped_column(BigInteger)
    engaged_views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    shares: Mapped[int | None] = mapped_column(BigInteger)
    subscribers_gained: Mapped[int | None] = mapped_column(BigInteger)
    subscribers_lost: Mapped[int | None] = mapped_column(BigInteger)
    estimated_minutes_watched: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    average_view_duration: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    average_view_percentage: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))

    provider: Mapped[str] = mapped_column(nullable=False, default="youtube_analytics")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None]

    published_video: Mapped[PublishedVideo] = relationship(back_populates="window_metrics")

    __table_args__ = (
        UniqueConstraint(
            "published_video_id",
            "window_type",
            name="uq_yt_window_metric_video_window",
        ),
        Index(
            "ix_yt_window_metric_type_status",
            "window_type",
            "status",
        ),
    )
