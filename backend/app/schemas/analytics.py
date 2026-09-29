"""Request contracts for YouTube analytics endpoints."""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class AnalyticsSyncRequest(BaseModel):
    """Omit the ID to fan out across every eligible published video."""

    published_video_id: uuid.UUID | None = None
