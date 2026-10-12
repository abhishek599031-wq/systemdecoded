"""Minimal reference-free pacing sweep for Original Chatterbox."""

from __future__ import annotations

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
WORD_COUNT = 20
OUTPUT_DIR = (
    Path(__file__).resolve().parents[2]
    / "media"
    / "pocs"
    / "chatterbox"
    / "original-pacing"
)
VARIANTS = (
    {"name": "neutral-cfg-0.3", "exaggeration": 0.5, "cfg_weight": 0.3},
    {"name": "neutral-cfg-0.4", "exaggeration": 0.5, "cfg_weight": 0.4},
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
    for variant in VARIANTS:
        torch.cuda.reset_peak_memory_stats()
        started = time.perf_counter()
        waveform = model.generate(
            AUDITION_TEXT,
            audio_prompt_path=None,
            exaggeration=variant["exaggeration"],
            cfg_weight=variant["cfg_weight"],
        )
        generation_seconds = time.perf_counter() - started
        duration_seconds = waveform.shape[-1] / float(model.sr)
        output_path = OUTPUT_DIR / f"{variant['name']}.wav"
        torchaudio.save(str(output_path), waveform.detach().cpu(), int(model.sr))
        results.append(
            {
                **variant,
                "model": "ResembleAI/chatterbox",
                "audio_prompt_path": None,
                "duration_seconds": round(duration_seconds, 3),
                "approx_wpm": round(WORD_COUNT / duration_seconds * 60.0, 1),
                "generation_seconds": round(generation_seconds, 3),
                "peak_vram_bytes": int(torch.cuda.max_memory_allocated()),
                "process_rss_after_generation_bytes": resident_bytes(),
                "output_path": str(output_path.resolve()),
            }
        )

    report = {
        "model": "ResembleAI/chatterbox",
        "package_version": importlib.metadata.version("chatterbox-tts"),
        "device": "cuda",
        "gpu": torch.cuda.get_device_name(0),
        "text": AUDITION_TEXT,
        "word_count": WORD_COUNT,
        "target_wpm": [175, 190],
        "model_load_seconds": round(load_seconds, 3),
        "process_rss_before_bytes": ram_before,
        "process_rss_after_load_bytes": ram_after_load,
        "workaround": workaround,
        "generated_at": datetime.now(UTC).isoformat(),
        "variants": results,
    }
    (OUTPUT_DIR / "pacing-comparison.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
