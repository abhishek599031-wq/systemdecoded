"""Narration segmentation and pause shaping.

Feeding a whole paragraph to a TTS model produces uniform cadence: every
sentence lands with the same rhythm and the same tiny gap, which is most of
what makes synthetic narration sound synthetic.

Instead, narration is split into the units a person would actually speak —
clauses and sentences — synthesized separately, and reassembled with pauses
chosen by *intent* rather than a fixed constant. A beat after a reveal is not
the same as a beat between two items in a list.

Everything here is pure text handling, so it is cheap to test and easy to tune
without re-rendering a video.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

__all__ = ["PauseKind", "Segment", "segment_narration", "total_pause_seconds"]


class PauseKind(StrEnum):
    """Why a pause exists, which is what decides how long it is."""

    CLAUSE = "clause"  # inside a thought: "Same inputs, same answer"
    SENTENCE = "sentence"  # between thoughts
    BEAT = "beat"  # deliberate suspense, marked with an ellipsis
    REVEAL = "reveal"  # after the line the whole scene exists to deliver
    NONE = "none"  # last segment of a scene; the scene gap covers it


# Tuned by listening, not derived. Ranges follow the brief's guidance:
# clause ~100-180ms, sentence ~200-350ms, major reveal ~350-550ms.
PAUSE_SECONDS: dict[PauseKind, float] = {
    PauseKind.CLAUSE: 0.14,
    PauseKind.SENTENCE: 0.28,
    PauseKind.BEAT: 0.42,
    PauseKind.REVEAL: 0.50,
    PauseKind.NONE: 0.0,
}


@dataclass(frozen=True, slots=True)
class Segment:
    """One spoken unit plus the silence that follows it."""

    text: str
    pause_kind: PauseKind

    @property
    def pause_seconds(self) -> float:
        return PAUSE_SECONDS[self.pause_kind]


# Split after sentence-ending punctuation, and after an ellipsis, keeping the
# punctuation with the segment it belongs to.
_SPLIT = re.compile(r"(?<=[.!?])\s+|(?<=\.{3})\s+")


def _classify(text: str, *, is_last: bool, reveal: bool) -> PauseKind:
    if is_last:
        # The inter-scene gap already provides separation; adding another pause
        # here is what creates audible dead air between scenes.
        return PauseKind.NONE
    stripped = text.rstrip()
    if stripped.endswith("..."):
        return PauseKind.BEAT
    if reveal:
        return PauseKind.REVEAL
    if stripped.endswith((".", "!", "?")):
        return PauseKind.SENTENCE
    return PauseKind.CLAUSE


def segment_narration(text: str, *, reveal_indexes: frozenset[int] = frozenset()) -> list[Segment]:
    """Split narration into spoken units with intent-shaped pauses.

    Args:
        text: the scene's narration.
        reveal_indexes: indexes of segments that land a payoff and deserve a
            longer beat after them. Declared per scene rather than guessed,
            because "which line is the reveal" is an editorial decision.
    """
    raw = [part.strip() for part in _SPLIT.split(text.strip()) if part.strip()]
    if not raw:
        return []

    segments: list[Segment] = []
    for index, part in enumerate(raw):
        segments.append(
            Segment(
                text=part,
                pause_kind=_classify(
                    part, is_last=index == len(raw) - 1, reveal=index in reveal_indexes
                ),
            )
        )
    return segments


def total_pause_seconds(segments: list[Segment]) -> float:
    return sum(s.pause_seconds for s in segments)


def speaking_text(segments: list[Segment]) -> str:
    """Rejoin segments into the transcript used for alignment."""
    return " ".join(s.text for s in segments)
