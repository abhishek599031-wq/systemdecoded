"""Add planned content experiments."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_content_experiments"
down_revision: str | None = "0007_content_intelligence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "content_experiment",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("control_reference_project_id", postgresql.UUID(as_uuid=True)),
        sa.Column("experiment_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="DRAFT"),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("primary_variable", sa.String(length=100), nullable=False),
        sa.Column("planned_topic_category", sa.String(length=100)),
        sa.Column("planned_topic", sa.String(length=300)),
        sa.Column("planned_hook_type", sa.String(length=50)),
        sa.Column("planned_duration_min_seconds", sa.Numeric(10, 3)),
        sa.Column("planned_duration_max_seconds", sa.Numeric(10, 3)),
        sa.Column("planned_explanation_pattern", sa.String(length=300)),
        sa.Column("planned_audience_level", sa.String(length=50)),
        sa.Column("planned_technical_depth", sa.String(length=50)),
        sa.Column("planned_title_style", sa.String(length=50)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "locked_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
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
            name="fk_content_experiment_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["control_reference_project_id"],
            ["content_project.id"],
            name="fk_content_experiment_control_project",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("project_id", name="uq_content_experiment_project"),
    )
    op.create_index(
        "ix_content_experiment_status",
        "content_experiment",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_content_experiment_status", table_name="content_experiment")
    op.drop_table("content_experiment")
