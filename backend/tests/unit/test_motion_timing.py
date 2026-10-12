from __future__ import annotations

import pytest

from app.core.errors import TerminalError
from app.providers.base import WordTiming
from app.providers.renderer.motion_timing import build_motion_timing_payload


def test_motion_cues_reuse_canonical_caption_timing() -> None:
    words = [
        WordTiming("A", 0.1, 0.2),
        WordTiming("unique", 0.2, 0.6),
        WordTiming("salt", 0.6, 0.9),
        WordTiming("changes", 1.0, 1.4),
        WordTiming("it.", 1.4, 1.7),
    ]
    payload = build_motion_timing_payload(
        words, 2.0, {"salt": "unique salt", "result": "changes it"}
    )

    assert payload["source"] == "canonical-caption-word-timing"
    assert payload["audio_duration_seconds"] == 2.0
    assert payload["events"] == [
        {"id": "salt", "phrase": "unique salt", "start": 0.2, "end": 0.9},
        {"id": "result", "phrase": "changes it", "start": 1.0, "end": 1.7},
    ]


def test_motion_cue_requires_a_canonical_anchor() -> None:
    words = [WordTiming("salt", 0.1, 0.4)]
    with pytest.raises(TerminalError, match="absent"):
        build_motion_timing_payload(words, 1.0, {"missing": "password"})


def test_motion_timing_rejects_words_outside_audio() -> None:
    words = [WordTiming("salt", 0.2, 1.4)]
    with pytest.raises(TerminalError, match="exceeds"):
        build_motion_timing_payload(words, 1.0, {"salt": "salt"})
