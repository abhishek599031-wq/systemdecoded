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
