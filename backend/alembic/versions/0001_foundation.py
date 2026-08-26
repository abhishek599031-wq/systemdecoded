"""Phase 0 foundation: job queue, job history, channel, app settings.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-08-24
"""

from __future__ import annotations

from collections.abc import Sequence

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Deterministic id so the seeded channel row is stable across environments and
# re-runs. Valid UUIDv7 layout (version nibble 7, RFC 4122 variant).
#
# Bound as a real uuid.UUID, not a str: sa.text().bindparams() infers a bare
# string's type as VARCHAR, and psycopg then refuses to insert it into the
# UUID `id` column ("column is of type uuid but expression is of type
# character varying"). `alembic upgrade head --sql` did not catch this — the
# offline renderer emits literal SQL without type-checking params against
# columns; only a live `psycopg` execution does.
CHANNEL_ID = uuid.UUID("01920000-0000-7000-8000-000000000001")


def upgrade() -> None:
    # ------------------------------------------------------------- channel ---
    op.create_table(
        "channel",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("tagline", sa.String(length=300), nullable=True),
        sa.Column("handle", sa.String(length=100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=10), server_default="en", nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
        sa.Column("niche", sa.String(length=200), nullable=True),
        sa.Column("target_audience", sa.Text(), nullable=True),
        sa.Column("brand_voice", sa.Text(), nullable=True),
        sa.Column("youtube_channel_id", sa.String(length=64), nullable=True),
        sa.Column(
            "connection_status",
            sa.String(length=20),
            server_default="NOT_CONNECTED",
            nullable=False,
        ),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "publishing_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "analytics_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_channel"),
        sa.UniqueConstraint("youtube_channel_id", name="uq_channel_youtube_channel_id"),
    )

    # --------------------------------------------------------- app_setting ---
    op.create_table(
        "app_setting",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_app_setting"),
    )

    # ------------------------------------------------------ background_job ---
    op.create_table(
        "background_job",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), server_default="QUEUED", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default="300", nullable=False),
        sa.Column("claimed_by", sa.String(length=100), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        # FK to content_project is added by the Phase 2 migration that creates
        # that table. The column exists now so job history is never orphaned
        # from its domain object (ADR 0001).
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_class", sa.String(length=200), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("traceback", sa.Text(), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_background_job"),
    )

    # Raw SQL for the indexes that need DESC ordering or a WHERE clause —
    # op.create_index cannot express either portably.

    # The claim query. Partial, so it stays small no matter how much terminal
    # history accumulates in the table.
    op.execute(
        """
        CREATE INDEX ix_background_job_claim
            ON background_job (priority DESC, run_after)
         WHERE status = 'QUEUED'
        """
    )
    # The reaper's query: RUNNING rows whose heartbeat has gone stale.
    op.execute(
        """
        CREATE INDEX ix_background_job_stale
            ON background_job (heartbeat_at)
         WHERE status = 'RUNNING'
        """
    )
    op.execute(
        "CREATE INDEX ix_background_job_project_id "
        "ON background_job (project_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_background_job_job_type "
        "ON background_job (job_type, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_background_job_status ON background_job (status, created_at DESC)"
    )
    # Enqueue idempotency. NULL keys do not collide with each other, which is
    # exactly right: a job without a key is always a new unit of work.
    op.execute(
        "CREATE UNIQUE INDEX uq_background_job_idempotency_key "
        "ON background_job (idempotency_key)"
    )

    # ----------------------------------------------------------- job_event ---
    op.create_table(
        "job_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_job_event"),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["background_job.id"],
            name="fk_job_event_job_id_background_job",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_job_event_job_id", "job_event", ["job_id", "created_at"])

    # ---------------------------------------------------------------- seed ---
    # One channel row, seeded here rather than at application startup so it is
    # deterministic and free of a multi-process race.
    op.execute(
        sa.text(
            """
            INSERT INTO channel (
                id, name, tagline, description, language, timezone,
                niche, target_audience, brand_voice, connection_status,
                publishing_enabled, analytics_enabled
            ) VALUES (
                :id, :name, :tagline, :description, 'en', 'UTC',
                :niche, :audience, :voice, 'NOT_CONNECTED',
                false, false
            )
            ON CONFLICT (id) DO NOTHING
            """
        ).bindparams(
            id=CHANNEL_ID,
            name="SystemDecoded",
            tagline="Complex Technology. Decoded.",
            description=(
                "Complicated technology explained through short, highly visual stories "
                "that anyone can understand."
            ),
            niche="AI + Technology Edutainment",
            audience="English-speaking global audience, roughly 18-35, technically curious, "
            "developers and non-developers alike.",
            voice=(
                "Curious and direct. Explains the hidden system behind an everyday action. "
                "No greetings, no fake urgency, no filler."
            ),
        )
    )


def downgrade() -> None:
    op.drop_index("ix_job_event_job_id", table_name="job_event")
    op.drop_table("job_event")
    op.execute("DROP INDEX IF EXISTS uq_background_job_idempotency_key")
    op.execute("DROP INDEX IF EXISTS ix_background_job_status")
    op.execute("DROP INDEX IF EXISTS ix_background_job_job_type")
    op.execute("DROP INDEX IF EXISTS ix_background_job_project_id")
    op.execute("DROP INDEX IF EXISTS ix_background_job_stale")
    op.execute("DROP INDEX IF EXISTS ix_background_job_claim")
    op.drop_table("background_job")
    op.drop_table("app_setting")
    op.drop_table("channel")
