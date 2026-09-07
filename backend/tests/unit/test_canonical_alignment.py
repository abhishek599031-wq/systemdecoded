"""The approved script is the source of truth for caption text.

These tests exist because of a real defect. Gemini narrated the script
correctly; faster-whisper transcribed it with a looped phrase and an invented
"Not yet.", and the old aligner — which kept recognised words whenever token
counts disagreed — would have put both into the captions.

Every test here asserts the same underlying rule from a different angle:
recognition supplies timing, never text.
"""

from __future__ import annotations

import pytest

from app.providers.base import WordTiming
from app.services.canonical_alignment import (
    AlignmentConfidence,
    align_to_canonical,
    normalise_token,
)


def heard(*pairs: tuple[str, float, float]) -> list[WordTiming]:
    return [WordTiming(word=w, start=s, end=e) for w, s, e in pairs]


def evenly(text: str, start: float = 0.0, per_word: float = 0.3) -> list[WordTiming]:
    """ASR output that heard exactly the script, at a steady pace."""
    return [
        WordTiming(word=tok, start=start + i * per_word, end=start + (i + 1) * per_word)
        for i, tok in enumerate(text.split())
    ]


def caption_text(result) -> str:
    return " ".join(w.word for w in result.words)


# --------------------------------------------------------- case 1: repeats ---
def test_repeated_transcription_does_not_duplicate_captions():
    """ASR says the line twice. Captions say it once."""
    canonical = "Nobody sent it to you."
    asr = heard(
        ("Nobody", 0.0, 0.4), ("sent", 0.4, 0.7), ("it", 0.7, 0.9),
        ("to", 0.9, 1.1), ("you.", 1.1, 1.5),
        # The loop.
        ("Nobody", 1.5, 1.9), ("sent", 1.9, 2.2), ("it", 2.2, 2.4),
        ("to", 2.4, 2.6), ("you.", 2.6, 3.0),
    )

    result = align_to_canonical(canonical, asr, block_end=3.0)

    assert caption_text(result) == "Nobody sent it to you."
    assert len(result.words) == 5
    assert result.quality.discarded_asr == 5


def test_repeated_transcription_is_reported_not_hidden():
    canonical = "Your phone calculated it."
    asr = evenly("Your phone calculated it. Your phone calculated it.")

    result = align_to_canonical(canonical, asr, block_end=2.4)

    assert result.quality.repeated_ngrams > 0
    assert any("repeated" in w for w in result.quality.warnings)


# -------------------------------------------------- case 2: invented words ---
def test_invented_words_never_reach_captions():
    """The exact hallucination observed in the auditions."""
    canonical = "Your phone calculated it."
    asr = heard(
        ("Not", 0.0, 0.3), ("yet.", 0.3, 0.6),
        ("Your", 0.6, 0.9), ("phone", 0.9, 1.3),
        ("calculated", 1.3, 1.9), ("it.", 1.9, 2.2),
    )

    result = align_to_canonical(canonical, asr, block_end=2.2)

    joined = caption_text(result).lower()
    assert "not yet" not in joined
    assert caption_text(result) == "Your phone calculated it."
    # The real words kept their measured timings despite the prefix.
    assert result.words[0].start == pytest.approx(0.6)


def test_invented_words_do_not_shift_real_timings():
    canonical = "Same inputs. Same answer."
    asr = heard(
        ("Same", 1.0, 1.4), ("inputs.", 1.4, 2.0),
        ("Right.", 2.0, 2.3),  # invented
        ("Same", 2.3, 2.7), ("answer.", 2.7, 3.2),
    )

    result = align_to_canonical(canonical, asr, block_end=3.2)

    assert caption_text(result) == "Same inputs. Same answer."
    assert result.words[-1].end == pytest.approx(3.2)
    assert result.quality.discarded_asr == 1


# --------------------------------------------------- case 3: token mismatch ---
def test_missing_words_are_interpolated_not_dropped():
    """ASR misses a word. The word still appears, timed between its neighbours."""
    canonical = "There's no message. No network call."
    asr = heard(
        ("There's", 0.0, 0.4), ("no", 0.4, 0.6),
        # "message." not heard
        ("No", 1.2, 1.4), ("network", 1.4, 1.9), ("call.", 1.9, 2.4),
    )

    result = align_to_canonical(canonical, asr, block_end=2.4)

    assert caption_text(result) == canonical
    assert result.quality.interpolated == 1
    message = result.words[2]
    assert message.word == "message."
    assert 0.6 <= message.start < message.end <= 1.2


