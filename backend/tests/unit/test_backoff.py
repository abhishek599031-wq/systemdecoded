"""Backoff is a pure function, so it gets exhaustive cheap tests."""

from __future__ import annotations

import pytest

from app.jobs.backoff import compute_backoff_seconds


def test_grows_exponentially_without_jitter() -> None:
    kwargs = {"base_seconds": 5.0, "max_seconds": 1000.0, "jitter_ratio": 0.0}
    assert compute_backoff_seconds(1, **kwargs) == 5.0
    assert compute_backoff_seconds(2, **kwargs) == 10.0
    assert compute_backoff_seconds(3, **kwargs) == 20.0
    assert compute_backoff_seconds(4, **kwargs) == 40.0


def test_respects_ceiling() -> None:
    delay = compute_backoff_seconds(
        20, base_seconds=5.0, max_seconds=60.0, jitter_ratio=0.0
    )
    assert delay == 60.0


def test_jitter_stays_within_band() -> None:
    # rand() == 0.0 gives the low edge, 1.0 the high edge.
    low = compute_backoff_seconds(
        1, base_seconds=10.0, max_seconds=100.0, jitter_ratio=0.25, rand=lambda: 0.0
    )
    high = compute_backoff_seconds(
        1, base_seconds=10.0, max_seconds=100.0, jitter_ratio=0.25, rand=lambda: 1.0
    )
    assert low == pytest.approx(7.5)
    assert high == pytest.approx(12.5)


def test_never_negative_even_with_full_jitter() -> None:
    delay = compute_backoff_seconds(
        1, base_seconds=1.0, max_seconds=10.0, jitter_ratio=1.0, rand=lambda: 0.0
    )
    assert delay >= 0.0


def test_is_monotonic_across_attempts() -> None:
    kwargs = {"base_seconds": 2.0, "max_seconds": 10_000.0, "jitter_ratio": 0.0}
    delays = [compute_backoff_seconds(a, **kwargs) for a in range(1, 10)]
    assert delays == sorted(delays)


@pytest.mark.parametrize("attempt", [0, -1])
def test_rejects_invalid_attempt(attempt: int) -> None:
    with pytest.raises(ValueError, match="attempt must be"):
        compute_backoff_seconds(attempt, base_seconds=1.0, max_seconds=10.0)


@pytest.mark.parametrize("ratio", [-0.1, 1.1])
def test_rejects_invalid_jitter(ratio: float) -> None:
    with pytest.raises(ValueError, match="jitter_ratio"):
        compute_backoff_seconds(1, base_seconds=1.0, max_seconds=10.0, jitter_ratio=ratio)
