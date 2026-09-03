"""Kokoro TTS (LOCAL provider).

Kokoro-82M is Apache-2.0, runs at or above real time on CPU, and is close
enough to paid TTS for short-form narration that TTS is simply not a
compromise in this stack (ARCH §3.6).

Model weights are downloaded once into MEDIA_ROOT/cache/models rather than
baked into the image, so the image stays lean and swapping models needs no
rebuild.

Synthesis is CPU-bound and blocking, so it runs in a worker thread — a blocking
call on the shared event loop would stall the worker's heartbeat and get the
job reaped mid-render.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from app.config import settings
from app.core.errors import RetryableError, TerminalError
from app.core.logging import get_logger
from app.providers.base import AudioResult, VoiceSpec

log = get_logger("tts.kokoro")

MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

# Curated for the SystemDecoded identity: clear, confident, conversational,
# globally intelligible. Deliberately not the breathier or theatrical voices —
# this is technology media, not an advertisement.
CANDIDATE_VOICES = ("af_heart", "af_bella", "am_michael", "bm_george", "af_nicole")


class KokoroTTS:
    name = "kokoro-onnx"

    def __init__(self, model_dir: Path | None = None) -> None:
        self.model_dir = model_dir or (settings.MEDIA_ROOT / "cache" / "models")
        self._kokoro = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- model io ---
    async def _download(self, url: str, dest: Path) -> None:
        if dest.exists() and dest.stat().st_size > 0:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        log.info("tts.model_download.start", file=dest.name)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
                async with client.stream("GET", url, follow_redirects=True) as response:
                    response.raise_for_status()
                    with tmp.open("wb") as fh:
                        async for chunk in response.aiter_bytes(1 << 20):
                            fh.write(chunk)
        except httpx.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise RetryableError(f"Could not download {dest.name}: {exc}") from exc
        tmp.replace(dest)
        log.info("tts.model_download.done", file=dest.name, bytes=dest.stat().st_size)

    async def _ensure_loaded(self):
        if self._kokoro is not None:
            return self._kokoro
        async with self._lock:
            if self._kokoro is not None:
                return self._kokoro

            model_path = self.model_dir / "kokoro-v1.0.onnx"
            voices_path = self.model_dir / "voices-v1.0.bin"
            await self._download(MODEL_URL, model_path)
            await self._download(VOICES_URL, voices_path)

            def _load():
                from kokoro_onnx import Kokoro

                return Kokoro(str(model_path), str(voices_path))

            self._kokoro = await asyncio.to_thread(_load)
            log.info("tts.model_loaded", model=model_path.name)
        return self._kokoro

    # ------------------------------------------------------------ synthesis ---
    async def list_voices(self) -> list[str]:
        kokoro = await self._ensure_loaded()
        try:
            return sorted(kokoro.get_voices())
        except Exception:  # noqa: BLE001 - the API varies across versions
            return list(CANDIDATE_VOICES)

    async def synthesize(self, text: str, voice: VoiceSpec, out_path: Path) -> AudioResult:
        if not text or not text.strip():
            raise TerminalError("Cannot synthesize empty narration")

        kokoro = await self._ensure_loaded()
        out_path.parent.mkdir(parents=True, exist_ok=True)

        def _run():
            import soundfile as sf

            samples, sample_rate = kokoro.create(
                text, voice=voice.voice, speed=voice.speed, lang=voice.lang
            )
            sf.write(str(out_path), samples, sample_rate)
            return len(samples) / float(sample_rate), sample_rate

        try:
            duration, sample_rate = await asyncio.to_thread(_run)
        except Exception as exc:
            # A bad voice name is a config error; anything else may be transient.
            if "voice" in str(exc).lower():
                raise TerminalError(f"Unknown Kokoro voice {voice.voice!r}: {exc}") from exc
            raise RetryableError(f"Kokoro synthesis failed: {exc}") from exc

        log.info(
            "tts.synthesized",
            voice=voice.voice,
            speed=voice.speed,
            duration_seconds=round(duration, 2),
            chars=len(text),
        )
        return AudioResult(
            path=out_path,
            duration_seconds=duration,
            sample_rate=sample_rate,
            provider=self.name,
            voice=voice.voice,
        )
