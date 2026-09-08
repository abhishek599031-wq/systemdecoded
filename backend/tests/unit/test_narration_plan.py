"""The narration plan is the single answer to "how many requests?".

The quota preflight is only worth having if its count matches what the renderer
actually does. A preflight that estimates from word counts, or that re-derives
the splitting rules in a second place, can approve a five-request render that
then makes seven. These tests pin the count to the units that will be spoken.
"""

from __future__ import annotations

import pytest

from app.core.errors import TerminalError
from app.services.narration_plan import build_narration_plan


class FakeScene:
    """Enough of a Scene for planning: narration, number, props."""

    def __init__(self, number: int, narration: str, props: dict | None = None) -> None:
        self.scene_number = number
        self.narration = narration
        self.template_props = props or {}


SCENES = [
    FakeScene(1, "That six-digit code in your authenticator app? Nobody sent it to you."),
    FakeScene(2, "There's no message. No network call. Your phone works it out on its own."),
    FakeScene(3, "When you set the app up, it and the server agreed on one shared secret."),
    FakeScene(4, "Both sides add the current time and run the same calculation. Same answer."),
    FakeScene(5, "Nothing is transmitted. That's TOTP. Decoded."),
]


def _plan(scenes=None, *, whole_block=True, provider="gemini"):
    return build_narration_plan(
        scenes if scenes is not None else SCENES,
        whole_block=whole_block,
        provider=provider,
        voice="Algieba",
        model="gemini-3.1-flash-tts-preview",
    )


def test_whole_block_provider_needs_one_request_per_scene():
    plan = _plan()
    assert len(plan.blocks) == 5
    assert plan.request_count == 5


def test_clause_provider_needs_more_requests_for_the_same_script():
    """The count is a property of the provider, not just the script."""
    assert _plan(whole_block=False).request_count > _plan(whole_block=True).request_count


def test_request_count_equals_the_units_that_will_be_spoken():
    """Counted, never estimated."""
    for whole_block in (True, False):
        plan = _plan(whole_block=whole_block)
        assert plan.request_count == sum(len(b.units) for b in plan.blocks)


def test_request_count_is_not_derived_from_length():
    """A longer script with the same scene count costs the same in whole-block mode."""
    longer = [FakeScene(i + 1, SCENES[i].narration * 4) for i in range(5)]
    assert _plan(longer).request_count == _plan(SCENES).request_count == 5


def test_blocks_keep_scene_order():
    plan = _plan(list(reversed(SCENES)))
    assert [b.scene_number for b in plan.blocks] == [1, 2, 3, 4, 5]


def test_whole_block_unit_is_the_untouched_scene_narration():
    plan = _plan()
    for block, scene in zip(plan.blocks, SCENES, strict=True):
        assert len(block.units) == 1
        assert block.units[0].text == scene.narration


def test_clause_units_carry_intent_shaped_pauses():
    plan = _plan(whole_block=False)
    pauses = [u.pause_after for b in plan.blocks for u in b.units]
    assert any(p > 0 for p in pauses), "clause mode should insert pauses"


def test_empty_script_is_refused():
    with pytest.raises(TerminalError, match="no scenes"):
        _plan([])


def test_scene_with_no_narration_is_refused():
    with pytest.raises(TerminalError, match="no speakable narration"):
        _plan([FakeScene(1, "   ")])


def test_metadata_reports_what_the_render_will_do():
    meta = _plan().as_metadata()
    assert meta["requests"] == 5
    assert meta["blocks"] == 5
    assert meta["voice"] == "Algieba"
    assert meta["whole_block"] is True


def test_plan_is_immutable():
    """It is handed to both the preflight and the renderer; neither may edit it."""
    plan = _plan()
    with pytest.raises((AttributeError, TypeError)):
        plan.blocks = ()  # type: ignore[misc]
