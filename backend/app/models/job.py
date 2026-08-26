"""Background job models.

`BackgroundJob` is both the queue and the audit record. Owning this schema is
the reason we chose a custom queue over Procrastinate — see ADR 0001.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import JobStatus


class BackgroundJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "background_job"

    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JobStatus.QUEUED, server_default=JobStatus.QUEUED
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300, server_default="300"
    )

    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Enqueue guard. Unique, so a duplicate enqueue is a no-op rather than a
    # second unit of work (ARCH §9.2, §13.4).
    idempotency_key: Mapped[str | None] = mapped_column(String(255))

    # Domain linkage. Deliberately a first-class column so the dashboard can
    # join rather than dig through JSONB (ADR 0001). The FK to content_project
    # is added in the Phase 2 migration that creates that table.
    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    error_class: Mapped[str | None] = mapped_column(String(200))
    error_message: Mapped[str | None] = mapped_column(Text)
    traceback: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    events: Mapped[list[JobEvent]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobEvent.created_at",
        lazy="selectin",
    )

    __table_args__ = (
        # The claim query. Partial so the index only covers claimable rows,
        # which keeps it small no matter how much history accumulates.
        Index(
            "ix_background_job_claim",
            text("priority DESC"),
            "run_after",
            postgresql_where=text("status = 'QUEUED'"),
        ),
        # The reaper's query: RUNNING jobs whose heartbeat has gone stale.
        Index(
            "ix_background_job_stale",
            "heartbeat_at",
            postgresql_where=text("status = 'RUNNING'"),
        ),
        Index("ix_background_job_project_id", "project_id", text("created_at DESC")),
        Index("ix_background_job_job_type", "job_type", text("created_at DESC")),
        Index("ix_background_job_status", "status", text("created_at DESC")),
        Index("uq_background_job_idempotency_key", "idempotency_key", unique=True),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<BackgroundJob {self.job_type} {self.status} "
            f"attempt={self.attempt}/{self.max_attempts} id={self.id}>"
        )


class JobEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only attempt history.

    `BackgroundJob` holds current state; this holds what happened. Separating
    them means a retry does not overwrite the record of why the previous
    attempt failed.
    """

    __tablename__ = "job_event"

    job_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("background_job.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    message: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    job: Mapped[BackgroundJob] = relationship(back_populates="events")

    __table_args__ = (Index("ix_job_event_job_id", "job_id", "created_at"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<JobEvent {self.event_type} attempt={self.attempt} job={self.job_id}>"
