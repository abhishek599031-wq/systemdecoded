"""Build semantic animation cues from the canonical caption timeline.

Motion Canvas consumes this payload directly.  The approved narration remains
the source of text, and the existing canonical alignment remains the only
source of word timing; this module merely names useful moments in that shared
timeline for the visual renderer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import TerminalError
from app.providers.base import WordTiming
from app.services.canonical_alignment import normalise_token


@dataclass(frozen=True, slots=True)
class MotionCue:
    id: str
    phrase: str
    start: float
    end: float

    def as_dict(self) -> dict[str, str | float]:
        return {
            "id": self.id,
            "phrase": self.phrase,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
        }


def _find_phrase(words: list[WordTiming], phrase: str) -> tuple[float, float]:
    needle = [normalise_token(token) for token in phrase.split()]
    haystack = [normalise_token(word.word) for word in words]
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start : start + len(needle)] == needle:
            last = start + len(needle) - 1
            return words[start].start, words[last].end
    raise TerminalError(f"Motion cue phrase {phrase!r} is absent from canonical timing")


def build_motion_timing_payload(
    words: list[WordTiming],
    audio_duration: float,
    cues: dict[str, str],
) -> dict:
    """Return explicit word and semantic timing for an audio-backed scene."""
    if audio_duration <= 0:
        raise TerminalError("Motion timing requires a positive audio duration")
    if not words:
        raise TerminalError("Motion timing requires canonical word timestamps")

    previous = 0.0
    for word in words:
        if word.start < previous or word.end < word.start or word.end > audio_duration + 0.05:
            raise TerminalError("Canonical word timing is not monotonic or exceeds the audio")
        previous = word.end

    events = [
        MotionCue(event_id, phrase, *_find_phrase(words, phrase)).as_dict()
        for event_id, phrase in cues.items()
    ]
    return {
        "schema_version": 1,
        "source": "canonical-caption-word-timing",
        "audio_duration_seconds": round(audio_duration, 3),
        "words": [
            {"word": word.word, "start": round(word.start, 3), "end": round(word.end, 3)}
            for word in words
        ],
        "events": events,
    }
