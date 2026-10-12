"""Generate all approved Video #2 blocks with one Original Chatterbox load."""

from __future__ import annotations

import importlib.machinery
import importlib.metadata
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType


PROJECT_ID = "01a11afb-aa75-7306-b2a2-e5e7af87b988"
SCRIPT_ID = "01a12eae-17b5-7d77-b0b9-1a6002a9af06"
SCENES = [
    "Good websites don’t store your password as readable text. They store a verifier.",
    (
        "First, your password is combined with a unique random salt, then passed through "
        "a slow password-hashing algorithm."
    ),
    (
        "Even if two people choose the same password, different salts produce different "
        "stored results. When you log in, the site repeats that process and compares the "
        "new result with the stored verifier."
    ),
    (
        "If attackers steal the database, they still can’t reverse the hash—but they can "
        "guess passwords offline. That’s why algorithms like Argon2id make every guess "
        "deliberately expensive."
    ),
    "The password itself never needs to sit in the database waiting to be stolen.",
]
SETTINGS = {
    "exaggeration": 0.5,
    "cfg_weight": 0.4,
    "temperature": 0.8,
    "repetition_penalty": 1.2,
    "min_p": 0.05,
    "top_p": 1.0,
}
OUTPUT_DIR = (
    Path(__file__).resolve().parents[2]
    / "media"
    / "renders"
    / PROJECT_ID
    / SCRIPT_ID
    / "audio"
)


def install_windows_sklearn_import_shim() -> str:
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
    return "Windows App Control sklearn DLL block; unused roc_curve shimmed in-process"


def resident_bytes() -> int | None:
    try:
        import psutil

        return psutil.Process().memory_info().rss
    except (ImportError, OSError):
        return None


def main() -> None:
    workaround = install_windows_sklearn_import_shim()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    import torch
    import torchaudio
    from chatterbox.tts import ChatterboxTTS

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required, but torch.cuda.is_available() is false")

    ram_before = resident_bytes()
    load_started = time.perf_counter()
    model = ChatterboxTTS.from_pretrained(device="cuda")
    load_seconds = time.perf_counter() - load_started
    ram_after_load = resident_bytes()
    results = []

    for number, narration in enumerate(SCENES, 1):
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        waveform = model.generate(narration, audio_prompt_path=None, **SETTINGS)
        generation_seconds = time.perf_counter() - started
        duration = waveform.shape[-1] / float(model.sr)
        output = OUTPUT_DIR / f"scene-{number:02d}.wav"
        torchaudio.save(str(output), waveform.detach().cpu(), int(model.sr))
        results.append(
            {
                "scene_number": number,
                "narration": narration,
                "word_count": len(narration.split()),
                "duration_seconds": round(duration, 3),
                "approx_wpm": round(len(narration.split()) / duration * 60.0, 1),
                "generation_seconds": round(generation_seconds, 3),
                "peak_vram_bytes": int(torch.cuda.max_memory_allocated()),
                "process_rss_bytes": resident_bytes(),
                "output_path": str(output.resolve()),
            }
        )

    metadata = {
        "project_id": PROJECT_ID,
        "script_id": SCRIPT_ID,
        "provider": "chatterbox",
        "model": "ResembleAI/chatterbox",
        "package_version": importlib.metadata.version("chatterbox-tts"),
        "device": "cuda",
        "gpu": torch.cuda.get_device_name(0),
        "audio_prompt_path": None,
        "settings": SETTINGS,
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_rate": int(model.sr),
        "model_load_seconds": round(load_seconds, 3),
        "process_rss_before_bytes": ram_before,
        "process_rss_after_load_bytes": ram_after_load,
        "process_rss_final_bytes": resident_bytes(),
        "peak_vram_bytes": max(result["peak_vram_bytes"] for result in results),
        "total_generation_seconds": round(
            sum(result["generation_seconds"] for result in results), 3
        ),
        "total_speech_duration_seconds": round(
            sum(result["duration_seconds"] for result in results), 3
        ),
        "word_count": sum(result["word_count"] for result in results),
        "windows_workaround": workaround,
        "scenes": results,
    }
    (OUTPUT_DIR / "generation-metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
