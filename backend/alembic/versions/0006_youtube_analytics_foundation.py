"""Add canonical daily YouTube analytics storage.

Revision ID: 0006_youtube_analytics_daily
Revises: 0005_publication_state_hardening
Create Date: 2026-09-22 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Alembic's default version table uses VARCHAR(32), so keep the stored
# revision identifier within that limit even though the descriptive filename
# is longer.
revision: str = "0006_youtube_analytics_daily"
down_revision: str | None = "0005_publication_state_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "youtube_video_daily_metric",
        sa.Column("published_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("views", sa.BigInteger(), nullable=True),
        sa.Column("engaged_views", sa.BigInteger(), nullable=True),
        sa.Column("likes", sa.BigInteger(), nullable=True),
        sa.Column("comments", sa.BigInteger(), nullable=True),
        sa.Column("shares", sa.BigInteger(), nullable=True),
        sa.Column("subscribers_gained", sa.BigInteger(), nullable=True),
        sa.Column("subscribers_lost", sa.BigInteger(), nullable=True),
        sa.Column("estimated_minutes_watched", sa.Numeric(20, 6), nullable=True),
        sa.Column("average_view_duration", sa.Numeric(20, 6), nullable=True),
        sa.Column("average_view_percentage", sa.Numeric(12, 6), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["published_video_id"],
            ["published_video.id"],
            name="fk_yt_daily_metric_published_video",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "published_video_id",
            "metric_date",
            name="uq_youtube_video_daily_metric_video_date",
        ),
    )
    op.create_index(
        "ix_youtube_video_daily_metric_date_video",
        "youtube_video_daily_metric",
        ["metric_date", "published_video_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_youtube_video_daily_metric_date_video",
        table_name="youtube_video_daily_metric",
    )
    op.drop_table("youtube_video_daily_metric")
