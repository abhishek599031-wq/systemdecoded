"""Rate-limit handling for the Gemini narration provider.

These exist because of a defect found while preparing the Algieba re-render.
The backoff ladder was 1.5s then 3s, so three attempts against a *per-minute*
rate limit were all spent inside five seconds. The fallback then produced the
rest of the video in the Kokoro voice — a silent switch of narrator mid-render,
recorded correctly in the metadata and audible to nobody until playback.

The fix has two halves, and both are tested here: don't trip the limit
(pacing), and when it is tripped, wait as long as the service actually asked
(retry_after) rather than as long as a generic ladder guessed.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app.core.errors import RetryableError
from app.providers.tts import gemini
from app.providers.tts.gemini import GeminiTTS, _retry_after, sleep_backoff


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    """Record every requested sleep without actually waiting.

    The real `asyncio.sleep` is captured before patching — a stub that calls
    the patched name recurses into itself.
    """
    real_sleep = asyncio.sleep
    recorded: list[float] = []

    async def fake(seconds: float) -> None:
        recorded.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake)
    return recorded


def _response(status: int, body: dict | None = None, headers: dict | None = None):
    return httpx.Response(
        status_code=status,
        json=body if body is not None else {},
        headers=headers or {},
        request=httpx.Request("POST", "https://example.test"),
    )


# ------------------------------------------------------ reading the delay ---
def test_retry_after_header_is_honoured():
    assert _retry_after(_response(429, headers={"Retry-After": "42"})) == 42.0


def test_google_retry_info_detail_is_read():
    """Google does not send Retry-After; it sends RetryInfo in the error body."""
    body = {
        "error": {
            "code": 429,
            "message": "Quota exceeded",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.QuotaFailure"},
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "27s"},
            ],
        }
    }
    assert _retry_after(_response(429, body)) == 27.0


def test_missing_delay_is_none_not_zero():
    """None means "we were not told"; zero would mean "retry immediately"."""
    assert _retry_after(_response(500)) is None
    assert _retry_after(_response(429, {"error": {"details": []}})) is None


def test_unparseable_delay_does_not_raise():
    body = {"error": {"details": [{"retryDelay": "soon"}]}}
    assert _retry_after(_response(429, body)) is None


# ------------------------------------------------- carried on the exception ---
async def test_rate_limited_call_carries_the_requested_delay(monkeypatch, tmp_path):
    monkeypatch.setattr(gemini.settings, "GEMINI_MIN_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(
        gemini.settings, "GEMINI_API_KEY", type(gemini.settings.GEMINI_API_KEY)("test-key")
    )

    body = {"error": {"details": [{"retryDelay": "31s"}]}}

    async def handler(request: httpx.Request) -> httpx.Response:
        return _response(429, body)

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)

    with pytest.raises(RetryableError) as excinfo:
        await GeminiTTS().synthesize(
            "hello", gemini.VoiceSpec(voice="Algieba"), tmp_path / "o.wav"
        )
    assert excinfo.value.retry_after == 31.0
    assert "test-key" not in str(excinfo.value)


def test_retryable_error_defaults_to_no_delay():
    assert RetryableError("boom").retry_after is None


# ----------------------------------------------------------------- backoff ---
async def test_backoff_waits_for_the_delay_the_service_asked_for(slept):
    await sleep_backoff(1, error=RetryableError("429", retry_after=27.0))

    assert slept and 27.0 <= slept[0] <= 29.0


async def test_backoff_outlasts_a_per_minute_limit_when_not_told(slept):
    """The original defect: three attempts finished in 4.5s and gave up."""
    for attempt in (1, 2, 3):
        await sleep_backoff(attempt)

    assert sum(slept) > 60.0, f"total wait {sum(slept)}s cannot outlast a 60s window"


async def test_backoff_is_bounded(slept):
    await sleep_backoff(9)
    await sleep_backoff(1, error=RetryableError("x", retry_after=9999.0))

    assert all(s <= 70.0 for s in slept)


# ------------------------------------------------------------------ pacing ---
async def test_pacing_spaces_out_consecutive_requests(monkeypatch, slept):
    monkeypatch.setattr(gemini.settings, "GEMINI_MIN_REQUEST_INTERVAL_SECONDS", 30.0)
    monkeypatch.setattr(gemini, "_last_request_at", 0.0)

    await gemini._pace()  # first call never waits
    assert slept == []

    await gemini._pace()
    assert slept and 25.0 < slept[0] <= 30.0


async def test_pacing_can_be_switched_off(monkeypatch, slept):
    """A paid tier should not be throttled by our own guard."""
    monkeypatch.setattr(gemini.settings, "GEMINI_MIN_REQUEST_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(gemini, "_last_request_at", time.monotonic())

    await gemini._pace()
    assert slept == []
