"""Align the authorized Scene 2 proof and export captions plus Motion cues."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import soundfile as sf

from app.providers.alignment.faster_whisper import FasterWhisperAligner
from app.providers.compositor.captions import build_ass
from app.providers.renderer.motion_timing import build_motion_timing_payload
from app.services.canonical_alignment import align_to_canonical

NARRATION = (
    "A salted password hash. A unique salt makes the stored result different "
    "for each account."
)
ROOT = Path("/media/pocs/motion_canvas/video2_scene2/audio-driven")
AUDIO = ROOT / "scene2-original-chatterbox.wav"


def wav_duration(path: Path) -> float:
    info = sf.info(str(path))
    return info.frames / float(info.samplerate)


async def main() -> None:
    duration = wav_duration(AUDIO)
    heard = await FasterWhisperAligner().align(AUDIO, NARRATION)
    aligned = align_to_canonical(
        NARRATION,
        heard.words,
        block_start=0.0,
        block_end=duration,
        label="video2-scene2-motion-proof",
    )
    timing = build_motion_timing_payload(
        aligned.words,
        duration,
        {
            "salted_hash": "salted password hash",
            "same_password": "password",
            "unique_salt": "unique salt",
            "different_outputs": "different",
            "each_account": "each account",
        },
    )
    payload = {
        "project_id": "01a11afb-aa75-7306-b2a2-e5e7af87b988",
        "script_id": "01a11b7b-b741-76a0-ad30-ffe7096e0c2b",
        "scene_id": "01a11b7b-b750-7785-957b-a741958b4e30",
        "narration": NARRATION,
        "alignment_provider": heard.provider,
        "alignment_quality": aligned.quality.as_dict(),
        **timing,
    }
    (ROOT / "motion-timing.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (ROOT / "scene2-captions.ass").write_text(
        build_ass(aligned.words), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
