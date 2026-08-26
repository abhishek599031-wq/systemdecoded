"""Execution context handed to every job handler.

Handlers receive a context rather than the queue itself. That is what makes
ADR 0001's "if this proves wrong, only queue.py and worker.py change" true —
no handler ever touches queue internals.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True)
class JobContext:
    job_id: uuid.UUID
    job_type: str
    attempt: int
    max_attempts: int
    payload: dict[str, Any]
    session: AsyncSession
    logger: structlog.stdlib.BoundLogger
    project_id: uuid.UUID | None = None

    @property
    def is_final_attempt(self) -> bool:
        return self.attempt >= self.max_attempts

    def require(self, key: str) -> Any:
        """Fetch a required payload key, failing terminally if absent.

        A missing payload key is a programming error, not a transient fault, so
        it must not be retried.
        """
        from app.core.errors import TerminalError

        if key not in self.payload:
            raise TerminalError(f"Job {self.job_type} requires payload key {key!r}")
        return self.payload[key]
