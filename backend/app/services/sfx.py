"""Internally generated sound design.

Deliberately synthesised rather than sourced. Two reasons: it keeps the
licensing story trivially clean (every asset is GENERATED, and the QC gate
stays honest), and interface-style tones fit SystemDecoded better than library
music would.

Kept extremely restrained. Narration is the content; these are punctuation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from app.core.logging import get_logger

log = get_logger("sfx")

SAMPLE_RATE = 48_000


@dataclass(frozen=True, slots=True)
class Cue:
    """One sound effect placed at an absolute time in the timeline."""

    path: Path
    at_seconds: float
    gain: float = 1.0


def _envelope(n: int, attack: float, release: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Attack/release shaping. Without it, tones click at the boundaries."""
    env = np.ones(n)
    a = max(1, int(attack * sr))
    r = max(1, int(release * sr))
    env[:a] = np.linspace(0.0, 1.0, a)
    env[-r:] = np.linspace(1.0, 0.0, r) ** 2
    return env


def _tone(freq: float, duration: float, *, amp: float = 0.25, harmonic: float = 0.0) -> np.ndarray:
    t = np.linspace(0.0, duration, int(duration * SAMPLE_RATE), endpoint=False)
    wave = np.sin(2 * np.pi * freq * t)
    if harmonic:
        wave += harmonic * np.sin(2 * np.pi * freq * 2 * t)
    return amp * wave * _envelope(len(t), 0.004, duration * 0.7)


def confirm_tone(path: Path) -> Path:
    """Two rising notes — a match/confirmation. Soft, short, not a fanfare."""
    first = _tone(660.0, 0.10, amp=0.18, harmonic=0.18)
    gap = np.zeros(int(0.035 * SAMPLE_RATE))
    second = _tone(880.0, 0.16, amp=0.20, harmonic=0.15)
    _write(path, np.concatenate([first, gap, second]))
    return path


def resolve_tone(path: Path) -> Path:
    """A single low, settled note for the ending. Closure, not celebration."""
    body = _tone(392.0, 0.42, amp=0.16, harmonic=0.22)
    _write(path, body)
    return path


def tick(path: Path) -> Path:
    """A dry click for a timer or digit change."""
    n = int(0.035 * SAMPLE_RATE)
    noise = np.random.default_rng(7).normal(0, 1, n)
    # Crude band-limiting: a short moving average knocks off the harsh top end.
    kernel = np.ones(6) / 6
    shaped = np.convolve(noise, kernel, mode="same")
    _write(path, 0.06 * shaped * _envelope(n, 0.001, 0.03))
    return path


def _write(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = float(np.max(np.abs(samples))) or 1.0
    # Leave headroom; these get mixed under narration and must never fight it.
    sf.write(str(path), (samples / peak * 0.5).astype(np.float32), SAMPLE_RATE)


def build_library(out_dir: Path) -> dict[str, Path]:
    """Generate the effect set. Deterministic, so renders stay reproducible."""
    out_dir.mkdir(parents=True, exist_ok=True)
    library = {
        "confirm": confirm_tone(out_dir / "sfx_confirm.wav"),
        "resolve": resolve_tone(out_dir / "sfx_resolve.wav"),
        "tick": tick(out_dir / "sfx_tick.wav"),
    }
    log.info("sfx.generated", effects=sorted(library))
    return library
