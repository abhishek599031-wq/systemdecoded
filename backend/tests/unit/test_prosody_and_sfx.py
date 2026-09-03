"""Narration segmentation and pause shaping.

Uniform cadence is most of what makes synthetic narration sound synthetic, so
the rules that break it up are worth pinning down.
"""

from __future__ import annotations

import pytest

from app.services.prosody import (
    PAUSE_SECONDS,
    PauseKind,
    segment_narration,
    speaking_text,
    total_pause_seconds,
)


def test_splits_on_sentence_boundaries() -> None:
    segments = segment_narration(
        "There's no message. No network call. Your phone works it out."
    )
    assert [s.text for s in segments] == [
        "There's no message.",
        "No network call.",
        "Your phone works it out.",
    ]


def test_question_ends_a_segment() -> None:
    segments = segment_narration("That code in your app? Nobody sent it.")
    assert len(segments) == 2
    assert segments[0].text.endswith("?")


def test_last_segment_has_no_pause() -> None:
    """The inter-scene gap covers it; another pause here is dead air."""
    segments = segment_narration("One. Two. Three.")
    assert segments[-1].pause_kind is PauseKind.NONE
    assert segments[-1].pause_seconds == 0.0


def test_ellipsis_produces_a_longer_beat() -> None:
    segments = segment_narration("Wait for it... Here it is. Done.")
    assert segments[0].pause_kind is PauseKind.BEAT
    assert segments[0].pause_seconds > PAUSE_SECONDS[PauseKind.SENTENCE]


def test_declared_reveal_gets_the_longest_pause() -> None:
    """Which line is the payoff is an editorial call, not something to guess."""
    plain = segment_narration("Nobody sent it. It was calculated. Done.")
    marked = segment_narration(
        "Nobody sent it. It was calculated. Done.", reveal_indexes=frozenset({0})
    )
    assert marked[0].pause_kind is PauseKind.REVEAL
    assert marked[0].pause_seconds > plain[0].pause_seconds


def test_pause_ranges_follow_the_brief() -> None:
    assert 0.10 <= PAUSE_SECONDS[PauseKind.CLAUSE] <= 0.18
    assert 0.20 <= PAUSE_SECONDS[PauseKind.SENTENCE] <= 0.35
    assert 0.35 <= PAUSE_SECONDS[PauseKind.REVEAL] <= 0.55


def test_cadence_is_not_uniform() -> None:
    """The whole point: identical gaps everywhere is what sounds robotic."""
    segments = segment_narration(
        "Hold on... Nobody sent it. Your phone worked it out. Done.",
        reveal_indexes=frozenset({1}),
    )
    kinds = {s.pause_kind for s in segments}
    assert len(kinds) >= 3


def test_empty_input_is_handled() -> None:
    assert segment_narration("   ") == []
    assert total_pause_seconds([]) == 0.0


def test_speaking_text_round_trips_for_alignment() -> None:
    text = "There's no message. No network call."
    assert speaking_text(segment_narration(text)) == text


def test_total_pause_is_the_sum() -> None:
    segments = segment_narration("A one. A two. A three.")
    assert total_pause_seconds(segments) == pytest.approx(
        sum(s.pause_seconds for s in segments)
    )


def test_single_sentence_has_no_trailing_pause() -> None:
    segments = segment_narration("Just one sentence.")
    assert len(segments) == 1
    assert segments[0].pause_seconds == 0.0
