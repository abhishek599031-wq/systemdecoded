"""Structured creative choices used by the observational learning dataset."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:  # pragma: no cover
    from app.models.content import ContentProject


class ContentFeatureProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Versioned feature snapshot describing what one project tried."""

    __tablename__ = "content_feature_profile"

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "content_project.id",
            name="fk_content_feature_profile_project",
            ondelete="CASCADE",
        ),
        nullable=False,
        unique=True,
    )
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    narration_duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    scene_count: Mapped[int | None] = mapped_column(Integer)
    script_word_count: Mapped[int | None] = mapped_column(Integer)
    narration_word_count: Mapped[int | None] = mapped_column(Integer)
    narration_words_per_minute: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    voice_provider: Mapped[str | None] = mapped_column(String(100))
    voice_model: Mapped[str | None] = mapped_column(String(200))
    voice_name: Mapped[str | None] = mapped_column(String(100))
    tts_speed: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    caption_event_count: Mapped[int | None] = mapped_column(Integer)
    render_width: Mapped[int | None] = mapped_column(Integer)
    render_height: Mapped[int | None] = mapped_column(Integer)
    template_families: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::varchar[]")
    )
    content_format: Mapped[str | None] = mapped_column(String(100))
    published_title: Mapped[str | None] = mapped_column(String(300))
    cta_present: Mapped[bool | None] = mapped_column(Boolean)

    topic_category: Mapped[str | None] = mapped_column(String(100))
    topic: Mapped[str | None] = mapped_column(String(300))
    subtopic: Mapped[str | None] = mapped_column(String(300))
    hook_type: Mapped[str | None] = mapped_column(String(50))
    hook_text: Mapped[str | None] = mapped_column(String(500))
    title_style: Mapped[str | None] = mapped_column(String(50))
    explanation_pattern: Mapped[str | None] = mapped_column(String(300))
    audience_level: Mapped[str | None] = mapped_column(String(50))
    technical_depth: Mapped[str | None] = mapped_column(String(50))
    cta_type: Mapped[str | None] = mapped_column(String(100))
    visual_storytelling_style: Mapped[str | None] = mapped_column(String(300))
    question_answered: Mapped[str | None] = mapped_column(String(500))

    field_provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    derived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[ContentProject] = relationship(back_populates="feature_profile")
