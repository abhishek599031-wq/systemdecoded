"""TTS provider selection, retry and fallback.

The pipeline asks for narration; this decides who produces it and what happens
when that fails. Downstream code sees one `TTSProvider` and one `AudioResult`
either way (ARCH §5.3).

The central rule:

    Transient provider failure  ->  retry, then fall back to Kokoro.
    Configuration / our own bug ->  surface immediately, never fall back.

That split is the whole point. Falling back on a missing API key or a bad voice
name would mean the system quietly narrates every video with the wrong voice
and nothing ever reports a problem — the failure would only be discovered by
listening. `TerminalError` is therefore never caught here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.errors import QuotaExhaustedError, RetryableError, TerminalError
from app.core.logging import get_logger
from app.providers.base import AudioResult, TTSProvider, VoiceSpec
from app.providers.tts.gemini import GeminiTTS, sleep_backoff
from app.providers.tts.kokoro import KokoroTTS

log = get_logger("tts.resolver")

# Distinguishes "caller did not specify a fallback, use the configured one"
# from "caller explicitly wants no fallback". Plain None cannot express both,
# and conflating them silently constructs a provider the caller did not ask for.
_UNSET: Any = object()


def build_provider(name: str) -> TTSProvider:
    """Construct a provider by configuration name."""
    if name == "gemini":
        return GeminiTTS()
    if name == "kokoro":
        return KokoroTTS()
    raise TerminalError(f"Unknown TTS provider {name!r}. Expected 'gemini' or 'kokoro'.")


def voice_for(provider_name: str) -> VoiceSpec:
    """The configured voice for a provider.

    Each provider has its own voice namespace — Kokoro's `am_puck` means
    nothing to Gemini and vice versa — so the voice is resolved alongside the
    provider rather than passed down from a single global setting.
    """
    if provider_name == "gemini":
        return VoiceSpec(voice=settings.GEMINI_TTS_VOICE, speed=1.0, lang=settings.TTS_LANG)
    return VoiceSpec(voice=settings.TTS_VOICE, speed=settings.TTS_SPEED, lang=settings.TTS_LANG)


class ResilientTTS:
    """A `TTSProvider` that retries its primary and falls back on exhaustion.

    Implements the same interface it wraps, so callers cannot tell the
    difference — except through `AudioResult.provider` and `.fallback_used`,
    which always report what genuinely produced the audio.
    """

    def __init__(
        self,
        primary: TTSProvider | None = None,
        fallback: TTSProvider | None = _UNSET,
        max_attempts: int | None = None,
        backoff: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        self.primary_name = settings.TTS_PROVIDER
        self.fallback_name = settings.TTS_FALLBACK_PROVIDER
        self.primary = primary or build_provider(self.primary_name)

        if fallback is _UNSET:
            # Not specified: use the configured fallback, unless that would
            # just be the primary again.
            self.fallback = (
                build_provider(self.fallback_name)
                if self.fallback_name != "none" and self.fallback_name != self.primary_name
                else None
            )
        else:
            self.fallback = fallback

        self.max_attempts = max_attempts or settings.GEMINI_MAX_ATTEMPTS
        self._backoff = backoff or sleep_backoff

    @property
    def name(self) -> str:
        return f"resilient({self.primary.name})"

    @property
    def prefers_whole_block(self) -> bool:
        """Follows the primary.

        Text is segmented before synthesis begins, so the decision has to be
        made against the provider we intend to use. A fallback mid-render then
        speaks blocks Kokoro would have preferred split — acceptable, because
        the alternative is shaping every render around a provider that usually
        never runs.
        """
        return self.primary.prefers_whole_block

    async def list_voices(self) -> list[str]:
        return await self.primary.list_voices()

    async def synthesize(
        self, text: str, voice: VoiceSpec | None = None, out_path: Path | None = None
    ) -> AudioResult:
        if out_path is None:
            raise TerminalError("synthesize() requires an output path")
        spec = voice or voice_for(self.primary_name)

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await self.primary.synthesize(text, spec, out_path)
            except TerminalError:
                # Our mistake or a permanent rejection. Fallback would hide it.
                raise
            except QuotaExhaustedError as exc:
                # Spent for the day. Retrying cannot succeed and only delays
                # the caller by a minute per block.
                last_error = exc
                log.warning(
                    "tts.quota_exhausted",
                    provider=self.primary.name,
                    attempt=attempt,
                    error=str(exc)[:200],
                )
                break
            except RetryableError as exc:
                last_error = exc
                log.warning(
                    "tts.primary_attempt_failed",
                    provider=self.primary.name,
                    attempt=attempt,
                    of=self.max_attempts,
                    error=str(exc)[:200],
                )
                if attempt < self.max_attempts:
                    # The error is passed through so a rate limit's own
                    # requested delay is honoured rather than guessed at.
                    await self._backoff(attempt, error=exc)

        if self.fallback is None:
            raise RetryableError(
                f"{self.primary.name} failed after {self.max_attempts} attempts and no "
                f"fallback is configured: {last_error}"
            )

        log.warning(
            "tts.falling_back",
            primary=self.primary.name,
            fallback=self.fallback.name,
            attempts=self.max_attempts,
            reason=str(last_error)[:200],
        )
        fallback_spec = voice_for(self.fallback_name)
        result = await self.fallback.synthesize(text, fallback_spec, out_path)

        # Rebuild rather than mutate: AudioResult is frozen, and the record must
        # say the fallback produced this audio, not the primary.
        return AudioResult(
            path=result.path,
            duration_seconds=result.duration_seconds,
            sample_rate=result.sample_rate,
            provider=result.provider,
            voice=result.voice,
            model=result.model,
            fallback_used=True,
            generated_at=result.generated_at,
            metadata={
                **result.metadata,
                "fallback_from": self.primary.name,
                "fallback_reason": str(last_error)[:300],
                "primary_attempts": self.max_attempts,
            },
        )


def get_narration_provider() -> TTSProvider:
    """The provider the production pipeline should use."""
    return ResilientTTS()
