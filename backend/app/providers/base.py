"""Provider interfaces (ARCH §5.3).

Every external capability sits behind one of these so the implementation can be
swapped without touching the pipeline. Phase 2 binds:

    TTS        -> Kokoro (LOCAL)
    Alignment  -> faster-whisper (LOCAL)
    Renderer   -> Playwright stills (LOCAL)
    Compositor -> FFmpeg (LOCAL)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class VoiceSpec:
    """How narration should sound. Configurable so voice becomes testable."""

    voice: str = "af_heart"
    speed: float = 1.0
    lang: str = "en-us"


@dataclass(frozen=True, slots=True)
class AudioResult:
    path: Path
    duration_seconds: float
    sample_rate: int
    provider: str
    voice: str


@dataclass(frozen=True, slots=True)
class WordTiming:
    """One word with its measured position in the audio."""

    word: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    words: list[WordTiming]
    provider: str
    audio_duration: float


@dataclass(frozen=True, slots=True)
class SceneRenderSpec:
    """What to render for one scene."""

    scene_number: int
    template_id: str
    props: dict[str, Any]
    keyframes: tuple[float, ...] = (0.0, 1.0)
    width: int = 1080
    height: int = 1920


@dataclass(frozen=True, slots=True)
class SceneRenderResult:
    scene_number: int
    frames: list[Path]
    keyframes: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class CompositionScene:
    """A scene ready to composite: measured timing plus rendered frames."""

    scene_number: int
    start: float
    end: float
    frames: list[Path]
    transition_in: str | None = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True, slots=True)
class CompositionSpec:
    scenes: list[CompositionScene]
    narration_audio: Path
    output_path: Path
    subtitle_path: Path | None = None
    music_path: Path | None = None
    width: int = 1080
    height: int = 1920
    fps: int = 30
    target_lufs: float = -14.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VideoResult:
    path: Path
    width: int
    height: int
    fps: int
    duration_seconds: float
    bytes: int
    loudness_lufs: float | None = None
    peak_dbfs: float | None = None


# ------------------------------------------------------------------ protocols ---
class TTSProvider(Protocol):
    name: str

    async def synthesize(self, text: str, voice: VoiceSpec, out_path: Path) -> AudioResult: ...

    async def list_voices(self) -> list[str]: ...


class AlignmentProvider(Protocol):
    """Named `align`, not `transcribe`, on purpose.

    We already know the words — we wrote them. What is needed is their position
    in audio we generated, i.e. forced alignment (ARCH §5.3).
    """

    name: str

    async def align(self, audio_path: Path, transcript: str) -> AlignmentResult: ...


class SceneRenderer(Protocol):
    name: str

    async def render(self, spec: SceneRenderSpec, out_dir: Path) -> SceneRenderResult: ...


class VideoCompositor(Protocol):
    name: str

    async def compose(self, spec: CompositionSpec) -> VideoResult: ...
