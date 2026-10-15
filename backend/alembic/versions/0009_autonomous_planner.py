"""Add persisted Phase 6A planning runs and content proposals."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_autonomous_planner"
down_revision: str | None = "0008_content_experiments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planner_run",
        sa.Column("trigger", sa.String(40), nullable=False, server_default="MANUAL"),
        sa.Column("status", sa.String(30), nullable=False, server_default="PENDING"),
        sa.Column("provider", sa.String(100)), sa.Column("model", sa.String(200)),
        sa.Column("planner_version", sa.String(50), nullable=False, server_default="6A.0"),
        sa.Column("context_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("decision_summary", sa.Text()), sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_planner_run_status", "planner_run", ["status", sa.text("created_at DESC")])
    op.create_table(
        "content_proposal",
        sa.Column("planner_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False), sa.Column("state", sa.String(30), nullable=False, server_default="CANDIDATE"),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("topic", sa.String(300), nullable=False), sa.Column("topic_key", sa.String(300), nullable=False),
        sa.Column("content_pillar", sa.String(100), nullable=False), sa.Column("core_question", sa.Text(), nullable=False),
        sa.Column("hook", sa.Text(), nullable=False), sa.Column("hook_type", sa.String(80), nullable=False),
        sa.Column("target_duration_seconds", sa.Integer(), nullable=False), sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("why_now", sa.Text(), nullable=False), sa.Column("novelty_assessment", sa.Text(), nullable=False),
        sa.Column("research_feasibility", sa.Text(), nullable=False), sa.Column("visual_feasibility", sa.Text(), nullable=False),
        sa.Column("experiment_hypothesis", sa.Text(), nullable=False), sa.Column("primary_variable", sa.String(100), nullable=False),
        sa.Column("evidence_limitations", sa.Text(), nullable=False), sa.Column("rejection_reason", sa.Text()),
        sa.Column("scoring", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("project_id", postgresql.UUID(as_uuid=True)),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["planner_run_id"], ["planner_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["content_project.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("planner_run_id", "rank", name="uq_content_proposal_run_rank"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_index("ix_content_proposal_topic_key", "content_proposal", ["topic_key"])


def downgrade() -> None:
    op.drop_index("ix_content_proposal_topic_key", table_name="content_proposal")
    op.drop_table("content_proposal")
    op.drop_index("ix_planner_run_status", table_name="planner_run")
    op.drop_table("planner_run")
