"""Caption construction and the content state machine.

Both are pure logic, so they get thorough cheap tests. The state-machine graph
tests in particular catch a class of bug that is otherwise only visible when a
project gets stuck in production.
"""

from __future__ import annotations

import itertools

import pytest

from app.core.errors import InvalidStateTransition
from app.core.state_machine import (
    LEGAL,
    can_transition,
    non_terminal_sinks,
    unreachable_states,
)
from app.models.enums import ProjectStatus as S
from app.providers.base import WordTiming
from app.providers.compositor.captions import (
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_WORDS,
    build_ass,
    chunk_words,
)


def words(*specs: tuple[str, float, float]) -> list[WordTiming]:
    return [WordTiming(word=w, start=s, end=e) for w, s, e in specs]


# ------------------------------------------------------------------ chunking ---
def test_chunks_break_on_sentence_end() -> None:
    """A full stop is a hard break — captions should not straddle sentences."""
    chunks = chunk_words(
        words(("Nobody", 0.0, 0.3), ("sent", 0.3, 0.6), ("it.", 0.6, 0.9), ("Here", 1.0, 1.3))
    )
    assert chunks[0].text == "Nobody sent it."
    assert chunks[1].text == "Here"


def test_chunks_respect_the_word_limit() -> None:
    long_run = words(*[(f"w{i}", i * 0.2, i * 0.2 + 0.2) for i in range(12)])
    for chunk in chunk_words(long_run):
        assert len(chunk.words) <= DEFAULT_MAX_WORDS


def test_chunks_respect_the_character_limit() -> None:
    """A phone screen, not a desktop preview, sets this budget."""
    wide = words(("elephantine", 0, 0.4), ("magnificently", 0.4, 0.9), ("verbose", 0.9, 1.3))
    for chunk in chunk_words(wide):
        assert len(chunk.text) <= DEFAULT_MAX_CHARS or len(chunk.words) == 1


def test_a_long_pause_forces_a_new_chunk() -> None:
    """Captions should track how the line is actually spoken."""
    spaced = words(("before", 0.0, 0.4), ("after", 3.0, 3.4))
    assert len(chunk_words(spaced)) == 2


def test_chunking_preserves_every_word() -> None:
    original = words(*[(f"w{i}", i * 0.3, i * 0.3 + 0.25) for i in range(9)])
    rebuilt = [w.word for chunk in chunk_words(original) for w in chunk.words]
    assert rebuilt == [w.word for w in original]


def test_empty_input_produces_no_chunks() -> None:
    assert chunk_words([]) == []


# ----------------------------------------------------------------------- ASS ---
def test_ass_has_required_sections() -> None:
    doc = build_ass(words(("Hello", 0.0, 0.4), ("world.", 0.4, 0.9)))
    assert "[Script Info]" in doc
    assert "[V4+ Styles]" in doc
    assert "[Events]" in doc
    assert "Style: SD," in doc


def test_ass_declares_the_shorts_frame() -> None:
    doc = build_ass(words(("Hi.", 0.0, 0.3)), width=1080, height=1920)
    assert "PlayResX: 1080" in doc
    assert "PlayResY: 1920" in doc


def test_ass_emits_one_event_per_word_when_highlighting() -> None:
    doc = build_ass(words(("one", 0, 0.3), ("two", 0.3, 0.6), ("three.", 0.6, 0.9)))
    assert doc.count("Dialogue:") == 3


def test_ass_highlights_exactly_one_word_per_event() -> None:
    from app.providers.compositor.captions import CYAN_BGR

    doc = build_ass(words(("one", 0, 0.3), ("two", 0.3, 0.6)))
    for line in [ln for ln in doc.splitlines() if ln.startswith("Dialogue:")]:
        assert line.count(f"\\c{CYAN_BGR}") == 1


def test_ass_without_highlighting_emits_one_event_per_chunk() -> None:
    doc = build_ass(
        words(("one", 0, 0.3), ("two", 0.3, 0.6), ("three.", 0.6, 0.9)),
        highlight_active_word=False,
    )
    assert doc.count("Dialogue:") == 1


def test_ass_timestamps_are_centisecond_format() -> None:
    import re

    doc = build_ass(words(("hi", 61.5, 62.0)))
    assert re.search(r"Dialogue: 0,0:01:01\.\d{2},", doc)


def test_ass_escapes_brace_characters() -> None:
    """Unescaped braces are ASS override tags and would corrupt rendering."""
    doc = build_ass(words(("{tricky}", 0.0, 0.4)))
    assert "\\{tricky\\}" in doc


def test_zero_length_word_still_gets_visible_duration() -> None:
    doc = build_ass(words(("blip", 1.0, 1.0)))
    assert "Dialogue:" in doc


def test_caption_margin_keeps_text_out_of_the_shorts_overlay() -> None:
    from app.providers.compositor.captions import DEFAULT_MARGIN_V

    # The Shorts UI occupies roughly the bottom 380px of a 1920px frame.
    assert DEFAULT_MARGIN_V > 380


# ------------------------------------------------------------- state machine ---
def test_every_state_is_reachable() -> None:
    assert unreachable_states() == set()


def test_no_accidental_dead_ends() -> None:
    """A non-terminal state with no exit strands a project forever."""
    assert non_terminal_sinks() == set()


def test_terminal_states_have_no_exits() -> None:
    for state in S.terminal():
        assert LEGAL[state] == set()


def test_happy_path_is_walkable() -> None:
    path = [
        S.IDEA, S.IDEA_APPROVED, S.RESEARCHING, S.RESEARCH_READY,
        S.SCRIPT_GENERATING, S.SCRIPT_REVIEW, S.SCRIPT_APPROVED,
        S.PRODUCTION_PLANNING, S.ASSETS_READY, S.RENDERING,
        S.VIDEO_REVIEW, S.APPROVED_FOR_PUBLISHING, S.AWAITING_HUMAN_UPLOAD,
        S.PUBLISHED, S.COMPLETED,
    ]
    for current, nxt in itertools.pairwise(path):
        assert can_transition(current, nxt), f"{current} -> {nxt} should be legal"


def test_rendering_cannot_skip_review() -> None:
    """Human review is a gate, not a suggestion."""
    assert not can_transition(S.RENDERING, S.APPROVED_FOR_PUBLISHING)
    assert not can_transition(S.RENDERING, S.PUBLISHED)


def test_failed_cannot_jump_straight_back_to_rendering() -> None:
    """A failed render is retriaged, never silently retried."""
    assert not can_transition(S.FAILED, S.RENDERING)
    assert can_transition(S.FAILED, S.NEEDS_REVISION)
    assert can_transition(S.NEEDS_REVISION, S.RENDERING)


def test_unknown_states_are_rejected() -> None:
    assert not can_transition("NOT_A_STATE", "IDEA")
    assert not can_transition("IDEA", "NOT_A_STATE")


@pytest.mark.parametrize("state", sorted(S.needs_human(), key=lambda s: s.value))
def test_human_gate_states_are_real_states(state: S) -> None:
    assert state in LEGAL


def test_invalid_transition_raises_with_the_allowed_set() -> None:
    err = InvalidStateTransition(
        "nope", detail={"from": "IDEA", "to": "PUBLISHED", "allowed": ["IDEA_APPROVED"]}
    )
    assert err.status_code == 409
    assert "allowed" in err.detail
