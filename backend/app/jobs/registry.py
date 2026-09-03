"""Job type registry.

Handlers register themselves with `@job(...)`. Each declares its own retry
budget, timeout and — importantly — which exceptions it considers retryable
(ARCH §9.2). Nothing is retryable by default beyond the conservative set in
`app.core.errors.DEFAULT_RETRYABLE`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.core.errors import DEFAULT_RETRYABLE

if TYPE_CHECKING:  # pragma: no cover
    from app.jobs.context import JobContext

JobFunc = Callable[["JobContext"], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True, slots=True)
class JobDefinition:
    name: str
    func: JobFunc
    max_attempts: int
    timeout_seconds: int
    retry_on: tuple[type[BaseException], ...]
    default_priority: int = 0
    description: str = ""

    def is_retryable(self, exc: BaseException) -> bool:
        from app.core.errors import is_retryable

        return is_retryable(exc, self.retry_on)


_REGISTRY: dict[str, JobDefinition] = {}


class UnknownJobType(KeyError):
    """Raised when a queued job has no registered handler."""


def job(
    name: str,
    *,
    max_attempts: int | None = None,
    timeout_seconds: int | None = None,
    retry_on: tuple[type[BaseException], ...] = DEFAULT_RETRYABLE,
    default_priority: int = 0,
    description: str = "",
) -> Callable[[JobFunc], JobFunc]:
    """Register an async job handler under a stable name.

    The name is persisted in the database, so renaming one is a data migration,
    not a refactor. Use `namespace.verb`, e.g. `system.ping`.
    """

    def decorator(func: JobFunc) -> JobFunc:
        if name in _REGISTRY:
            raise ValueError(f"Job type {name!r} is already registered")
        _REGISTRY[name] = JobDefinition(
            name=name,
            func=func,
            max_attempts=max_attempts or settings.JOB_DEFAULT_MAX_ATTEMPTS,
            timeout_seconds=timeout_seconds or settings.JOB_DEFAULT_TIMEOUT_SECONDS,
            retry_on=retry_on,
            default_priority=default_priority,
            description=description or (func.__doc__ or "").strip().split("\n")[0],
        )
        return func

    return decorator


def get_definition(name: str) -> JobDefinition:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise UnknownJobType(name) from exc


def all_definitions() -> dict[str, JobDefinition]:
    return dict(_REGISTRY)


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def load_all_jobs() -> None:
    """Import every task module so decorators run.

    Called by the worker, the scheduler, the API and the tests. Import side
    effects are the registration mechanism, so this must happen before any
    process claims or enqueues work.
    """
    from app.jobs.tasks import content, system, youtube  # noqa: F401
