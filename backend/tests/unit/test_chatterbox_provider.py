from __future__ import annotations

from app.config import Settings
from app.providers.tts.chatterbox import ChatterboxOriginalTTS, ChatterboxTurboTTS
from app.providers.tts.resolver import build_provider, voice_for


def test_original_chatterbox_is_available_without_becoming_default() -> None:
    configured = Settings()  # type: ignore[call-arg]

    assert isinstance(build_provider("chatterbox"), ChatterboxOriginalTTS)
    assert isinstance(build_provider("chatterbox-turbo"), ChatterboxTurboTTS)
    assert voice_for("chatterbox").voice == "builtin-default"
    assert configured.TTS_PROVIDER != "chatterbox"


def test_reviewed_original_chatterbox_controls_are_persisted() -> None:
    configured = Settings()  # type: ignore[call-arg]

    assert configured.CHATTERBOX_MODEL == "ResembleAI/chatterbox"
    assert configured.CHATTERBOX_EXAGGERATION == 0.5
    assert configured.CHATTERBOX_CFG_WEIGHT == 0.4
    assert configured.CHATTERBOX_TEMPERATURE == 0.8
    assert configured.CHATTERBOX_REPETITION_PENALTY == 1.2
    assert configured.CHATTERBOX_MIN_P == 0.05
    assert configured.CHATTERBOX_TOP_P == 1.0
