"""Time helpers.

All timestamps in this system are timezone-aware UTC. Naive datetimes are a bug.
Centralised here so tests can monkeypatch a single function.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

__all__ = ["seconds_from_now", "utcnow"]


def utcnow() -> datetime:
    return datetime.now(UTC)


def seconds_from_now(seconds: float) -> datetime:
    return utcnow() + timedelta(seconds=seconds)