def test_severe_mismatch_flags_uncertainty_and_keeps_script():
    """When ASR is unusable, timings degrade — the text does not."""
    canonical = "When you set the app up, it and the server agreed on one shared secret."
    asr = evenly("completely different words that match nothing at all here")

    result = align_to_canonical(canonical, asr, block_end=4.0)

    assert caption_text(result) == canonical
    assert result.quality.confidence is AlignmentConfidence.LOW
    assert any("coverage" in w for w in result.quality.warnings)
    # Degraded, but still inside the real audio and still ordered.
    assert result.words[0].start >= 0.0
    assert result.words[-1].end <= 4.0


def test_empty_asr_still_produces_canonical_captions():
    canonical = "That's TOTP. Decoded."
    result = align_to_canonical(canonical, [], block_end=2.0)

    assert caption_text(result) == canonical
    assert result.quality.confidence is AlignmentConfidence.LOW
    assert result.words[-1].end <= 2.0


# ------------------------------------------------- case 4: non-monotonicity ---
def test_backwards_timestamps_are_corrected():
    canonical = "Same inputs. Same answer."
    asr = heard(
        ("Same", 0.0, 0.5), ("inputs.", 0.5, 1.0),
        ("Same", 0.2, 0.6),  # jumps backwards
        ("answer.", 1.2, 1.8),
    )

    result = align_to_canonical(canonical, asr, block_end=1.8)

    starts = [w.start for w in result.words]
    assert starts == sorted(starts)
    for a, b in zip(result.words, result.words[1:], strict=False):
        assert a.end <= b.start + 1e-6
    assert result.quality.monotonicity_fixes > 0


def test_every_word_has_positive_duration():
    canonical = "Nothing is transmitted."
    asr = heard(("Nothing", 0.5, 0.5), ("is", 0.5, 0.5), ("transmitted.", 0.5, 0.5))

    result = align_to_canonical(canonical, asr, block_end=2.0)

    assert all(w.end > w.start for w in result.words)


def test_timings_never_exceed_the_audio():
    canonical = "New window. New code."
    asr = evenly(canonical, per_word=5.0)  # ASR claims 20s of audio

    result = align_to_canonical(canonical, asr, block_end=2.5)

    assert all(w.end <= 2.5 for w in result.words)


# ------------------------------------------------------------- the happy path ---
def test_clean_transcription_takes_measured_timings():
    canonical = "Nobody sent it to you."
    asr = evenly(canonical)

    result = align_to_canonical(canonical, asr, block_end=1.5)

    assert caption_text(result) == canonical
    assert result.quality.confidence is AlignmentConfidence.HIGH
    assert result.quality.coverage == 1.0
    assert result.quality.interpolated == 0
    assert result.words[1].start == pytest.approx(0.3)


def test_script_punctuation_and_casing_survive():
    """Captions show what we wrote, including the em-dash and the question mark."""
    canonical = "That six-digit code in your authenticator app?"
    asr = heard(
        ("that", 0.0, 0.3), ("six", 0.3, 0.5), ("digit", 0.5, 0.8),
        ("code", 0.8, 1.1), ("in", 1.1, 1.2), ("your", 1.2, 1.4),
        ("authenticator", 1.4, 2.1), ("app", 2.1, 2.5),
    )

    result = align_to_canonical(canonical, asr, block_end=2.5)

    assert caption_text(result) == canonical
    assert result.words[-1].word.endswith("?")


def test_hyphenation_differences_still_match():
    """"six-digit" heard as two words must not count as a miss."""
    result = align_to_canonical(
        "six-digit code",
        heard(("six", 0.0, 0.3), ("digit", 0.3, 0.6), ("code", 0.6, 1.0)),
        block_end=1.0,
    )
    assert result.quality.canonical_words == 2
    assert result.words[0].word == "six-digit"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Code,", "code"), ("six-digit", "sixdigit"), ("TOTP.", "totp"),
     ("it's", "it's"), ("...", ""), ("418902", "418902")],
)
def test_normalise_token(raw: str, expected: str):
    assert normalise_token(raw) == expected


# ------------------------------------------------------ the whole first Short ---
def test_full_script_block_with_realistic_hallucination():
    """End to end on a real block, with a loop and an invention together."""
    canonical = "That six-digit code in your authenticator app? Nobody sent it to you."
    asr = (
        evenly("That six-digit code in your authenticator app?", start=0.0)
        + heard(("Hmm.", 2.4, 2.7))
        + evenly("Nobody sent it to you.", start=2.7)
        + evenly("Nobody sent it to you.", start=4.2)  # the loop
    )

    result = align_to_canonical(canonical, asr, block_end=5.7, label="scene_1")

    assert caption_text(result) == canonical
    assert "Hmm." not in caption_text(result)
    assert result.quality.discarded_asr >= 6
    starts = [w.start for w in result.words]
    assert starts == sorted(starts)
