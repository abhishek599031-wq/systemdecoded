"""The narration profile is the channel's voice, and config must not drift from it."""

from __future__ import annotations

from app.config import Settings
from app.core.narration import SYSTEMDECODED_DIRECTION, SYSTEMDECODED_NARRATION
from app.providers.tts.gemini import PREBUILT_VOICES, GeminiTTS
from app.providers.tts.kokoro import KokoroTTS


def test_selected_voice_is_algieba():
    """Chosen by listening to five auditions of the same script."""
    assert SYSTEMDECODED_NARRATION.voice == "Algieba"
    assert SYSTEMDECODED_NARRATION.provider == "gemini"
    assert SYSTEMDECODED_NARRATION.model == "gemini-3.1-flash-tts-preview"
    assert SYSTEMDECODED_NARRATION.fallback_provider == "kokoro"


def test_selected_voice_exists_on_the_provider():
    """A typo here would be a 400 from the API at render time, not before it."""
    assert SYSTEMDECODED_NARRATION.voice in PREBUILT_VOICES


def test_config_defaults_come_from_the_profile():
    """One decision, not four settings drifting apart."""
    defaults = Settings.model_fields
    assert defaults["TTS_PROVIDER"].default == SYSTEMDECODED_NARRATION.provider
    assert defaults["GEMINI_TTS_MODEL"].default == SYSTEMDECODED_NARRATION.model
    assert defaults["GEMINI_TTS_VOICE"].default == SYSTEMDECODED_NARRATION.voice
    assert defaults["TTS_FALLBACK_PROVIDER"].default == SYSTEMDECODED_NARRATION.fallback_provider


def test_profile_carries_the_performance_direction():
    assert SYSTEMDECODED_NARRATION.direction is SYSTEMDECODED_DIRECTION


def test_profile_metadata_is_reproducible_and_carries_no_secret():
    meta = SYSTEMDECODED_NARRATION.as_metadata()
    assert meta["voice"] == "Algieba"
    assert meta["direction"] == SYSTEMDECODED_DIRECTION.fingerprint
    assert set(meta) == {"narration_profile", "provider", "model", "voice", "direction"}


def test_direction_fingerprint_is_stable_and_short():
    first = SYSTEMDECODED_DIRECTION.fingerprint
    assert first == SYSTEMDECODED_DIRECTION.fingerprint
    assert len(first) == 12


def test_direction_still_expresses_the_intended_personality():
    """Guards against the prompt being casually rewritten."""
    instruction = SYSTEMDECODED_DIRECTION.as_instruction().lower()
    for phrase in ("technology storyteller", "conversational", "confident", "subtle emphasis"):
        assert phrase in instruction
    for phrase in ("announcer", "robotic", "exaggerated"):
        assert phrase in instruction  # named in Avoid


# ------------------------------------------------------- block capability ---
def test_gemini_takes_whole_blocks():
    """It is directed to vary its own pacing, so give it a whole beat."""
    assert GeminiTTS().prefers_whole_block is True


def test_kokoro_needs_the_text_split():
    """Kokoro reads every sentence identically; the pipeline shapes its pauses."""
    assert KokoroTTS().prefers_whole_block is False
