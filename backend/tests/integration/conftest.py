"""Integration test fixtures — require a live PostgreSQL instance.

Skipped with a clear reason rather than failed when one is not reachable.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import dispose_engine, get_sessionmaker
from tests.conftest import TEST_DATABASE_URL

# Tables cleared between tests. `channel` is excluded on purpose: it is seeded
# by the initial migration and the channel API tests depend on that row.
TRUNCATE_TABLES = (
    "job_event",
    "background_job",
    "app_setting",
    "youtube_connection",
    "oauth_state",
)


def _dsn(url: str, database: str | None = None) -> str:
    """Convert a SQLAlchemy URL into a libpq DSN, optionally swapping the db."""
    parsed = urlparse(url.replace("postgresql+psycopg://", "postgresql://"))
    db = database or parsed.path.lstrip("/")
    return (
        f"host={parsed.hostname} port={parsed.port or 5432} "
        f"user={parsed.username} password={parsed.password} dbname={db}"
    )


def _ensure_database() -> None:
    target = urlparse(TEST_DATABASE_URL.replace("postgresql+psycopg://", "postgresql://"))
    dbname = target.path.lstrip("/")
    with psycopg.connect(_dsn(TEST_DATABASE_URL, "postgres"), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{dbname}"')


def _run_migrations() -> None:
    """Apply Alembic migrations to the test database.

    Deliberately migrations rather than `metadata.create_all`: the schema under
    test is then the schema that ships, and the migration itself is exercised
    on every run.
    """
    from alembic.config import Config

    from alembic import command

    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def database() -> None:
    try:
        with psycopg.connect(_dsn(TEST_DATABASE_URL, "postgres"), connect_timeout=3):
            pass
    except Exception as exc:  # noqa: BLE001 - becomes a skip reason
        pytest.skip(
            f"PostgreSQL not reachable at {TEST_DATABASE_URL} ({exc}). "
            "Start it with: docker compose up -d postgres"
        )
    _ensure_database()
    _run_migrations()


@pytest.fixture(autouse=True)
async def clean_tables(database: None) -> AsyncIterator[None]:
    """Truncate mutable tables before each test; dispose the engine after.

    The dispose matters: pytest-asyncio gives each test a fresh event loop, and
    pooled connections bound to a previous loop are unusable.
    """
    async with get_sessionmaker()() as session:
        await session.execute(
            text(f"TRUNCATE {', '.join(TRUNCATE_TABLES)} RESTART IDENTITY CASCADE")
        )
        # `channel` is seeded by the migration rather than truncated, so reset
        # the fields a YouTube connection writes to. Without this, one test's
        # successful connect leaks into the next test's "not connected" state.
        await session.execute(
            text(
                """
                UPDATE channel SET
                    youtube_channel_id = NULL,
                    thumbnail_url = NULL,
                    uploads_playlist_id = NULL,
                    subscriber_count = NULL,
                    video_count = NULL,
                    view_count = NULL,
                    connection_status = 'NOT_CONNECTED',
                    connected_at = NULL,
                    last_sync_at = NULL,
                    publishing_enabled = false
                """
            )
        )
        await session.commit()
    try:
        yield
    finally:
        await dispose_engine()


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A session for tests that drive the queue and services directly."""
    async with get_sessionmaker()() as s:
        yield s
        await s.commit()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the real ASGI app and the real test database."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
