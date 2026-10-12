"""Experimental local Chatterbox-Turbo provider.

Kept behind the existing TTSProvider interface and loaded lazily.  The model is
cached once per worker process and generation is serialised because concurrent
8 GB GPU jobs are deliberately outside this POC's scope.
"""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.metadata
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import ClassVar

from app.config import settings
from app.core.errors import RetryableError, TerminalError
from app.core.logging import get_logger
from app.providers.base import AudioResult, VoiceSpec

log = get_logger("tts.chatterbox")


def _install_windows_sklearn_import_shim() -> str | None:
    """Avoid a blocked, unused sklearn binary dependency on this Windows host."""
    if os.name != "nt":
        return None
    sklearn = ModuleType("sklearn")
    sklearn.__path__ = []  # type: ignore[attr-defined]
    sklearn.__spec__ = importlib.machinery.ModuleSpec(
        "sklearn", loader=None, is_package=True
    )
    metrics = ModuleType("sklearn.metrics")
    metrics.__spec__ = importlib.machinery.ModuleSpec("sklearn.metrics", loader=None)
    metrics.roc_curve = lambda *args, **kwargs: None  # type: ignore[attr-defined]
    sklearn.metrics = metrics  # type: ignore[attr-defined]
    sys.modules["sklearn"] = sklearn
    sys.modules["sklearn.metrics"] = metrics
    return "unused sklearn.metrics.roc_curve shimmed because Windows App Control blocks its DLL"


class ChatterboxOriginalTTS:
    """Reference-free Original Chatterbox with the reviewed pacing controls."""

    name = "chatterbox"
    prefers_whole_block = True

    _models: ClassVar[dict[tuple[str, str], object]] = {}
    _load_seconds: ClassVar[dict[tuple[str, str], float]] = {}
    _load_lock: ClassVar[asyncio.Lock | None] = None
    _generation_lock: ClassVar[asyncio.Lock | None] = None

    def __init__(self, *, device: str | None = None, model: str | None = None) -> None:
        self.device = device or settings.CHATTERBOX_DEVICE
        self.model = model or settings.CHATTERBOX_MODEL

    @classmethod
    def _locks(cls) -> tuple[asyncio.Lock, asyncio.Lock]:
        cls._load_lock = cls._load_lock or asyncio.Lock()
        cls._generation_lock = cls._generation_lock or asyncio.Lock()
        return cls._load_lock, cls._generation_lock

    async def _ensure_loaded(self):
        key = (self.device, self.model)
        if key in self._models:
            return self._models[key]
        load_lock, _ = self._locks()
        async with load_lock:
            if key in self._models:
                return self._models[key]

            def _load():
                workaround = _install_windows_sklearn_import_shim()
                import torch
                from chatterbox.tts import ChatterboxTTS as Model

                if self.device == "cuda" and not torch.cuda.is_available():
                    raise TerminalError(
                        "Chatterbox requested CUDA, but torch.cuda.is_available() is false"
                    )
                started = time.perf_counter()
                loaded = Model.from_pretrained(device=self.device)
                return loaded, time.perf_counter() - started, workaround

            try:
                loaded, seconds, workaround = await asyncio.to_thread(_load)
            except TerminalError:
                raise
            except (ImportError, ModuleNotFoundError) as exc:
                raise TerminalError(
                    "Original Chatterbox is not installed. Install chatterbox-tts."
                ) from exc
            except Exception as exc:
                raise RetryableError(f"Original Chatterbox model load failed: {exc}") from exc
            self._models[key] = loaded
            self._load_seconds[key] = seconds
            if workaround:
                log.warning("tts.windows_import_workaround", provider=self.name, detail=workaround)
            log.info(
                "tts.model_loaded", provider=self.name, model=self.model,
                device=self.device, load_seconds=round(seconds, 3)
            )
            return loaded

    async def list_voices(self) -> list[str]:
        return ["builtin-default"]

    async def synthesize(self, text: str, voice: VoiceSpec, out_path: Path) -> AudioResult:
        if not text.strip():
            raise TerminalError("Cannot synthesize empty narration")
        model = await self._ensure_loaded()
        _, generation_lock = self._locks()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        controls = {
            "exaggeration": settings.CHATTERBOX_EXAGGERATION,
            "cfg_weight": settings.CHATTERBOX_CFG_WEIGHT,
            "temperature": settings.CHATTERBOX_TEMPERATURE,
            "repetition_penalty": settings.CHATTERBOX_REPETITION_PENALTY,
            "min_p": settings.CHATTERBOX_MIN_P,
            "top_p": settings.CHATTERBOX_TOP_P,
        }

        def _generate():
            import torch
            import torchaudio

            if self.device == "cuda":
                torch.cuda.reset_peak_memory_stats()
            started = time.perf_counter()
            wav = model.generate(text, audio_prompt_path=None, **controls)
            generation_seconds = time.perf_counter() - started
            sample_rate = int(model.sr)
            audio = wav.detach().cpu()
            torchaudio.save(str(out_path), audio, sample_rate)
            duration = audio.shape[-1] / float(sample_rate)
            peak_vram = (
                int(torch.cuda.max_memory_allocated()) if self.device == "cuda" else None
            )
            return generation_seconds, duration, sample_rate, peak_vram

        async with generation_lock:
            try:
                generation_seconds, duration, sample_rate, peak_vram = (
                    await asyncio.to_thread(_generate)
                )
            except Exception as exc:
                raise RetryableError(f"Original Chatterbox synthesis failed: {exc}") from exc

        key = (self.device, self.model)
        return AudioResult(
            path=out_path,
            duration_seconds=duration,
            sample_rate=sample_rate,
            provider=self.name,
            model=self.model,
            voice="builtin-default",
            metadata={
                "package_version": importlib.metadata.version("chatterbox-tts"),
                "generated_at": datetime.now(UTC).isoformat(),
                "device": self.device,
                "audio_prompt_path": None,
                "controls": controls,
                "model_load_seconds": round(self._load_seconds[key], 3),
                "generation_seconds": round(generation_seconds, 3),
                "real_time_factor": round(generation_seconds / duration, 4),
                "peak_vram_bytes": peak_vram,
            },
        )


