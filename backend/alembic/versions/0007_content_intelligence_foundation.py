"""Add authoritative windows and content feature profiles.

Revision ID: 0007_content_intelligence
Revises: 0006_youtube_analytics_daily
Create Date: 2026-10-05 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_content_intelligence"
down_revision: str | None = "0006_youtube_analytics_daily"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "youtube_video_window_metric",
        sa.Column("published_video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("window_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("window_start_date", sa.Date(), nullable=False),
        sa.Column("window_end_date", sa.Date(), nullable=False),
        sa.Column("views", sa.BigInteger()),
        sa.Column("engaged_views", sa.BigInteger()),
        sa.Column("likes", sa.BigInteger()),
        sa.Column("comments", sa.BigInteger()),
        sa.Column("shares", sa.BigInteger()),
        sa.Column("subscribers_gained", sa.BigInteger()),
        sa.Column("subscribers_lost", sa.BigInteger()),
        sa.Column("estimated_minutes_watched", sa.Numeric(20, 6)),
        sa.Column("average_view_duration", sa.Numeric(20, 6)),
        sa.Column("average_view_percentage", sa.Numeric(12, 6)),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String()),
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
            name="fk_yt_window_metric_published_video",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "published_video_id", "window_type", name="uq_yt_window_metric_video_window"
        ),
    )
    op.create_index(
        "ix_yt_window_metric_type_status",
        "youtube_video_window_metric",
        ["window_type", "status"],
    )

    op.create_table(
        "content_feature_profile",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Numeric(10, 3)),
        sa.Column("narration_duration_seconds", sa.Numeric(10, 3)),
        sa.Column("scene_count", sa.Integer()),
        sa.Column("script_word_count", sa.Integer()),
        sa.Column("narration_word_count", sa.Integer()),
        sa.Column("narration_words_per_minute", sa.Numeric(10, 3)),
        sa.Column("voice_provider", sa.String(length=100)),
        sa.Column("voice_model", sa.String(length=200)),
        sa.Column("voice_name", sa.String(length=100)),
        sa.Column("tts_speed", sa.Numeric(6, 3)),
        sa.Column("caption_event_count", sa.Integer()),
        sa.Column("render_width", sa.Integer()),
        sa.Column("render_height", sa.Integer()),
        sa.Column(
            "template_families",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column("content_format", sa.String(length=100)),
        sa.Column("published_title", sa.String(length=300)),
        sa.Column("cta_present", sa.Boolean()),
        sa.Column("topic_category", sa.String(length=100)),
        sa.Column("topic", sa.String(length=300)),
        sa.Column("subtopic", sa.String(length=300)),
        sa.Column("hook_type", sa.String(length=50)),
        sa.Column("hook_text", sa.String(length=500)),
        sa.Column("title_style", sa.String(length=50)),
        sa.Column("explanation_pattern", sa.String(length=300)),
        sa.Column("audience_level", sa.String(length=50)),
        sa.Column("technical_depth", sa.String(length=50)),
        sa.Column("cta_type", sa.String(length=100)),
        sa.Column("visual_storytelling_style", sa.String(length=300)),
        sa.Column("question_answered", sa.String(length=500)),
        sa.Column(
            "field_provenance",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("derived_at", sa.DateTime(timezone=True)),
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
            ["project_id"],
            ["content_project.id"],
            name="fk_content_feature_profile_project",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("content_feature_profile")
    op.drop_index("ix_yt_window_metric_type_status", table_name="youtube_video_window_metric")
    op.drop_table("youtube_video_window_metric")
