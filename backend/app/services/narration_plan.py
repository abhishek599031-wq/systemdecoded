"""What the renderer is about to ask the TTS provider for.

This exists so that one question has one answer: **exactly how many requests
will this render make?**

Before, the answer was implicit in a loop inside `production._synthesize_blocks`
— which is fine right up until something else needs to know it in advance. A
quota preflight that estimates the count from word counts, or that re-derives it
with its own copy of the splitting rules, is a preflight that can disagree with
the renderer. It would approve a render needing five requests, the renderer
would make seven, and the guarantee would be worthless.

So the plan is built once and both consume it: the preflight counts
`request_count`, and the renderer speaks `plan.blocks` verbatim. They cannot
drift, because there is only one of them.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import TerminalError
from app.models.content import Scene
from app.services.prosody import segment_narration

__all__ = ["NarrationBlock", "NarrationPlan", "SpokenUnit", "build_narration_plan"]


@dataclass(frozen=True, slots=True)
class SpokenUnit:
    """One thing to synthesize, and the silence that follows it.

    For a whole-block provider this is an entire scene. For a clause-level
    provider it is one clause, with a pause chosen by intent.
    """

    text: str
    pause_after: float


@dataclass(frozen=True, slots=True)
class NarrationBlock:
    """One scene's narration: a complete beat of the story."""

    scene: Scene
    units: tuple[SpokenUnit, ...]

    @property
    def scene_number(self) -> int:
        return self.scene.scene_number


@dataclass(frozen=True, slots=True)
class NarrationPlan:
    blocks: tuple[NarrationBlock, ...]
    provider: str
    voice: str
    model: str | None
    whole_block: bool

    @property
    def request_count(self) -> int:
        """Exactly how many synthesis requests this render will make.

        Counted from the units that will actually be spoken — not estimated
        from word or character counts.
        """
        return sum(len(block.units) for block in self.blocks)

    def as_metadata(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "voice": self.voice,
            "blocks": len(self.blocks),
            "requests": self.request_count,
            "whole_block": self.whole_block,
        }


def build_narration_plan(
    scenes: list[Scene],
    *,
    whole_block: bool,
    provider: str,
    voice: str,
    model: str | None = None,
) -> NarrationPlan:
    """Decide what will be spoken, and in how many pieces.

    `whole_block` comes from the provider's own `prefers_whole_block`, so the
    plan reflects the provider that will actually run — a Gemini plan is five
    requests where the same script through Kokoro is fourteen.
    """
    if not scenes:
        raise TerminalError("Script has no scenes")

    blocks: list[NarrationBlock] = []
    for scene in sorted(scenes, key=lambda s: s.scene_number):
        if not (scene.narration or "").strip():
            raise TerminalError(f"Scene {scene.scene_number} has no speakable narration")

        if whole_block:
            # The provider shapes its own internal rhythm, so the block goes
            # over in one piece: one scene, one request.
            units = (SpokenUnit(text=scene.narration, pause_after=0.0),)
        else:
            reveals = frozenset((scene.template_props or {}).get("reveal_segments") or [])
            segments = segment_narration(scene.narration, reveal_indexes=reveals)
            if not segments:
                raise TerminalError(
                    f"Scene {scene.scene_number} has no speakable narration"
                )
            units = tuple(
                SpokenUnit(text=s.text, pause_after=s.pause_seconds) for s in segments
            )

        blocks.append(NarrationBlock(scene=scene, units=units))

    return NarrationPlan(
        blocks=tuple(blocks),
        provider=provider,
        voice=voice,
        model=model,
        whole_block=whole_block,
    )
