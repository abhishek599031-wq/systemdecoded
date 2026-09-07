"""TTS providers.

`get_narration_provider()` is the entry point the pipeline uses; it returns a
provider that already handles retry and fallback, so callers never branch on
which backend is configured.
"""

from app.providers.tts.direction import (
    SYSTEMDECODED_DIRECTION,
    SYSTEMDECODED_NARRATION,
    NarrationProfile,
    VoiceDirection,
    build_prompt,
)
from app.providers.tts.gemini import PREBUILT_VOICES, GeminiTTS
from app.providers.tts.kokoro import KokoroTTS
from app.providers.tts.resolver import (
    ResilientTTS,
    build_provider,
    get_narration_provider,
    voice_for,
)

__all__ = [
    "PREBUILT_VOICES",
    "SYSTEMDECODED_DIRECTION",
    "SYSTEMDECODED_NARRATION",
    "GeminiTTS",
    "KokoroTTS",
    "NarrationProfile",
    "ResilientTTS",
    "VoiceDirection",
    "build_prompt",
    "build_provider",
    "get_narration_provider",
    "voice_for",
]
