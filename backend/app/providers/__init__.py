"""Swappable provider implementations behind the interfaces in `base`.

Phase 2 binds every capability to a LOCAL implementation. EXTERNAL_API
variants drop in later without the pipeline changing (ARCH §5.3).
"""

from app.providers.base import (
    AlignmentProvider,
    AlignmentResult,
    AudioResult,
    CompositionScene,
    CompositionSpec,
    LLMProvider,
    LLMResult,
    SceneRenderer,
    SceneRenderResult,
    SceneRenderSpec,
    StructuredLLMResult,
    TTSProvider,
    VideoCompositor,
    VideoResult,
    VoiceSpec,
    WordTiming,
)

__all__ = [
    "AlignmentProvider",
    "AlignmentResult",
    "AudioResult",
    "CompositionScene",
    "CompositionSpec",
    "LLMProvider",
    "LLMResult",
    "SceneRenderResult",
    "SceneRenderSpec",
    "SceneRenderer",
    "StructuredLLMResult",
    "TTSProvider",
    "VideoCompositor",
    "VideoResult",
    "VoiceSpec",
    "WordTiming",
]
