"""Job API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    attempt: int
    message: str | None = None
    data: dict[str, Any] | None = None
    created_at: datetime


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_type: str
    status: str
    priority: int
    attempt: int
    max_attempts: int
    run_after: datetime
    claimed_by: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_class: str | None = None
    error_message: str | None = None
    project_id: uuid.UUID | None = None
    created_at: datetime

    @property
    def is_retry_pending(self) -> bool:
        return self.status == "QUEUED" and self.attempt > 0


class JobDetail(JobSummary):
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    traceback: str | None = None
    timeout_seconds: int
    idempotency_key: str | None = None
    heartbeat_at: datetime | None = None
    events: list[JobEventOut] = Field(default_factory=list)


class JobListResponse(BaseModel):
    items: list[JobSummary]
    total: int
    limit: int
    offset: int


class EnqueueJobRequest(BaseModel):
    job_type: str = Field(..., description="A registered job type, e.g. 'system.ping'")
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int | None = None
    delay_seconds: float | None = Field(default=None, ge=0)
    max_attempts: int | None = Field(default=None, ge=1)
    idempotency_key: str | None = None


class EnqueueJobResponse(BaseModel):
    job: JobSummary
    created: bool = Field(
        ..., description="False when an existing job already held this idempotency key."
    )


class RequeueJobRequest(BaseModel):
    reset_attempts: bool = Field(
        default=True,
        description="Reset the attempt counter so the job gets a full retry budget.",
    )


class JobTypeOut(BaseModel):
    name: str
    description: str
    max_attempts: int
    timeout_seconds: int
    default_priority: int
    retry_on: list[str]
