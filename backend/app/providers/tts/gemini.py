"""Gemini TTS (EXTERNAL_API provider).

Implements the same `TTSProvider` interface as Kokoro, so nothing downstream
learns that narration came from a hosted API (ARCH §5.3).

Two things about this API are worth knowing before reading the code:

1. **It returns raw PCM, not a container.** The response mime type is
   `audio/l16; rate=24000; channels=1` — signed 16-bit little-endian samples
   with no header at all. Writing those bytes to a `.wav` file produces
   something no decoder will open, so the header is written here explicitly.
   24 kHz mono happens to match what Kokoro produces, which is why the
   alignment stage needs no changes.

2. **Style direction is part of the prompt.** There is no separate "style"
   field; the performance direction is prefixed to the text (see
   `app/providers/tts/direction.py`).

Error classification is deliberate and load-bearing. Configuration and parsing
faults raise `TerminalError` so they surface loudly; only genuinely transient
conditions raise `RetryableError`, because only those should ever reach the
Kokoro fallback (see `resolver.py`).

Nothing here logs, formats or re-raises the API key.
"""

from __future__ import annotations

import asyncio
import base64
import re
import struct
import time
import wave
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.core.errors import QuotaExhaustedError, RetryableError, TerminalError
from app.core.logging import get_logger
from app.providers.base import AudioResult, VoiceSpec
from app.providers.tts.direction import (
    SYSTEMDECODED_DIRECTION,
    VoiceDirection,
    build_prompt,
)

log = get_logger("tts.gemini")

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# The prebuilt voices published for the Gemini TTS models. Kept as a constant so
# a typo becomes a clear configuration error before a request is ever made,
# rather than an opaque 400 from the API.
PREBUILT_VOICES = (
    "Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede",
    "Callirrhoe", "Autonoe", "Enceladus", "Iapetus", "Umbriel", "Algieba",
    "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia", "Achernar",
    "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Zubenelgenubi",
    "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat",
)

# Retried: the service is momentarily unavailable or throttling us.
TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

# Serialises requests from this process and spaces them out. The preview TTS
# model's free tier allows only a couple of requests per minute; a five-block
# render fired back-to-back gets 429 on everything after the first. Waiting is
# not a workaround here — it is the correct way to use a rate-limited API, and
# it is far better than the alternative, which is exhausting retries and
# quietly finishing the video in the fallback voice.
#
# Set GEMINI_MIN_REQUEST_INTERVAL_SECONDS=0 on a paid tier to remove it.
_pacing_lock = asyncio.Lock()
_last_request_at: float = 0.0


async def _pace() -> None:
    """Hold until the minimum gap since the previous request has elapsed."""
    global _last_request_at
    interval = settings.GEMINI_MIN_REQUEST_INTERVAL_SECONDS
    async with _pacing_lock:
        if interval > 0 and _last_request_at:
            wait = interval - (time.monotonic() - _last_request_at)
            if wait > 0:
                log.info("tts.pacing", wait_seconds=round(wait, 1))
                await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


