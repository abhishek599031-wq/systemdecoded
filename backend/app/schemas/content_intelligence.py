"""Content Intelligence request contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AudienceLevel, HookType, TechnicalDepth, TitleStyle


class EditorialProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_category: str | None = Field(default=None, max_length=100)
    topic: str | None = Field(default=None, max_length=300)
    subtopic: str | None = Field(default=None, max_length=300)
    hook_type: HookType | None = None
    hook_text: str | None = Field(default=None, max_length=500)
    title_style: TitleStyle | None = None
    explanation_pattern: str | None = Field(default=None, max_length=300)
    audience_level: AudienceLevel | None = None
    technical_depth: TechnicalDepth | None = None
    cta_type: str | None = Field(default=None, max_length=100)
    visual_storytelling_style: str | None = Field(default=None, max_length=300)
    question_answered: str | None = Field(default=None, max_length=500)
