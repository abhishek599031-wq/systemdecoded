"""Generate Gemini voice auditions.

    docker compose exec worker python -m app.cli.audition
    docker compose exec worker python -m app.cli.audition Charon Kore Puck

Writes one WAV per voice to media/voice_auditions/ and prints a comparison
table. Never prints the API key.
"""

from __future__ import annotations

import asyncio
import sys

from app.core.errors import TerminalError
from app.core.logging import configure_logging
from app.services.voice_audition import (
    AUDITION_SCRIPT,
    DEFAULT_SHORTLIST,
    generate_auditions,
)


async def main(argv: list[str]) -> int:
    configure_logging(service="audition")
    voices = tuple(argv) if argv else DEFAULT_SHORTLIST

    try:
        auditions = await generate_auditions(voices=voices)
    except TerminalError as exc:
        # Sanitised by construction: provider errors never carry the key.
        print(f"\nAudition failed: {exc}\n", file=sys.stderr)
        return 1

    words = len(AUDITION_SCRIPT.split())
    print(f"\n{len(auditions)} auditions · {words} words · model {auditions[0].model}\n")
    print(f"{'voice':14} {'dur':>6} {'wpm':>5} {'rate':>7}  file")
    for a in auditions:
        wpm = words / (a.duration_seconds / 60) if a.duration_seconds else 0
        print(
            f"{a.voice:14} {a.duration_seconds:6.2f} {wpm:5.0f} {a.sample_rate:7} "
            f" {a.path}"
        )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