class ChatterboxTurboTTS:
    name = "chatterbox-turbo"
    prefers_whole_block = True

    _models: ClassVar[dict[str, object]] = {}
    _load_seconds: ClassVar[dict[str, float]] = {}
    _load_lock: ClassVar[asyncio.Lock | None] = None
    _generation_lock: ClassVar[asyncio.Lock | None] = None

    def __init__(self, *, device: str | None = None, model: str | None = None) -> None:
        self.device = device or settings.CHATTERBOX_DEVICE
        self.model = model or settings.CHATTERBOX_TURBO_MODEL

    @classmethod
    def _locks(cls) -> tuple[asyncio.Lock, asyncio.Lock]:
        # Locks are created lazily inside the active event loop.  Module-level
        # asyncio locks can bind to the wrong loop in test/worker processes.
        cls._load_lock = cls._load_lock or asyncio.Lock()
        cls._generation_lock = cls._generation_lock or asyncio.Lock()
        return cls._load_lock, cls._generation_lock

    async def _ensure_loaded(self):
        if self.device in self._models:
            return self._models[self.device]
        load_lock, _ = self._locks()
        async with load_lock:
            if self.device in self._models:
                return self._models[self.device]

            def _load():
                import torch
                from chatterbox.tts_turbo import ChatterboxTurboTTS as Model

                if self.device == "cuda" and not torch.cuda.is_available():
                    raise TerminalError(
                        "Chatterbox requested CUDA, but torch.cuda.is_available() is false"
                    )
                started = time.perf_counter()
                loaded = Model.from_pretrained(device=self.device)
                return loaded, time.perf_counter() - started

            try:
                loaded, seconds = await asyncio.to_thread(_load)
            except TerminalError:
                raise
            except (ImportError, ModuleNotFoundError) as exc:
                raise TerminalError(
                    "Chatterbox is not installed. Install the optional chatterbox dependency."
                ) from exc
            except Exception as exc:
                raise RetryableError(f"Chatterbox model load failed: {exc}") from exc
            self._models[self.device] = loaded
            self._load_seconds[self.device] = seconds
            log.info("tts.model_loaded", provider=self.name, model=self.model,
                     device=self.device, load_seconds=round(seconds, 3))
            return loaded

    async def list_voices(self) -> list[str]:
        # Turbo conditions on reference audio rather than a named voice bank.
        reference = settings.CHATTERBOX_REFERENCE_AUDIO
        return [reference] if reference else []

    async def synthesize(self, text: str, voice: VoiceSpec, out_path: Path) -> AudioResult:
        if not text.strip():
            raise TerminalError("Cannot synthesize empty narration")
        reference = Path(voice.voice or settings.CHATTERBOX_REFERENCE_AUDIO)
        if not reference.exists():
            raise TerminalError(
                "Chatterbox-Turbo requires safe reference audio; configured file does not exist: "
                f"{reference}"
            )
        model = await self._ensure_loaded()
        _, generation_lock = self._locks()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        def _generate():
            import torch
            import torchaudio

            if self.device == "cuda":
                torch.cuda.reset_peak_memory_stats()
            started = time.perf_counter()
            wav = model.generate(text, audio_prompt_path=str(reference))
            seconds = time.perf_counter() - started
            sample_rate = int(model.sr)
            audio = wav.detach().cpu()
            torchaudio.save(str(out_path), audio, sample_rate)
            duration = audio.shape[-1] / float(sample_rate)
            peak_vram = (
                int(torch.cuda.max_memory_allocated()) if self.device == "cuda" else None
            )
            return seconds, duration, sample_rate, peak_vram

        async with generation_lock:
            try:
                generation_seconds, duration, sample_rate, peak_vram = await asyncio.to_thread(
                    _generate
                )
            except Exception as exc:
                raise RetryableError(f"Chatterbox synthesis failed: {exc}") from exc

        package_version = importlib.metadata.version("chatterbox-tts")
        return AudioResult(
            path=out_path,
            duration_seconds=duration,
            sample_rate=sample_rate,
            provider=self.name,
            model=self.model,
            voice=str(reference),
            metadata={
                "package_version": package_version,
                "generated_at": datetime.now(UTC).isoformat(),
                "device": self.device,
                "reference_audio": str(reference),
                "model_load_seconds": round(self._load_seconds[self.device], 3),
                "generation_seconds": round(generation_seconds, 3),
                "real_time_factor": round(generation_seconds / duration, 4),
                "peak_vram_bytes": peak_vram,
            },
        )
