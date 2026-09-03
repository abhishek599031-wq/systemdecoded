"""Forced alignment via faster-whisper (LOCAL provider).

We know the words already; what we need is where they land in audio we
generated. Running recognition over our own clean TTS output and taking the
word timestamps is accurate enough for caption timing and costs nothing.

Why this matters: scene timings and captions are derived from measured audio,
never from word-count estimates. Guessing produces drift that compounds across
a 30-second video and is miserable to debug later (ARCH §14.1).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from app.config import settings
from app.core.errors import RetryableError
from app.core.logging import get_logger
from app.providers.base import AlignmentResult, WordTiming

log = get_logger("alignment.faster_whisper")


def _normalise(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", word.lower())


class FasterWhisperAligner:
    name = "faster-whisper"

    def __init__(self, model_size: str | None = None) -> None:
        self.model_size = model_size or settings.ALIGNMENT_MODEL
        self.model_dir = settings.MEDIA_ROOT / "cache" / "models" / "whisper"
        self._model = None
        self._lock = asyncio.Lock()

    async def _ensure_loaded(self):
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is not None:
                return self._model

            def _load():
                from faster_whisper import WhisperModel

                self.model_dir.mkdir(parents=True, exist_ok=True)
                return WhisperModel(
                    self.model_size,
                    device="cpu",
                    compute_type="int8",
                    download_root=str(self.model_dir),
                )

            log.info("alignment.model_loading", model=self.model_size)
            self._model = await asyncio.to_thread(_load)
            log.info("alignment.model_loaded", model=self.model_size)
        return self._model

    async def align(self, audio_path: Path, transcript: str) -> AlignmentResult:
        model = await self._ensure_loaded()

        def _run():
            segments, info = model.transcribe(
                str(audio_path),
                word_timestamps=True,
                language="en",
                # We wrote the script, so bias recognition toward it. This
                # markedly improves timing on technical terms the model would
                # otherwise mis-hear.
                initial_prompt=transcript[:900],
                vad_filter=False,
                beam_size=5,
            )
            words: list[WordTiming] = []
            for segment in segments:
                for w in segment.words or []:
                    token = w.word.strip()
                    if token:
                        words.append(WordTiming(word=token, start=float(w.start), end=float(w.end)))
            return words, float(info.duration)

        try:
            words, duration = await asyncio.to_thread(_run)
        except Exception as exc:
            raise RetryableError(f"Alignment failed: {exc}") from exc

        words = self._repair_against_transcript(words, transcript)
        log.info(
            "alignment.done",
            words=len(words),
            audio_duration=round(duration, 2),
            model=self.model_size,
        )
        return AlignmentResult(words=words, provider=self.name, audio_duration=duration)

    def _repair_against_transcript(
        self, words: list[WordTiming], transcript: str
    ) -> list[WordTiming]:
        """Restore the script's own spelling and punctuation.

        Recognition returns what it heard; captions should show what we wrote.
        When the token sequences line up we substitute the script's text while
        keeping the measured timings. If they diverge we keep the recognised
        words rather than forcing a bad mapping — wrong text is worse than
        slightly imperfect casing.
        """
        script_tokens = [t for t in transcript.split() if t.strip()]
        if len(script_tokens) != len(words):
            log.warning(
                "alignment.token_count_mismatch",
                script_words=len(script_tokens),
                heard_words=len(words),
            )
            return words

        repaired: list[WordTiming] = []
        for original, timing in zip(script_tokens, words, strict=True):
            if _normalise(original) and _normalise(original) != _normalise(timing.word):
                log.debug("alignment.word_differs", script=original, heard=timing.word)
            repaired.append(WordTiming(word=original, start=timing.start, end=timing.end))
        return repaired
