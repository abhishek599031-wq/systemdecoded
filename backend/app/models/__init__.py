"""SQLAlchemy models.

Every model must be imported here so `Base.metadata` is complete before Alembic
autogenerate or `create_all` runs. A model that is not imported here is
invisible to migrations — a silent and annoying failure mode.
"""

from app.db.base import Base
from app.models.channel import AppSetting, Channel
from app.models.enums import (
    ConnectionStatus,
    JobEventType,
    JobStatus,
    ProviderMode,
)
from app.models.job import BackgroundJob, JobEvent
from app.models.youtube import OAuthState, YouTubeConnection

__all__ = [
    "AppSetting",
    "BackgroundJob",
    "Base",
    "Channel",
    "ConnectionStatus",
    "JobEvent",
    "JobEventType",
    "JobStatus",
    "OAuthState",
    "ProviderMode",
    "YouTubeConnection",
]
