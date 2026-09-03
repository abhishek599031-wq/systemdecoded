"""SQLAlchemy models.

Every model must be imported here so `Base.metadata` is complete before Alembic
autogenerate or `create_all` runs. A model that is not imported here is
invisible to migrations — a silent and annoying failure mode.
"""

from app.db.base import Base
from app.models.channel import AppSetting, Channel
from app.models.content import (
    ContentProject,
    ProductionAsset,
    ProjectTransition,
    PublishedVideo,
    PublishingJob,
    QualityCheck,
    ResearchNote,
    ResearchSource,
    Scene,
    Script,
    VideoRender,
)
from app.models.enums import (
    AssetOrigin,
    AssetType,
    ConnectionStatus,
    JobEventType,
    JobStatus,
    ProjectStatus,
    ProviderMode,
    PublishingMode,
    PublishState,
    QualityVerdict,
    RenderStatus,
)
from app.models.job import BackgroundJob, JobEvent
from app.models.youtube import OAuthState, YouTubeConnection

__all__ = [
    "AppSetting",
    "AssetOrigin",
    "AssetType",
    "BackgroundJob",
    "Base",
    "Channel",
    "ConnectionStatus",
    "ContentProject",
    "JobEvent",
    "JobEventType",
    "JobStatus",
    "OAuthState",
    "ProductionAsset",
    "ProjectStatus",
    "ProjectTransition",
    "ProviderMode",
    "PublishState",
    "PublishedVideo",
    "PublishingJob",
    "PublishingMode",
    "QualityCheck",
    "QualityVerdict",
    "RenderStatus",
    "ResearchNote",
    "ResearchSource",
    "Scene",
    "Script",
    "VideoRender",
    "YouTubeConnection",
]
