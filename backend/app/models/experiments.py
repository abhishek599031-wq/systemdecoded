"""Planned creative intent for controlled content experiments."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ExperimentStatus

if TYPE_CHECKING:  # pragma: no cover
    from app.models.content import ContentProject


class ContentExperiment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One immutable-after-planning statement of creative intent."""

    __tablename__ = "content_experiment"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("content_project.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    control_reference_project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("content_project.id", ondelete="SET NULL"),
    )
    experiment_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=ExperimentStatus.DRAFT.value
    )
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    primary_variable: Mapped[str] = mapped_column(String(100), nullable=False)

    planned_topic_category: Mapped[str | None] = mapped_column(String(100))
    planned_topic: Mapped[str | None] = mapped_column(String(300))
    planned_hook_type: Mapped[str | None] = mapped_column(String(50))
    planned_duration_min_seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    planned_duration_max_seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    planned_explanation_pattern: Mapped[str | None] = mapped_column(String(300))
    planned_audience_level: Mapped[str | None] = mapped_column(String(50))
    planned_technical_depth: Mapped[str | None] = mapped_column(String(50))
    planned_title_style: Mapped[str | None] = mapped_column(String(50))

    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    project: Mapped[ContentProject] = relationship(
        foreign_keys=[project_id], back_populates="experiment", lazy="selectin"
    )
    control_reference_project: Mapped[ContentProject | None] = relationship(
        foreign_keys=[control_reference_project_id], lazy="selectin"
    )
