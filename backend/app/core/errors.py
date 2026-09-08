"""Error foundation.

Two orthogonal hierarchies that are easy to conflate and must not be:

1. `AppError` — errors that become HTTP responses. Carries a status code and a
   stable machine-readable `code`.
2. `RetryableError` / `TerminalError` — errors that tell the job worker whether
   an operation may be attempted again.

The retry distinction is load-bearing. `PHASE-1-ARCHITECTURE.md` §9.2: blind
retries burn API quota and, in the publishing path, risk the double-upload the
brief explicitly forbids. So the default is conservative — an unrecognised
exception is treated as TERMINAL, and each job opts in to what it considers
retryable.
"""

from __future__ import annotations

from typing import Any


# --------------------------------------------------------------- HTTP-facing ---
class AppError(Exception):
    """Base for errors that map onto an HTTP response."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        detail: Any = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.detail = detail
        super().__init__(self.message)

    def to_payload(self, request_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.detail is not None:
            body["detail"] = self.detail
        if request_id:
            body["request_id"] = request_id
        return {"error": body}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "Resource not found."


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"
    message = "The request was not valid."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state."


class InvalidStateTransition(ConflictError):
    code = "invalid_state_transition"
    message = "That state transition is not allowed."


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
    message = "A dependency is unavailable."


class NotConfiguredError(AppError):
    """A capability exists in the codebase but is not configured yet.

    Used for the YouTube boundary in Phase 0: the endpoints and interfaces are
    present, but no credentials exist, and saying so plainly beats pretending.
    """

    status_code = 503
    code = "not_configured"
    message = "This capability is not configured."


# --------------------------------------------------------------- job-facing ---
class RetryableError(Exception):
    """A transient failure. The worker may attempt this job again.

    `retry_after` carries a wait the *service* asked for, in seconds, when it
    said so — a 429's `Retry-After` header or Google's `RetryInfo.retryDelay`.
    Honouring it beats guessing: a generic exponential ladder can exhaust every
    attempt inside a few seconds against a per-minute rate limit, and then the
    caller falls back for what was only ever a short wait.
    """

    def __init__(self, *args: Any, retry_after: float | None = None) -> None:
        super().__init__(*args)
        self.retry_after = retry_after


class TerminalError(Exception):
    """A permanent failure. The worker must NOT attempt this job again.

    Always wins over retryable classification, even via subclassing.
    """


class QuotaExhaustedError(RetryableError):
    """A quota is spent over a horizon far longer than any retry loop.

    Distinct from an ordinary rate limit. A per-minute limit is worth waiting
    out; a per-*day* quota is not — retrying burns attempts, delays the caller
    by minutes, and cannot succeed. Callers should stop retrying immediately and
    go to whatever their fallback is.

    Still a `RetryableError`, because the job itself is worth attempting again
    tomorrow: nothing is misconfigured.
    """


class QuotaBlockedError(TerminalError):
    """A render was refused because the provider cannot finish it.

    Terminal on purpose. The condition is real and will not clear on the job
    queue's timescale — a daily allowance resets tomorrow, not in thirty
    seconds — so retrying is only a way of discovering the same thing again.
    The render is meant to be re-queued once quota returns.

    `detail` carries the preflight's own verdict, so the reason reaches a job
    result and the UI without being reconstructed from a message string.
    """

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


class JobTimeoutError(RetryableError):
    """A job exceeded its declared timeout budget."""


# Conservative default retry set. Anything outside it is treated as terminal
# unless a job declares otherwise via @job(retry_on=...).
DEFAULT_RETRYABLE: tuple[type[BaseException], ...] = (
    RetryableError,
    TimeoutError,
    ConnectionError,
    OSError,
)


def is_retryable(
    exc: BaseException,
    retry_on: tuple[type[BaseException], ...] = DEFAULT_RETRYABLE,
) -> bool:
    """Decide whether `exc` permits another attempt.

    TerminalError always returns False, regardless of `retry_on`.
    """
    if isinstance(exc, TerminalError):
        return False
    return isinstance(exc, retry_on)
