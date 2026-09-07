"""The SystemDecoded narration identity, in one place.

Two things live here, and they belong together.

**Performance direction.** Gemini's TTS models accept natural-language style
direction alongside the text to speak. That direction *is* the channel's
narration identity, so it is a single named constant rather than a string
duplicated at each call site — the same reason the visual identity lives in
`scene_templates/_base/tokens.css`. Providers that cannot take direction
(Kokoro) ignore it and simply speak the text.

**Narration profile.** A voice is four things that must travel together:

    provider   who synthesizes it
    model      which model of theirs
    voice      which voice of that model
    direction  how it should be performed

Split across four unrelated settings, they drift, and a channel ends up with
audio nobody can reproduce. Declared as one object, "what does SystemDecoded
sound like?" has a single answer, and a second profile later is a new constant
rather than a refactor.

This module sits in `app.core` rather than under `app.providers.tts` because
`app.config` reads its defaults from here, and the provider package imports
`app.config` — putting it there would close an import cycle.

The profile is a default, not a lock: `settings` still overrides every field,
which is what makes auditions and A/B comparisons possible without code edits.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

__all__ = [
    "SYSTEMDECODED_DIRECTION",
    "SYSTEMDECODED_NARRATION",
    "NarrationProfile",
    "VoiceDirection",
    "build_prompt",
]


@dataclass(frozen=True, slots=True)
class VoiceDirection:
    """Reusable performance direction for a narration provider."""

    role: str
    tone: str
    style: str
    energy: str
    delivery: tuple[str, ...]
    avoid: tuple[str, ...]

    def as_instruction(self) -> str:
        """Render as the instruction block prefixed to the spoken text."""
        avoid = " ".join(self.avoid)
        delivery = " ".join(self.delivery)
        return (
            f"You are {self.role} "
            f"Tone: {self.tone} "
            f"Style: {self.style} "
            f"Energy: {self.energy} "
            f"Delivery: {delivery} "
            f"Avoid: {avoid}"
        )

    @property
    def fingerprint(self) -> str:
        """Short stable id for this direction.

        Recorded on a render so two takes that sound different can be traced to
        a changed performance prompt, without copying the whole instruction
        block into every database row.
        """
        return hashlib.sha256(self.as_instruction().encode("utf-8")).hexdigest()[:12]


SYSTEMDECODED_DIRECTION = VoiceDirection(
    role=(
        "an intelligent technology storyteller explaining an interesting "
        "concept to a curious adult viewer."
    ),
    tone="natural, conversational, clear and confident.",
    style="a knowledgeable person explaining something fascinating to a friend.",
    energy="moderately engaging but never exaggerated.",
    delivery=(
        "Vary the pace naturally.",
        "Put subtle emphasis on surprising statements.",
        "Slow slightly around important reveals.",
        "Keep technical explanations flowing smoothly.",
        "Leave realistic pauses between thoughts.",
        "Do not give every sentence the same cadence.",
    ),
    avoid=(
        "Commercial-announcer delivery.",
        "Generic AI-narrator cadence.",
        "Over-enthusiastic YouTube-presenter voice.",
        "Robotic rhythm.",
        "Overly dramatic pauses.",
        "Fake excitement.",
    ),
)


def build_prompt(text: str, direction: VoiceDirection | None) -> str:
    """Combine style direction with the text to be spoken.

    The separator matters. Gemini treats a leading instruction followed by an
    explicit "Now read this aloud:" marker as direction rather than script; a
    bare concatenation risks the model narrating the instructions themselves.
    Auditions verify this by checking the produced audio is the length the
    script implies, not the length the instruction block would add.
    """
    if direction is None:
        return text
    return (
        f"{direction.as_instruction()}\n\n"
        "Now read the following text aloud, and read nothing else:\n\n"
        f"{text}"
    )


@dataclass(frozen=True, slots=True)
class NarrationProfile:
    """Provider, model, voice and direction as one reproducible unit."""

    provider: str
    model: str
    voice: str
    direction: VoiceDirection
    fallback_provider: str

    def as_metadata(self) -> dict[str, str]:
        """For persisting on a render, so the audio stays reproducible."""
        return {
            "narration_profile": "systemdecoded",
            "provider": self.provider,
            "model": self.model,
            "voice": self.voice,
            "direction": self.direction.fingerprint,
        }


# Selected after auditioning Charon, Sadaltager, Algieba, Gacrux and Achird on an
# identical script with identical settings and peak-normalised output.
SYSTEMDECODED_NARRATION = NarrationProfile(
    provider="gemini",
    model="gemini-3.1-flash-tts-preview",
    voice="Algieba",
    direction=SYSTEMDECODED_DIRECTION,
    fallback_provider="kokoro",
)
