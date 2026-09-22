"""Harden manual publication identity and metadata.

Revision ID: 0005_publication_state_hardening
Revises: 0004_provider_quota_usage
Create Date: 2026-09-22 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_publication_state_hardening"
down_revision: str | None = "0004_provider_quota_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Legacy rows predate verified metadata. Preserve them with the strongest
    # timestamp/provenance available rather than making the migration lossy.
    op.execute(
        """
        UPDATE published_video
           SET published_at = COALESCE(published_at, reconciled_at, created_at, now()),
               reconciliation_method = COALESCE(reconciliation_method, 'legacy_manual')
         WHERE published_at IS NULL OR reconciliation_method IS NULL
        """
    )
    op.alter_column(
        "published_video",
        "published_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.alter_column(
        "published_video",
        "reconciliation_method",
        existing_type=sa.String(length=40),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_published_video_publishing_job_id_publishing_job",
        "published_video",
        "publishing_job",
        ["publishing_job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_published_video_project_id",
        "published_video",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_published_video_project_id", "published_video", type_="unique"
    )
    op.drop_constraint(
        "fk_published_video_publishing_job_id_publishing_job",
        "published_video",
        type_="foreignkey",
    )
    op.alter_column(
        "published_video",
        "reconciliation_method",
        existing_type=sa.String(length=40),
        nullable=True,
    )
    op.alter_column(
        "published_video",
        "published_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )
