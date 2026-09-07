"""Voice auditions.

Generates the same sample line in several voices so they can be compared
fairly. Every variable except the voice is held constant — identical script,
identical style direction, identical audio settings, identical peak
normalisation — because anything else makes the comparison meaningless.

This is a decision aid, not part of the production pipeline. It writes to
`media/voice_auditions/` and touches no project state.
"""

from __future__ import annotations

import asyncio
import wave
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.core.errors import RetryableError, TerminalError
from app.core.logging import get_logger
from app.providers.base import VoiceSpec
from app.providers.tts.gemini import GeminiTTS, peak_amplitude

log = get_logger("voice_audition")

# The sample every voice reads. Short, but it contains the things that separate
# a good read from a flat one: a trailing-off question, a hard reveal, and a
# plain factual close.
AUDITION_SCRIPT = (
    "That six-digit code...\n"
    "in your authenticator app?\n\n"
    "Nobody sent it to you.\n\n"
    "Your phone calculated it."
)

# Requested shortlist. Validated against the provider's published voice list
# before any request is made, so an invalid id is reported rather than silently
# swapped for something else.
DEFAULT_SHORTLIST = ("Charon", "Sadaltager", "Algieba", "Gacrux", "Achird")

# Target peak for normalisation. Loudness differences between voices are an
# artefact of the model, not a quality signal, and the louder sample always
# sounds "better" if left uncorrected.
TARGET_PEAK = 0.89

# Seconds between voices. The preview TTS model's free tier allows only a
# couple of requests per minute, and a burst returns 429 for every voice after
# the first. Auditions run rarely, so waiting is far better than failing.
DEFAULT_PACE_SECONDS = 32.0

# Per-voice retries. Separate from the production fallback on purpose: an
# audition must come from Gemini or fail visibly — silently substituting Kokoro
# would produce a file labelled with a Gemini voice it never used.
AUDITION_ATTEMPTS = 4
AUDITION_BACKOFF_SECONDS = 45.0


@dataclass(frozen=True, slots=True)
class Audition:
    voice: str
    path: Path
    duration_seconds: float
    sample_rate: int
    peak_before: float
    peak_after: float
    model: str


def audition_dir() -> Path:
    return settings.MEDIA_ROOT / "voice_auditions"


def _normalise_peak(path: Path, target: float = TARGET_PEAK) -> tuple[float, float]:
    """Scale a WAV to a fixed peak so voices compare on tone, not volume."""
    with wave.open(str(path), "rb") as wav:
        params = wav.getparams()
        frames = wav.readframes(wav.getnframes())

    before = peak_amplitude(frames)
    if before <= 0:
        return 0.0, 0.0

    gain = target / before
    count = len(frames) // 2
    import struct

    samples = struct.unpack(f"<{count}h", frames[: count * 2])
    scaled = struct.pack(
        f"<{count}h",
        *(max(-32768, min(32767, int(s * gain))) for s in samples),
    )

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(params.nchannels)
        wav.setsampwidth(params.sampwidth)
        wav.setframerate(params.framerate)
        wav.writeframes(scaled)

    return before, peak_amplitude(scaled)


async def _synthesize_with_retry(
    provider: GeminiTTS, script: str, voice: str, path: Path
):
    """Synthesize one voice, retrying rate limits but never falling back."""
    last: Exception | None = None
    for attempt in range(1, AUDITION_ATTEMPTS + 1):
        try:
            return await provider.synthesize(script, VoiceSpec(voice=voice), path)
        except RetryableError as exc:
            last = exc
            if attempt == AUDITION_ATTEMPTS:
                break
            wait = AUDITION_BACKOFF_SECONDS * attempt
            log.warning(
                "audition.rate_limited",
                voice=voice,
                attempt=attempt,
                of=AUDITION_ATTEMPTS,
                retry_in_seconds=wait,
            )
            await asyncio.sleep(wait)
    raise TerminalError(
        f"Could not audition {voice} after {AUDITION_ATTEMPTS} attempts: {last}"
    )


async def generate_auditions(
    voices: tuple[str, ...] = DEFAULT_SHORTLIST,
    script: str = AUDITION_SCRIPT,
    out_dir: Path | None = None,
    pace_seconds: float = DEFAULT_PACE_SECONDS,
) -> list[Audition]:
    """Render `script` once per voice with identical settings."""
    provider = GeminiTTS()
    available = set(await provider.list_voices())

    unknown = [v for v in voices if v not in available]
    if unknown:
        # Deliberately not substituted silently — a swapped voice would make the
        # audition report a name the audio does not match.
        raise TerminalError(
            f"Unknown Gemini voice(s): {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(available))}"
        )

    out_dir = out_dir or audition_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[Audition] = []
    for index, voice in enumerate(voices):
        if index:
            await asyncio.sleep(pace_seconds)
        path = out_dir / f"gemini_{voice.lower()}.wav"
        result = await _synthesize_with_retry(provider, script, voice, path)
        before, after = _normalise_peak(path)
        results.append(
            Audition(
                voice=voice,
                path=path,
                duration_seconds=result.duration_seconds,
                sample_rate=result.sample_rate,
                peak_before=before,
                peak_after=after,
                model=result.model or settings.GEMINI_TTS_MODEL,
            )
        )
        log.info(
            "audition.generated",
            voice=voice,
            duration_seconds=round(result.duration_seconds, 2),
            file=path.name,
        )

    return results
