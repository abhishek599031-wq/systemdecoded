"""Generate the one approved Video #2 Scene 2 proof narration."""

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
SCRIPT_ID = "01a11b7b-b741-76a0-ad30-ffe7096e0c2b"
SCENE_ID = "01a11b7b-b750-7785-957b-a741958b4e30"
NARRATION = (
    "A salted password hash. A unique salt makes the stored result different "
    "for each account."
)
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
    / "pocs"
    / "motion_canvas"
    / "video2_scene2"
    / "audio-driven"
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

    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    waveform = model.generate(NARRATION, audio_prompt_path=None, **SETTINGS)
    generation_seconds = time.perf_counter() - started
    duration_seconds = waveform.shape[-1] / float(model.sr)
    output_path = OUTPUT_DIR / "scene2-original-chatterbox.wav"
    torchaudio.save(str(output_path), waveform.detach().cpu(), int(model.sr))

    metadata = {
        "project_id": PROJECT_ID,
        "script_id": SCRIPT_ID,
        "scene_id": SCENE_ID,
        "scene_number": 2,
        "provider": "chatterbox",
        "model": "ResembleAI/chatterbox",
        "package_version": importlib.metadata.version("chatterbox-tts"),
        "device": "cuda",
        "gpu": torch.cuda.get_device_name(0),
        "narration": NARRATION,
        "word_count": len(NARRATION.split()),
        "audio_prompt_path": None,
        "settings": SETTINGS,
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_rate": int(model.sr),
        "model_load_seconds": round(load_seconds, 3),
        "generation_seconds": round(generation_seconds, 3),
        "audio_duration_seconds": round(duration_seconds, 3),
        "approx_wpm": round(len(NARRATION.split()) / duration_seconds * 60.0, 1),
        "peak_vram_bytes": int(torch.cuda.max_memory_allocated()),
        "process_rss_before_bytes": ram_before,
        "process_rss_after_load_bytes": ram_after_load,
        "process_rss_after_generation_bytes": resident_bytes(),
        "windows_workaround": workaround,
        "output_path": str(output_path.resolve()),
    }
    (OUTPUT_DIR / "tts-metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
