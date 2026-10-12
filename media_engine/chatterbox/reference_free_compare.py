"""Generate exactly one reference-free audition for each Chatterbox model."""

from __future__ import annotations

import gc
import importlib.machinery
import importlib.metadata
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType


AUDITION_TEXT = (
    "A secure website shouldn’t store your password. It stores a verifier instead. "
    "Your password is hashed with a unique salt."
)
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "media" / "pocs" / "chatterbox" / "video2-reference-free"


def install_windows_sklearn_import_shim() -> str:
    """Avoid the blocked optional sklearn DLL imported by Transformers.

    Transformers only imports ``roc_curve`` for an optional generation helper;
    Chatterbox inference does not call it.  The host's Application Control
    policy blocks sklearn's compiled DLL, so provide that unused symbol in
    process rather than changing the installed environment or using audio
    conditioning.
    """
    sklearn = ModuleType("sklearn")
    sklearn.__path__ = []
    sklearn.__spec__ = importlib.machinery.ModuleSpec(
        "sklearn", loader=None, is_package=True
    )
    metrics = ModuleType("sklearn.metrics")
    metrics.__spec__ = importlib.machinery.ModuleSpec("sklearn.metrics", loader=None)
    metrics.roc_curve = lambda *args, **kwargs: None
    sklearn.metrics = metrics
    sys.modules["sklearn"] = sklearn
    sys.modules["sklearn.metrics"] = metrics
    return "Windows App Control blocked sklearn compiled DLL; unused sklearn.metrics.roc_curve was shimmed in-process."


def resident_bytes() -> int | None:
    try:
        import psutil

        return psutil.Process().memory_info().rss
    except (ImportError, OSError):
        return None


def generate_one(model_name: str, model_factory, output_name: str, torch, torchaudio) -> dict:
    ram_before = resident_bytes()
    torch.cuda.empty_cache()
    load_started = time.perf_counter()
    model = model_factory.from_pretrained(device="cuda")
    load_seconds = time.perf_counter() - load_started
    ram_after_load = resident_bytes()

    torch.cuda.reset_peak_memory_stats()
    generation_started = time.perf_counter()
    # Explicit None is intentional: built-in/default conditioning only.
    waveform = model.generate(AUDITION_TEXT, audio_prompt_path=None)
    generation_seconds = time.perf_counter() - generation_started

    output_path = OUTPUT_DIR / output_name
    torchaudio.save(str(output_path), waveform.detach().cpu(), int(model.sr))
    duration_seconds = waveform.shape[-1] / float(model.sr)
    peak_vram = int(torch.cuda.max_memory_allocated())
    ram_after_generation = resident_bytes()
    package_version = importlib.metadata.version("chatterbox-tts")
    result = {
        "provider": "chatterbox",
        "model": model_name,
        "package_version": package_version,
        "device": "cuda",
        "gpu": torch.cuda.get_device_name(0),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "text": AUDITION_TEXT,
        "audio_prompt_path": None,
        "conditioning": "built-in/default; no audio reference",
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
        "output_path": str(output_path.resolve()),
        "windows_librosa_sklearn_workaround": WORKAROUND,
    }

    del waveform, model
    gc.collect()
    torch.cuda.empty_cache()
    return result


def main() -> None:
    global WORKAROUND
    WORKAROUND = install_windows_sklearn_import_shim()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    import torch
    import torchaudio
    from chatterbox.tts import ChatterboxTTS
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required, but torch.cuda.is_available() is false")

    results = [
        generate_one(
            "ResembleAI/chatterbox-turbo",
            ChatterboxTurboTTS,
            "chatterbox-turbo-reference-free.wav",
            torch,
            torchaudio,
        ),
        generate_one(
            "ResembleAI/chatterbox",
            ChatterboxTTS,
            "chatterbox-original-reference-free.wav",
            torch,
            torchaudio,
        ),
    ]
    report = {
        "text": AUDITION_TEXT,
        "audio_prompt_path": None,
        "samples": results,
        "sample_count": len(results),
    }
    (OUTPUT_DIR / "comparison.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


WORKAROUND = ""

if __name__ == "__main__":
    main()
