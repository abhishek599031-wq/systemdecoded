"""Request contracts for the Phase 5 experiment workflow."""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import AudienceLevel, HookType, TechnicalDepth, TitleStyle


class ExperimentFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis: str = Field(min_length=1, max_length=4000)
    primary_variable: str = Field(min_length=1, max_length=100)
    control_reference_project_id: uuid.UUID | None = None
    planned_topic_category: str | None = Field(default=None, max_length=100)
    planned_topic: str | None = Field(default=None, max_length=300)
    planned_hook_type: HookType | None = None
    planned_duration_min_seconds: Decimal | None = Field(default=None, ge=1, le=600)
    planned_duration_max_seconds: Decimal | None = Field(default=None, ge=1, le=600)
    planned_explanation_pattern: str | None = Field(default=None, max_length=300)
    planned_audience_level: AudienceLevel | None = None
    planned_technical_depth: TechnicalDepth | None = None
    planned_title_style: TitleStyle | None = None

    @model_validator(mode="after")
    def validate_duration_range(self) -> ExperimentFields:
        if (
            self.planned_duration_min_seconds is not None
            and self.planned_duration_max_seconds is not None
            and self.planned_duration_min_seconds > self.planned_duration_max_seconds
        ):
            raise ValueError("planned duration minimum cannot exceed maximum")
        return self


class ExperimentCreate(ExperimentFields):
    project_id: uuid.UUID | None = None


class ExperimentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis: str | None = Field(default=None, min_length=1, max_length=4000)
    primary_variable: str | None = Field(default=None, min_length=1, max_length=100)
    control_reference_project_id: uuid.UUID | None = None
    planned_topic_category: str | None = Field(default=None, max_length=100)
    planned_topic: str | None = Field(default=None, max_length=300)
    planned_hook_type: HookType | None = None
    planned_duration_min_seconds: Decimal | None = Field(default=None, ge=1, le=600)
    planned_duration_max_seconds: Decimal | None = Field(default=None, ge=1, le=600)
    planned_explanation_pattern: str | None = Field(default=None, max_length=300)
    planned_audience_level: AudienceLevel | None = None
    planned_technical_depth: TechnicalDepth | None = None
    planned_title_style: TitleStyle | None = None

    @model_validator(mode="after")
    def validate_duration_range(self) -> ExperimentPatch:
        if (
            self.planned_duration_min_seconds is not None
            and self.planned_duration_max_seconds is not None
            and self.planned_duration_min_seconds > self.planned_duration_max_seconds
        ):
            raise ValueError("planned duration minimum cannot exceed maximum")
        return self
