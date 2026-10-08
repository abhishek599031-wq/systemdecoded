"""SQLAlchemy models.

Every model must be imported here so `Base.metadata` is complete before Alembic
autogenerate or `create_all` runs. A model that is not imported here is
invisible to migrations — a silent and annoying failure mode.
"""

from app.db.base import Base
from app.models.analytics import YouTubeVideoDailyMetric, YouTubeVideoWindowMetric
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
from app.models.content_intelligence import ContentFeatureProfile
from app.models.enums import (
    AnalyticsFreshness,
    AnalyticsWindowStatus,
    AnalyticsWindowType,
    AssetOrigin,
    AssetType,
    AudienceLevel,
    ConnectionStatus,
    FeatureProvenance,
    HookType,
    JobEventType,
    JobStatus,
    ProjectStatus,
    ProviderMode,
    PublishingMode,
    PublishState,
    QualityVerdict,
    RenderStatus,
    TechnicalDepth,
    TitleStyle,
)
from app.models.job import BackgroundJob, JobEvent
from app.models.quota import ProviderQuotaUsage
from app.models.youtube import OAuthState, YouTubeConnection

__all__ = [
    "AnalyticsFreshness",
    "AnalyticsWindowStatus",
    "AnalyticsWindowType",
    "AppSetting",
    "AssetOrigin",
    "AssetType",
    "AudienceLevel",
    "BackgroundJob",
    "Base",
    "Channel",
    "ConnectionStatus",
    "ContentFeatureProfile",
    "ContentProject",
    "FeatureProvenance",
    "HookType",
    "JobEvent",
    "JobEventType",
    "JobStatus",
    "OAuthState",
    "ProductionAsset",
    "ProjectStatus",
    "ProjectTransition",
    "ProviderMode",
    "ProviderQuotaUsage",
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
    "TechnicalDepth",
    "TitleStyle",
    "VideoRender",
    "YouTubeConnection",
    "YouTubeVideoDailyMetric",
    "YouTubeVideoWindowMetric",
]
