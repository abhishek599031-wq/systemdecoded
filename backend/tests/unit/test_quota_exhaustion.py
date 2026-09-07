"""A spent daily quota is not a rate limit, and must not be retried like one.

Found during the Algieba re-render. The free tier allows 10 requests per *day*
for the preview TTS model. Five voice auditions plus a five-block render passed
it mid-render, and every remaining block then spent three retries and 95 seconds
discovering something that could not change for another eight hours.

Worse, each block fell back independently, so the finished video was narrated by
Gemini for scene 1 and by Kokoro for scenes 2-5. Both halves of that are fixed:
this file covers not retrying, and the pipeline covers falling back as a whole.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.errors import QuotaExhaustedError, RetryableError, TerminalError
from app.providers.base import AudioResult, VoiceSpec
from app.providers.tts.gemini import _daily_quota_spent
from app.providers.tts.resolver import ResilientTTS

DAILY_QUOTA_BODY = {
    "error": {
        "code": 429,
        "message": "You exceeded your current quota.",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [
                    {
                        "quotaMetric": "generativelanguage.googleapis.com/"
                        "generate_content_free_tier_requests",
                        "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                        "quotaValue": "10",
                    }
                ],
            },
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "14s"},
        ],
    }
}

RATE_LIMIT_BODY = {
    "error": {
        "code": 429,
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                "violations": [
                    {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}
                ],
            },
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "14s"},
        ],
    }
}


def _response(body: dict) -> httpx.Response:
    return httpx.Response(
        429, json=body, request=httpx.Request("POST", "https://example.test")
    )


# ------------------------------------------------------ telling them apart ---
def test_daily_quota_violation_is_recognised():
    assert _daily_quota_spent(_response(DAILY_QUOTA_BODY)) is True


def test_per_minute_limit_is_not_a_daily_quota():
    """A per-minute limit *is* worth waiting out — it must not be confused."""
    assert _daily_quota_spent(_response(RATE_LIMIT_BODY)) is False


def test_unstructured_429_is_not_treated_as_a_daily_quota():
    assert _daily_quota_spent(_response({"error": {"message": "slow down"}})) is False
    assert _daily_quota_spent(
        httpx.Response(429, text="nope", request=httpx.Request("POST", "https://e.test"))
    ) is False


def test_quota_exhausted_is_still_retryable_tomorrow():
    """Nothing is misconfigured, so the *job* may run again — just not now."""
    assert isinstance(QuotaExhaustedError("spent"), RetryableError)
    assert not isinstance(QuotaExhaustedError("spent"), TerminalError)


# ------------------------------------------------------- resolver behaviour ---
class _Boom:
    name = "boom"
    prefers_whole_block = True

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def list_voices(self):
        return []

    async def synthesize(self, text, voice, out_path):
        self.calls += 1
        raise self.error


class _Works:
    name = "works"
    prefers_whole_block = False

    def __init__(self) -> None:
        self.calls = 0

    async def list_voices(self):
        return []

    async def synthesize(self, text, voice, out_path):
        self.calls += 1
        return AudioResult(
            path=out_path, duration_seconds=1.0, sample_rate=24000,
            provider=self.name, voice="fb",
        )


async def _never_sleep(attempt: int, error: Exception | None = None) -> None:
    raise AssertionError(f"backoff should not run for attempt {attempt}")


async def test_daily_quota_goes_straight_to_the_fallback(tmp_path):
    """One attempt, no backoff. Waiting cannot help."""
    primary = _Boom(QuotaExhaustedError("daily quota spent"))
    fallback = _Works()

    resolver = ResilientTTS(
        primary=primary, fallback=fallback, max_attempts=3, backoff=_never_sleep
    )
    result = await resolver.synthesize("hi", VoiceSpec(voice="Algieba"), tmp_path / "o.wav")

    assert primary.calls == 1, "a spent daily quota must not be retried"
    assert fallback.calls == 1
    assert result.fallback_used is True
    assert result.provider == "works"


async def test_ordinary_rate_limit_still_retries(tmp_path):
    """The distinction has to earn its keep: normal transients still retry."""
    attempts: list[int] = []

    async def record(attempt: int, error: Exception | None = None) -> None:
        attempts.append(attempt)

    primary = _Boom(RetryableError("429 slow down", retry_after=1.0))
    fallback = _Works()

    resolver = ResilientTTS(
        primary=primary, fallback=fallback, max_attempts=3, backoff=record
    )
    await resolver.synthesize("hi", VoiceSpec(voice="Algieba"), tmp_path / "o.wav")

    assert primary.calls == 3
    assert attempts == [1, 2]


async def test_quota_exhaustion_without_a_fallback_surfaces(tmp_path):
    primary = _Boom(QuotaExhaustedError("daily quota spent"))
    resolver = ResilientTTS(
        primary=primary, fallback=None, max_attempts=3, backoff=_never_sleep
    )

    with pytest.raises(RetryableError, match="no fallback"):
        await resolver.synthesize("hi", VoiceSpec(voice="Algieba"), tmp_path / "o.wav")
    assert primary.calls == 1


async def test_configuration_error_still_never_falls_back(tmp_path):
    """The rule that outranks all of this stays intact."""
    primary = _Boom(TerminalError("GEMINI_API_KEY is not set"))
    fallback = _Works()

    resolver = ResilientTTS(
        primary=primary, fallback=fallback, max_attempts=3, backoff=_never_sleep
    )
    with pytest.raises(TerminalError):
        await resolver.synthesize("hi", VoiceSpec(voice="Algieba"), tmp_path / "o.wav")

    assert primary.calls == 1
    assert fallback.calls == 0
