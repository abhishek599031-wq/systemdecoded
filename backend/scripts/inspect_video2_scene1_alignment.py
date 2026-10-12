"""Print raw ASR evidence for the Scene 1 QC warning."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.providers.alignment.faster_whisper import FasterWhisperAligner

AUDIO = Path(
    "/media/renders/01a11afb-aa75-7306-b2a2-e5e7af87b988/audio/scene_01.wav"
)
SCRIPT = (
    "Good websites don\u2019t store your password as readable text. "
    "They store a verifier."
)


async def main() -> None:
    for model in ("base.en", "small.en"):
        result = await FasterWhisperAligner(model).align(AUDIO, SCRIPT)
        print(model, " ".join(word.word for word in result.words))


if __name__ == "__main__":
    asyncio.run(main())
