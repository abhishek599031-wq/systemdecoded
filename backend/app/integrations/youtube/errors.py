"""YouTube/Google API error classification.

The queue treats unrecognised exceptions as terminal (ARCH §9.2), so every
failure mode that *should* be retried has to be named explicitly. Getting this
split right is what keeps a transient 503 from permanently failing a job, and a
revoked token from being retried 3 times against Google.
"""

from __future__ import annotations

from app.core.errors import AppError, RetryableError, TerminalError

__all__ = [
    "GoogleAPIError",
    "GoogleAuthError",
    "InvalidGrantError",
    "NoChannelError",
    "QuotaExceededError",
    "RateLimitedError",
    "TransientGoogleError",
    "YouTubeNotConnectedError",
]


class GoogleAPIError(TerminalError):
    """A Google API call failed in a way retrying will not fix."""

    def __init__(self, message: str, *, status: int | None = None, reason: str | None = None):
        super().__init__(message)
        self.status = status
        self.reason = reason


class GoogleAuthError(GoogleAPIError):
    """Authentication or authorization failed. Terminal until reconnected."""


class InvalidGrantError(GoogleAuthError):
    """The refresh token is no longer valid.

    The expected causes are all terminal and all need human action:
      - the OAuth consent screen is in "testing" and 7 days elapsed (ARCH §3.3)
      - the user revoked access in their Google account
      - the token was superseded by a newer grant

    Retrying cannot fix any of them, so this must never be retryable.
    """


class TransientGoogleError(RetryableError):
    """A 5xx or network failure. Safe to retry."""


class RateLimitedError(RetryableError):
    """429 or userRateLimitExceeded. Retry with backoff."""


class QuotaExceededError(TerminalError):
    """Daily quota is gone. Retrying today cannot help (ARCH §3.2).

    Terminal on purpose: the quota window is a calendar day, far longer than any
    retry budget, so burning attempts against it is pure waste.
    """


class YouTubeNotConnectedError(AppError):
    """An operation needs a live connection and there is not one."""

    status_code = 409
    code = "youtube_not_connected"
    message = "No active YouTube connection. Connect a channel first."


class NoChannelError(AppError):
    """The authorised Google account has no YouTube channel."""

    status_code = 422
    code = "youtube_no_channel"
    message = (
        "That Google account has no YouTube channel. Create one, then reconnect."
    )


# Google returns these in error.errors[].reason for rate limiting.
_RATE_LIMIT_REASONS = {
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "backendError",
}
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}


def classify_api_error(status: int, payload: dict) -> Exception:
    """Map a Google API error response onto our retry taxonomy."""
    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    message = error.get("message") or f"Google API returned HTTP {status}"
    reasons = {e.get("reason") for e in error.get("errors", []) if isinstance(e, dict)}

    if reasons & _QUOTA_REASONS:
        return QuotaExceededError(f"YouTube API quota exceeded: {message}")
    if status == 429 or (reasons & _RATE_LIMIT_REASONS):
        return RateLimitedError(f"Rate limited by Google: {message}")
    if status in (401, 403) and not reasons & _QUOTA_REASONS:
        return GoogleAuthError(message, status=status, reason=next(iter(reasons), None))
    if status >= 500:
        return TransientGoogleError(f"Google API {status}: {message}")
    return GoogleAPIError(message, status=status, reason=next(iter(reasons), None))


def classify_token_error(status: int, payload: dict) -> Exception:
    """Map an OAuth token endpoint error onto our retry taxonomy."""
    error = payload.get("error") if isinstance(payload, dict) else None
    description = (
        payload.get("error_description", "") if isinstance(payload, dict) else ""
    )

    if error == "invalid_grant":
        return InvalidGrantError(
            "Refresh token is invalid or expired. "
            "This happens when access was revoked, or when the OAuth consent "
            "screen is still in 'testing' (Google expires those tokens after 7 "
            "days). Reconnect the channel to fix it."
        )
    if error in ("invalid_client", "unauthorized_client"):
        return GoogleAuthError(
            f"OAuth client rejected by Google ({error}). "
            "Check GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
        )
    if status >= 500:
        return TransientGoogleError(f"Google token endpoint {status}: {description}")
    return GoogleAuthError(f"Token request failed ({error or status}): {description}")
