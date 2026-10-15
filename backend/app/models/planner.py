"""Persisted, reviewable Phase 6A planning decisions.

Planner output is deliberately separate from projects.  A run can fail, be
replayed, or produce rejected candidates without creating accidental content.
Only one selected proposal is allowed to create one draft project.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from app.models.content import ContentProject


class PlannerRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "planner_run"

    trigger: Mapped[str] = mapped_column(String(40), nullable=False, server_default="MANUAL")
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="PENDING")
    provider: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(200))
    planner_version: Mapped[str] = mapped_column(String(50), nullable=False, server_default="6A.0")
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    decision_summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    proposals: Mapped[list[ContentProposal]] = relationship(
        back_populates="planner_run", cascade="all, delete-orphan", order_by="ContentProposal.rank"
    )

    __table_args__ = (Index("ix_planner_run_status", "status", text("created_at DESC")),)


class ContentProposal(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_proposal"

    planner_run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("planner_run.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(30), nullable=False, server_default="CANDIDATE")
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    topic_key: Mapped[str] = mapped_column(String(300), nullable=False)
    content_pillar: Mapped[str] = mapped_column(String(100), nullable=False)
    core_question: Mapped[str] = mapped_column(Text, nullable=False)
    hook: Mapped[str] = mapped_column(Text, nullable=False)
    hook_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    why_now: Mapped[str] = mapped_column(Text, nullable=False)
    novelty_assessment: Mapped[str] = mapped_column(Text, nullable=False)
    research_feasibility: Mapped[str] = mapped_column(Text, nullable=False)
    visual_feasibility: Mapped[str] = mapped_column(Text, nullable=False)
    experiment_hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    primary_variable: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_limitations: Mapped[str] = mapped_column(Text, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    scoring: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("content_project.id", ondelete="SET NULL"), unique=True
    )

    planner_run: Mapped[PlannerRun] = relationship(back_populates="proposals")
    project: Mapped[ContentProject | None] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("planner_run_id", "rank", name="uq_content_proposal_run_rank"),
        Index("ix_content_proposal_topic_key", "topic_key"),
    )
