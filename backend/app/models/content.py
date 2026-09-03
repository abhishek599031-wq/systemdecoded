"""Content production models (Phase 2).

The hierarchy from ARCH §7, implemented to the depth Phase 2 actually needs:

    ContentProject
        ├── ResearchNote  ──> ResearchSource
        ├── Script ──> Scene ──> ProductionAsset
        ├── VideoRender ──> QualityCheck
        └── PublishingJob ──> PublishedVideo

Facts are deliberately stored separately from narration: `ResearchNote.claim`
is what we assert is true, `Scene.narration` is how we say it. Keeping them
apart is what makes "every factual claim traces to a source" checkable rather
than aspirational.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AssetOrigin,
    ProjectStatus,
    PublishingMode,
    PublishState,
    QualityVerdict,
    RenderStatus,
)


class ContentProject(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One video, from topic to publication. The aggregate root."""

    __tablename__ = "content_project"

    channel_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("channel.id", ondelete="CASCADE"), nullable=False
    )

    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    working_title: Mapped[str | None] = mapped_column(String(300))
    content_pillar: Mapped[str | None] = mapped_column(String(100))
    content_format: Mapped[str | None] = mapped_column(String(100))
    target_viewer: Mapped[str | None] = mapped_column(Text)
    curiosity_gap: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default=ProjectStatus.IDEA, server_default=ProjectStatus.IDEA
    )
    status_detail: Mapped[str | None] = mapped_column(Text)

    target_duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="32"
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Normalised topic key, trigram-indexed. Phase 6's planner uses this for
    # duplicate avoidance; captured from the start so history is usable.
    topic_key: Mapped[str | None] = mapped_column(String(300))

    created_by: Mapped[str] = mapped_column(String(20), nullable=False, server_default="HUMAN")
    failure_reason: Mapped[str | None] = mapped_column(Text)

    current_script_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    current_render_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    scripts: Mapped[list[Script]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Script.version"
    )
    renders: Mapped[list[VideoRender]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="VideoRender.created_at"
    )
    research_notes: Mapped[list[ResearchNote]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    # selectin, not lazy: under the async driver a lazy load outside an
    # awaited context raises MissingGreenlet, and the timeline is always wanted
    # alongside the project anyway.
    transitions: Mapped[list[ProjectTransition]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectTransition.created_at",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_content_project_status", "status", text("created_at DESC")),
        Index("ix_content_project_topic_key", "topic_key"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ContentProject {self.topic!r} status={self.status}>"


class ProjectTransition(UUIDPrimaryKeyMixin, Base):
    """Append-only record of every status change (ARCH §8.3 rule 3).

    Written in the same transaction as the status update, so a project can
    never have a state whose origin is unexplained.
    """

    __tablename__ = "project_transition"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("content_project.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(40), nullable=False, server_default="SYSTEM")
    reason: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    project: Mapped[ContentProject] = relationship(back_populates="transitions")

    __table_args__ = (Index("ix_project_transition_project", "project_id", "created_at"),)


# ------------------------------------------------------------------ research ---
class ResearchSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A citable source. Shared across projects."""

    __tablename__ = "research_source"

    url: Mapped[str | None] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(300))
    source_tier: Mapped[str] = mapped_column(String(30), nullable=False, server_default="SECONDARY")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ResearchSource {self.title!r}>"


class ResearchNote(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One atomic factual claim, tied to the source that supports it."""

    __tablename__ = "research_note"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("content_project.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("research_source.id", ondelete="SET NULL")
    )

    claim: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(30), nullable=False, server_default="FACT")
    confidence: Mapped[str] = mapped_column(String(20), nullable=False, server_default="HIGH")
    verification_status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="UNVERIFIED"
    )
    used_in_script: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    project: Mapped[ContentProject] = relationship(back_populates="research_notes")
    source: Mapped[ResearchSource | None] = relationship(lazy="selectin")

    __table_args__ = (Index("ix_research_note_project", "project_id"),)


# -------------------------------------------------------------------- script ---
class Script(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A versioned script. Revisions create new rows rather than mutating."""

    __tablename__ = "script"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("content_project.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    title_candidates: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::varchar[]")
    )
    selected_title: Mapped[str | None] = mapped_column(String(300))
    hook_candidates: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::varchar[]")
    )
    selected_hook: Mapped[str | None] = mapped_column(Text)

    narration: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::varchar[]")
    )

    # How this script was produced — the compliance-relevant field (ARCH §3.4).
    authoring_mode: Mapped[str] = mapped_column(String(20), nullable=False, server_default="manual")
    word_count: Mapped[int | None] = mapped_column(Integer)
    estimated_duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_notes: Mapped[str | None] = mapped_column(Text)

    project: Mapped[ContentProject] = relationship(back_populates="scripts")
    scenes: Mapped[list[Scene]] = relationship(
        back_populates="script",
        cascade="all, delete-orphan",
        order_by="Scene.scene_number",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_script_project_id_version"),
    )


class Scene(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A renderer-agnostic scene definition (ARCH §7.2).

    `visual_instruction` records intent; `template_id` + `template_props` are
    the executable spec. Separating them is what lets a future renderer consume
    the same scene without re-deriving what it was supposed to look like.

    Timings are NULL until narration audio exists — they are measured from the
    real audio, never estimated from word counts (ARCH §14.1).
    """

    __tablename__ = "scene"

    script_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("script.id", ondelete="CASCADE"), nullable=False
    )
    scene_number: Mapped[int] = mapped_column(Integer, nullable=False)

    narration: Mapped[str] = mapped_column(Text, nullable=False)
    on_screen_text: Mapped[str | None] = mapped_column(Text)
    visual_instruction: Mapped[str | None] = mapped_column(Text)

    template_id: Mapped[str] = mapped_column(String(100), nullable=False)
    template_props: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    start_seconds: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    end_seconds: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    transition_in: Mapped[str | None] = mapped_column(String(40))

    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    script: Mapped[Script] = relationship(back_populates="scenes")

    __table_args__ = (
        UniqueConstraint("script_id", "scene_number", name="uq_scene_script_id_scene_number"),
    )

    @property
    def duration_seconds(self) -> float | None:
        if self.start_seconds is None or self.end_seconds is None:
            return None
        return float(self.end_seconds) - float(self.start_seconds)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Scene {self.scene_number} template={self.template_id}>"


# --------------------------------------------------------------------- assets ---
class ProductionAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Every file that goes into a video, with its provenance.

    `origin` and `license` are NOT NULL by intent: the licensing quality gate
    can only be meaningful if it is impossible to record an asset without
    saying where it came from.
    """

    __tablename__ = "production_asset"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("content_project.id", ondelete="CASCADE"), nullable=False
    )
    scene_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("scene.id", ondelete="SET NULL")
    )

    asset_type: Mapped[str] = mapped_column(String(40), nullable=False)
    origin: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=AssetOrigin.GENERATED
    )
    license: Mapped[str] = mapped_column(String(200), nullable=False, server_default="internal")
    attribution_text: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(1000))

    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    bytes: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(80))
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))

    provider: Mapped[str | None] = mapped_column(String(100))
    asset_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_production_asset_project", "project_id", "asset_type"),
        Index("ix_production_asset_scene", "scene_id"),
    )


# --------------------------------------------------------------------- render ---
class VideoRender(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One attempt at composing a final video."""

    __tablename__ = "video_render"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("content_project.id", ondelete="CASCADE"), nullable=False
    )
    script_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("script.id", ondelete="SET NULL")
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=RenderStatus.PENDING
    )
    renderer: Mapped[str] = mapped_column(String(80), nullable=False, server_default="ffmpeg")

    output_path: Mapped[str | None] = mapped_column(String(1000))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    bytes: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(80))

    loudness_lufs: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    peak_dbfs: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))

    spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[ContentProject] = relationship(back_populates="renders")
    quality_checks: Mapped[list[QualityCheck]] = relationship(
        back_populates="render", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (Index("ix_video_render_project", "project_id", text("created_at DESC")),)


class QualityCheck(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Structured QC result for a render (ARCH §13.3)."""

    __tablename__ = "quality_check"

    render_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("video_render.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    verdict: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=QualityVerdict.FAIL
    )
    checks: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    blocking_issues: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::varchar[]")
    )
    warnings: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::varchar[]")
    )

    render: Mapped[VideoRender] = relationship(back_populates="quality_checks")


# ----------------------------------------------------------------- publishing ---
class PublishingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A publish attempt and its handoff package.

    The partial unique index (see the migration) allows at most one live job
    per project, which is the database-level layer of the never-upload-twice
    guard (ARCH §13.4).
    """

    __tablename__ = "publishing_job"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("content_project.id", ondelete="CASCADE"), nullable=False
    )
    render_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("video_render.id", ondelete="SET NULL")
    )

    provider_mode: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=PublishingMode.MANUAL_HANDOFF
    )
    state: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=PublishState.PENDING
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255))

    title: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::varchar[]")
    )
    privacy_status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="public")
    publishing_notes: Mapped[str | None] = mapped_column(Text)

    # YouTube requires disclosure of realistic synthetic media. Synthetic
    # narration is recorded here so the decision is explicit, not forgotten.
    contains_synthetic_media: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    # Persisted before any bytes are sent, so a retry resumes instead of
    # restarting (ARCH §13.4). Unused in MANUAL_HANDOFF.
    resumable_session_uri: Mapped[str | None] = mapped_column(String(1000))
    youtube_video_id: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_publishing_job_project", "project_id"),)


class PublishedVideo(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A video that actually exists on YouTube."""

    __tablename__ = "published_video"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("content_project.id", ondelete="CASCADE"), nullable=False
    )
    publishing_job_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    # Unique, non-null: belt and braces against duplicate uploads.
    youtube_video_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str | None] = mapped_column(String(300))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    privacy_status: Mapped[str | None] = mapped_column(String(20))
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reconciliation_method: Mapped[str | None] = mapped_column(String(40))

    __table_args__ = (Index("ix_published_video_project", "project_id"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<PublishedVideo {self.youtube_video_id}>"