class GeminiTTS:
    name = "gemini"

    # Gemini is directed to vary its pacing across a passage, so it is given a
    # whole semantic block. Splitting narration into single clauses would throw
    # that away and turn one request into a dozen against a preview model whose
    # free tier allows a couple of requests per minute.
    prefers_whole_block = True

    def __init__(
        self,
        model: str | None = None,
        direction: VoiceDirection | None = SYSTEMDECODED_DIRECTION,
    ) -> None:
        self.model = model or settings.GEMINI_TTS_MODEL
        self.direction = direction

    # ------------------------------------------------------------- config ---
    def _api_key(self) -> str:
        """Fetch the key at the single point of use.

        A missing key is a configuration error, never something to fall back
        from — quietly narrating with Kokoro would hide the mistake for as long
        as nobody listened carefully.
        """
        key = settings.GEMINI_API_KEY.get_secret_value()
        if not key:
            raise TerminalError(
                "GEMINI_API_KEY is not set. Add it to .env and recreate the "
                "containers (docker compose up -d --force-recreate)."
            )
        return key

    def _validate_voice(self, voice: str) -> str:
        if voice not in PREBUILT_VOICES:
            raise TerminalError(
                f"Unknown Gemini voice {voice!r}. Available: {', '.join(PREBUILT_VOICES)}"
            )
        return voice

    async def list_voices(self) -> list[str]:
        return list(PREBUILT_VOICES)

    # ---------------------------------------------------------- synthesis ---
    def _build_request(self, text: str, voice: str) -> dict[str, Any]:
        return {
            "contents": [{"parts": [{"text": build_prompt(text, self.direction)}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}
                },
            },
        }

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """One API call. Raises RetryableError only for transient conditions."""
        url = f"{API_BASE}/models/{self.model}:generateContent"
        headers = {"x-goog-api-key": self._api_key(), "Content-Type": "application/json"}
        await _pace()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(settings.GEMINI_TIMEOUT_SECONDS, connect=20.0)
            ) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            # Network-level. The exception type alone is safe to surface; the
            # message could echo the request URL, never the header.
            raise RetryableError(f"Gemini unreachable: {type(exc).__name__}") from exc

        if response.status_code == 429 and _daily_quota_spent(response):
            # The service still sends a RetryInfo of a few seconds here, but it
            # is meaningless against a per-day quota — waiting it out cannot
            # succeed. Say so, so the caller stops retrying immediately.
            raise QuotaExhaustedError(
                f"Gemini daily quota exhausted for {self.model}: {_reason(response)}"
            )
        if response.status_code in TRANSIENT_STATUSES:
            raise RetryableError(
                f"Gemini returned {response.status_code} "
                f"({_reason(response)}); eligible for retry",
                retry_after=_retry_after(response),
            )
        if response.status_code >= 400:
            # 400/401/403/404 mean our request or credentials are wrong. Those
            # are ours to fix, so they must not trigger the fallback.
            raise TerminalError(
                f"Gemini rejected the request with {response.status_code}: {_reason(response)}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise TerminalError("Gemini returned a non-JSON response") from exc

    def _extract_pcm(self, payload: dict[str, Any]) -> tuple[bytes, int]:
        """Pull raw PCM and its sample rate out of the response.

        A parsing failure here is our bug, not the provider's, so it is terminal.
        """
        try:
            parts = payload["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            feedback = payload.get("promptFeedback") if isinstance(payload, dict) else None
            raise TerminalError(
                f"Gemini response contained no audio part. promptFeedback={feedback}"
            ) from exc

        inline = next(
            (p.get("inlineData") or p.get("inline_data") for p in parts if
             p.get("inlineData") or p.get("inline_data")),
            None,
        )
        if not inline or "data" not in inline:
            raise TerminalError("Gemini response part carried no inline audio data")

        mime = inline.get("mimeType") or inline.get("mime_type") or ""
        sample_rate = _rate_from_mime(mime)
        try:
            return base64.b64decode(inline["data"]), sample_rate
        except (ValueError, TypeError) as exc:
            raise TerminalError("Gemini audio payload was not valid base64") from exc

    async def synthesize(self, text: str, voice: VoiceSpec, out_path: Path) -> AudioResult:
        if not text or not text.strip():
            raise TerminalError("Cannot synthesize empty narration")

        voice_name = self._validate_voice(voice.voice)
        payload = await self._post(self._build_request(text, voice_name))
        pcm, sample_rate = self._extract_pcm(payload)

        if not pcm:
            raise RetryableError("Gemini returned an empty audio payload")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        duration = write_pcm_as_wav(pcm, out_path, sample_rate=sample_rate)

        log.info(
            "tts.synthesized",
            provider=self.name,
            model=self.model,
            voice=voice_name,
            duration_seconds=round(duration, 2),
            chars=len(text),
            sample_rate=sample_rate,
        )
        return AudioResult(
            path=out_path,
            duration_seconds=duration,
            sample_rate=sample_rate,
            provider=self.name,
            voice=voice_name,
            model=self.model,
            metadata={
                "styled": self.direction is not None,
                "usage": _usage(payload),
            },
        )


# ----------------------------------------------------------------- helpers ---
def _reason(response: httpx.Response) -> str:
    """A short, safe description of an error response.

    Reads the API's own message rather than the raw body, and truncates — the
    request headers (and therefore the key) are never part of this.
    """
    try:
        body = response.json()
        return str(body.get("error", {}).get("message", ""))[:300] or response.reason_phrase
    except ValueError:
        return response.reason_phrase


def _usage(payload: dict[str, Any]) -> dict[str, Any]:
    usage = payload.get("usageMetadata") or {}
    return {
        "prompt_tokens": usage.get("promptTokenCount"),
        "audio_tokens": usage.get("candidatesTokenCount"),
    }


def _rate_from_mime(mime: str) -> int:
    """Parse `audio/l16; rate=24000; channels=1`.

    Falls back to 24000, the documented default, rather than guessing wildly —
    a wrong rate would silently pitch-shift the narration and desynchronise
    every caption.
    """
    for part in mime.split(";"):
        part = part.strip()
        if part.startswith("rate="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                break
    return 24_000


def write_pcm_as_wav(
    pcm: bytes, out_path: Path, *, sample_rate: int = 24_000, channels: int = 1
) -> float:
    """Wrap headerless signed 16-bit PCM in a WAV container.

    Gemini returns `audio/l16`, which is samples only. Writing them straight to
    a .wav produces a file nothing can open; soundfile, ffmpeg and whisper all
    need the RIFF header this adds.
    """
    if len(pcm) % 2:
        # An odd byte count means a truncated final sample; dropping it is
        # correct and inaudible, whereas keeping it corrupts the last frame.
        pcm = pcm[:-1]

    with wave.open(str(out_path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)  # 16-bit
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)

    frames = len(pcm) // (2 * channels)
    return frames / float(sample_rate)


def peak_amplitude(pcm: bytes) -> float:
    """Largest absolute sample, normalised to 0..1. Used by the audition tool."""
    if len(pcm) < 2:
        return 0.0
    count = len(pcm) // 2
    samples = struct.unpack(f"<{count}h", pcm[: count * 2])
    return max(abs(min(samples)), abs(max(samples))) / 32768.0


def _daily_quota_spent(response: httpx.Response) -> bool:
    """Whether a 429 is a per-day quota rather than a rate limit.

    The free tier allows 10 requests per day for the preview TTS model, which
    a five-block render plus a set of voice auditions reaches easily. The two
    cases need opposite handling: wait out a rate limit, give up immediately on
    a daily quota. Google distinguishes them in `QuotaFailure.violations`:

        quotaId: "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    """
    try:
        body = response.json()
    except ValueError:
        return False
    for detail in (body.get("error") or {}).get("details") or []:
        for violation in detail.get("violations") or []:
            if "PerDay" in str(violation.get("quotaId", "")):
                return True
    return False


def _retry_after(response: httpx.Response) -> float | None:
    """How long the service asked us to wait, if it said.

    Google returns a `RetryInfo` detail on a 429 carrying `retryDelay: "27s"`.
    Honouring it is both better behaviour and more reliable than guessing —
    guessing is how the first version exhausted three retries in 4.5 seconds
    against a per-minute limit and fell back to the wrong voice.
    """
    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    try:
        body = response.json()
    except ValueError:
        return None
    for detail in (body.get("error") or {}).get("details") or []:
        delay = detail.get("retryDelay")
        if isinstance(delay, str):
            match = re.fullmatch(r"(\d+(?:\.\d+)?)s?", delay.strip())
            if match:
                return float(match.group(1))
    return None


async def sleep_backoff(attempt: int, base: float = 5.0, error: Exception | None = None) -> None:
    """Wait before the next attempt.

    Prefers the delay the service itself asked for. Failing that, backs off
    exponentially with a base chosen so that the *sum* of the default three
    attempts (5s + 15s + 45s) outlasts a 60-second rate-limit window — the
    whole point of retrying a 429 is to still be trying when the window resets.
    The original 1.5s base summed to 4.5s, which never was.
    """
    asked = getattr(error, "retry_after", None)
    if asked:
        wait = min(float(asked) + 1.0, 70.0)
    else:
        wait = min(base * (3 ** (attempt - 1)), 65.0)
    log.info(
        "tts.backoff",
        attempt=attempt,
        wait_seconds=round(wait, 1),
        asked_by_server=bool(asked),
    )
    await asyncio.sleep(wait)
