"""Retry backoff.

A pure function, isolated from the queue so it can be unit-tested without a
database. Exponential with full-width jitter — jitter matters because without it
a batch of jobs that fail together will retry together, reproducing the same
contention that caused the failure.
"""

from __future__ import annotations

import random
from collections.abc import Callable

__all__ = ["compute_backoff_seconds"]


def compute_backoff_seconds(
    attempt: int,
    *,
    base_seconds: float,
    max_seconds: float,
    jitter_ratio: float = 0.25,
    rand: Callable[[], float] = random.random,
) -> float:
    """Delay before the next attempt.

    Args:
        attempt: the attempt number that just failed, 1-based.
        base_seconds: delay after the first failure.
        max_seconds: ceiling applied before jitter.
        jitter_ratio: +/- proportion of the delay to randomise, in [0, 1].
        rand: injectable source of randomness in [0, 1), for deterministic tests.

    Returns:
        Seconds to wait. Never negative.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    if not 0.0 <= jitter_ratio <= 1.0:
        raise ValueError("jitter_ratio must be within [0, 1]")

    raw = base_seconds * (2 ** (attempt - 1))
    capped = min(raw, max_seconds)
    jitter = capped * jitter_ratio * (rand() * 2 - 1)
    return max(0.0, capped + jitter)
