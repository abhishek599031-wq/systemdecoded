"""Forced alignment via faster-whisper (LOCAL provider).

We know the words already; what we need is where they land in audio we
generated. Running recognition over our own clean TTS output and taking the
word timestamps is accurate enough for caption timing and costs nothing.

Why this matters: scene timings and captions are derived from measured audio,
never from word-count estimates. Guessing produces drift that compounds across
a 30-second video and is miserable to debug later (ARCH §14.1).

**This provider returns evidence, not truth.** It reports the words it heard
and when it heard them. It does not decide what the narration says — that is
the approved script's job, and `app.services.canonical_alignment` is what maps
one onto the other. An earlier version of this file substituted script text
only when token counts matched and otherwise kept what it heard; on Gemini
audio, where the recogniser sometimes loops a phrase, that quietly promoted a
hallucination into the captions. Nothing here decides caption text any more.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import settings
from app.core.errors import RetryableError
from app.core.logging import get_logger
from app.providers.base import AlignmentResult, WordTiming

log = get_logger("alignment.faster_whisper")


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
                # --- anti-hallucination decoding -------------------------------
                # Whisper carries its own previous output forward as context. On
                # a short, cleanly-articulated TTS clip that feedback is what
                # makes it repeat a phrase it already emitted: having just said
                # "That six-digit code in your authenticator app", the primed
                # decoder finds saying it again more probable than stopping.
                # Cutting the loop is the single most effective change here.
                condition_on_previous_text=False,
                # Deterministic decoding. The temperature ladder exists to escape
                # bad beams on noisy speech; on our own studio-clean audio it
                # only adds variance between otherwise identical renders.
                temperature=0.0,
                # A segment whose average token probability is this low is the
                # model guessing. Dropping it is right for alignment: a missing
                # anchor gets interpolated, whereas a confident-looking invention
                # would drag real words to wrong timestamps.
                log_prob_threshold=-1.0,
                # Trailing silence and breath tails are where invented text
                # appears. This suppresses runs that are almost certainly silence.
                no_speech_threshold=0.6,
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

        log.info(
            "alignment.heard",
            words=len(words),
            script_words=len(transcript.split()),
            audio_duration=round(duration, 2),
            model=self.model_size,
        )
        return AlignmentResult(words=words, provider=self.name, audio_duration=duration)
