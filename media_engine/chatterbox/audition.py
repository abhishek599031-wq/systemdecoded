"""Bounded Chatterbox-Turbo CUDA audition for the Phase 5.1 quality gate."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace


AUDITION_TEXT = (
    "A secure website shouldn’t store your password. It stores a verifier instead. "
    "Your password is hashed with a unique salt."
)
PROJECT_ID = "01a11afb-aa75-7306-b2a2-e5e7af87b988"
SCENE_ID = "01a11b7b-b750-70be-a7ec-00c5dcec0c33"
MODEL_ID = "ResembleAI/chatterbox-turbo"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / ".poc" / "chatterbox-cache",
    )
    return parser.parse_args()


def resident_bytes() -> int | None:
    try:
        import psutil

        return psutil.Process().memory_info().rss
    except (ImportError, OSError):
        return None


def install_windows_trim_workaround() -> None:
    """Avoid a blocked sklearn DLL imported by librosa.effects on this host.

    Chatterbox only needs ``effects.trim`` here.  The synthetic reference is
    already clean, but this retains conservative peak-relative silence
    trimming without importing librosa's unrelated decomposition/sklearn path.
    """
    import librosa
    import numpy as np

    def trim(waveform, *, top_db: float = 60, **_kwargs):
        samples = np.asarray(waveform)
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        if peak == 0.0:
            return samples, np.asarray([0, samples.size])
        threshold = peak * (10.0 ** (-top_db / 20.0))
        audible = np.flatnonzero(np.abs(samples) >= threshold)
        if audible.size == 0:
            return samples, np.asarray([0, samples.size])
        start, end = int(audible[0]), int(audible[-1]) + 1
        return samples[start:end], np.asarray([start, end])

    librosa.effects = SimpleNamespace(trim=trim)


def main() -> None:
    args = parse_args()
    if not args.reference.is_file():
        raise SystemExit(f"Safe reference audio does not exist: {args.reference}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(args.cache_dir.resolve()))

    import torch
    import torchaudio
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required for this POC, but torch.cuda.is_available() is false")

    ram_before = resident_bytes()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    load_started = time.perf_counter()
    model = ChatterboxTurboTTS.from_pretrained(device="cuda")
    load_seconds = time.perf_counter() - load_started
    ram_after_load = resident_bytes()
    install_windows_trim_workaround()

    # Turbo does not support the original Chatterbox exaggeration/CFG controls.
    # Keep this checkpoint to one neutral audition rather than manufacturing an
    # unsafe voice imitation or an arbitrary sampling-temperature comparison.
    generation_started = time.perf_counter()
    waveform = model.generate(AUDITION_TEXT, audio_prompt_path=str(args.reference))
    generation_seconds = time.perf_counter() - generation_started
    output = args.output_dir / "chatterbox-turbo-neutral.wav"
    torchaudio.save(str(output), waveform.detach().cpu(), int(model.sr))
    duration_seconds = waveform.shape[-1] / float(model.sr)
    peak_vram = int(torch.cuda.max_memory_allocated())
    ram_after_generation = resident_bytes()

    result = {
        "provider": "chatterbox-turbo",
        "package_version": importlib.metadata.version("chatterbox-tts"),
        "model": MODEL_ID,
        "device": "cuda",
        "gpu": torch.cuda.get_device_name(0),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "project_id": PROJECT_ID,
        "scene_id": SCENE_ID,
        "text": AUDITION_TEXT,
        "voice_reference": str(args.reference.resolve()),
        "reference_provenance": "project-owned synthetic Gemini narration for Video #2 scene 1",
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_rate": int(model.sr),
        "model_load_seconds": round(load_seconds, 3),
        "generation_seconds": round(generation_seconds, 3),
        "audio_duration_seconds": round(duration_seconds, 3),
        "real_time_factor": round(generation_seconds / duration_seconds, 4),
        "peak_vram_bytes": peak_vram,
        "process_rss_before_bytes": ram_before,
        "process_rss_after_load_bytes": ram_after_load,
        "process_rss_after_generation_bytes": ram_after_generation,
        "output_path": str(output.resolve()),
        "alternate_sample": None,
        "alternate_reason": (
            "Turbo ignores exaggeration and CFG; no alternate was generated because sampling "
            "temperature is not a reliable calm-technical style control."
        ),
        "runtime_workaround": (
            "Used local amplitude-based reference silence trimming because Windows App Control "
            "blocked sklearn._sag_fast imported indirectly by librosa.effects."
        ),
    }
    (args.output_dir / "benchmark.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
