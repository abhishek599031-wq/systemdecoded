"""Enumerations.

Design note — these are stored as VARCHAR, not native PostgreSQL ENUM types.

PostgreSQL enums require an `ALTER TYPE ... ADD VALUE` migration for every new
member, cannot easily remove members, and (before PG12) could not be altered
inside a transaction. The workflow state machine in `PHASE-1-ARCHITECTURE.md` §8
will gain states throughout Phases 2-8, so native enums would mean a schema
migration for what should be a code change.

VARCHAR + a Python `StrEnum` gives type safety where it matters (application
code) at the cost of database-level enforcement we do not need for a
single-writer system. Revisit if a second writer ever touches this database.
"""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    """Job lifecycle.

    Deliberately five states, not six. An earlier draft separated FAILED
    ("attempt failed, retry pending") from DEAD ("attempts exhausted"), but a
    job awaiting retry is simply claimable again — so it is QUEUED, and
    `attempt > 0` distinguishes a retry from a first run. FAILED therefore means
    exactly one thing: terminal failure, no further automatic attempts.
    The full attempt history lives in `job_event`.
    """

    QUEUED = "QUEUED"  # claimable; attempt > 0 means a retry is pending
    RUNNING = "RUNNING"  # claimed by a worker and executing
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"  # terminal: attempts exhausted or a terminal error
    CANCELLED = "CANCELLED"

    @classmethod
    def terminal(cls) -> frozenset[JobStatus]:
        return frozenset({cls.SUCCEEDED, cls.FAILED, cls.CANCELLED})

    @classmethod
    def active(cls) -> frozenset[JobStatus]:
        return frozenset({cls.QUEUED, cls.RUNNING})


class JobEventType(StrEnum):
    ENQUEUED = "ENQUEUED"
    CLAIMED = "CLAIMED"
    SUCCEEDED = "SUCCEEDED"
    ATTEMPT_FAILED = "ATTEMPT_FAILED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    TIMED_OUT = "TIMED_OUT"
    REAPED = "REAPED"
    FAILED = "FAILED"
    REQUEUED = "REQUEUED"
    CANCELLED = "CANCELLED"


class ProviderMode(StrEnum):
    """How an external capability is fulfilled (ARCH §5.3)."""

    LOCAL = "local"
    MANUAL = "manual"
    EXTERNAL_API = "external_api"


class ConnectionStatus(StrEnum):
    """YouTube connection health (ARCH §3.3).

    Token loss is an expected event, not an error path: refresh tokens expire
    after 7 days while the OAuth consent screen is in Testing status.
    """

    NOT_CONNECTED = "NOT_CONNECTED"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    ERROR = "ERROR"


# ===========================================================================
# Phase 2 — content production
# ===========================================================================


class ProjectStatus(StrEnum):
    """Master workflow state for a ContentProject (ARCH §8.2).

    One authoritative status on the project, with sub-resources (Script,
    VideoRender, PublishingJob) carrying their own independent lifecycles. The
    full Phase 2-8 set is declared here rather than added piecemeal, because
    these are VARCHAR values — adding one is a code change, not a migration.
    """

    IDEA = "IDEA"
    IDEA_APPROVED = "IDEA_APPROVED"
    RESEARCHING = "RESEARCHING"
    RESEARCH_READY = "RESEARCH_READY"
    SCRIPT_GENERATING = "SCRIPT_GENERATING"
    AWAITING_LLM_INPUT = "AWAITING_LLM_INPUT"
    SCRIPT_REVIEW = "SCRIPT_REVIEW"
    SCRIPT_APPROVED = "SCRIPT_APPROVED"
    PRODUCTION_PLANNING = "PRODUCTION_PLANNING"
    ASSETS_REQUIRED = "ASSETS_REQUIRED"
    ASSETS_READY = "ASSETS_READY"
    RENDERING = "RENDERING"
    VIDEO_REVIEW = "VIDEO_REVIEW"
    APPROVED_FOR_PUBLISHING = "APPROVED_FOR_PUBLISHING"
    SCHEDULED = "SCHEDULED"
    PUBLISHING = "PUBLISHING"
    AWAITING_HUMAN_UPLOAD = "AWAITING_HUMAN_UPLOAD"
    PUBLISHED = "PUBLISHED"
    ANALYTICS_COLLECTING = "ANALYTICS_COLLECTING"
    COMPLETED = "COMPLETED"
    NEEDS_REVISION = "NEEDS_REVISION"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"

    @classmethod
    def terminal(cls) -> frozenset[ProjectStatus]:
        return frozenset({cls.COMPLETED, cls.REJECTED, cls.ARCHIVED})

    @classmethod
    def needs_human(cls) -> frozenset[ProjectStatus]:
        """States that block until a person acts (ARCH §8.3 rule 5)."""
        return frozenset(
            {
                cls.SCRIPT_REVIEW,
                cls.VIDEO_REVIEW,
                cls.ASSETS_REQUIRED,
                cls.AWAITING_LLM_INPUT,
                cls.AWAITING_HUMAN_UPLOAD,
            }
        )


class AssetType(StrEnum):
    NARRATION_AUDIO = "NARRATION_AUDIO"
    SCENE_FRAME = "SCENE_FRAME"
    CAPTION_FILE = "CAPTION_FILE"
    MUSIC = "MUSIC"
    SFX = "SFX"
    THUMBNAIL = "THUMBNAIL"
    VIDEO = "VIDEO"


class AssetOrigin(StrEnum):
    """Where an asset came from — the basis of the licensing QC gate.

    Every asset must declare this. A render cannot pass quality checks if any
    asset lacks a license record (ARCH §14.5), which is what keeps
    copyright/trademark risk out of a channel whose purpose is monetization.
    """

    GENERATED = "GENERATED"  # produced by our own code (scene renders, TTS)
    LICENSED = "LICENSED"  # third-party under a recorded licence
    PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
    USER_SUPPLIED = "USER_SUPPLIED"


class RenderStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class QualityVerdict(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    FAIL = "FAIL"


class PublishingMode(StrEnum):
    """ARCH §3.1.

    API uploads from an unaudited Google Cloud project are permanently locked
    to private with no appeal, so MANUAL_HANDOFF is the only mode that can
    actually grow the channel until the compliance audit passes.
    """

    MANUAL_HANDOFF = "MANUAL_HANDOFF"
    API_PRIVATE_ONLY = "API_PRIVATE_ONLY"
    API_FULL = "API_FULL"


class PublishState(StrEnum):
    PENDING = "PENDING"
    AWAITING_HUMAN_UPLOAD = "AWAITING_HUMAN_UPLOAD"
    UPLOADING = "UPLOADING"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"
