"""Async engine and session management."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.DATABASE_URL,
            echo=settings.DB_ECHO,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_pre_ping=True,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Transactional scope for workers, scheduler and scripts.

    Commits on success, rolls back on exception. This is the unit that makes
    "state transition + job enqueue commit together" work (ARCH §8.3 rule 4).
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. Commits at the end of a successful request."""
    async with session_scope() as session:
        yield session


async def wait_for_database(
    attempts: int = 30,
    delay_seconds: float = 2.0,
) -> bool:
    """Block until the database answers, or give up after `attempts`.

    The worker cannot do anything without a database, so it waits rather than
    crash-looping with a stack trace — which is what Compose's `restart` policy
    would otherwise produce during a transient outage. Returns False so the
    caller can exit cleanly with a readable message instead of a traceback.

    The API deliberately does *not* call this: it should start and serve
    /health/live regardless, reporting the outage through /health/ready.
    """
    from sqlalchemy import text as _text

    from app.core.logging import get_logger

    log = get_logger("db")
    for attempt in range(1, attempts + 1):
        try:
            async with get_sessionmaker()() as session:
                await session.execute(_text("SELECT 1"))
            if attempt > 1:
                log.info("db.available", after_attempts=attempt)
            return True
        except Exception as exc:  # noqa: BLE001 - reported, then retried
            if attempt == attempts:
                log.error(
                    "db.unreachable",
                    attempts=attempts,
                    error=str(exc).splitlines()[0][:200],
                )
                return False
            log.warning(
                "db.waiting",
                attempt=attempt,
                of=attempts,
                retry_in_seconds=delay_seconds,
            )
            # Drop the poisoned pool so the next attempt dials fresh.
            await dispose_engine()
            await asyncio.sleep(delay_seconds)
    return False


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
