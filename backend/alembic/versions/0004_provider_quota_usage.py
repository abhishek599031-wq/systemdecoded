"""Provider request accounting, so a render can know its quota before it starts.

Revision ID: 0004_provider_quota_usage
Revises: 0003_content_production
Create Date: 2026-09-07 05:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_provider_quota_usage"
down_revision: str | None = "0003_content_production"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_quota_usage",
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("requests_used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("observed_limit", sa.Integer(), nullable=True),
        sa.Column("exhausted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_quota_usage")),
        sa.UniqueConstraint(
            "provider", "model", "usage_date", name="uq_provider_quota_usage_day"
        ),
    )


def downgrade() -> None:
    op.drop_table("provider_quota_usage")
