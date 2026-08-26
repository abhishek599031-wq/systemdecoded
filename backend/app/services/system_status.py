"""System status aggregation.

Backs the health endpoints and the dashboard's "system status" panel
(`PHASE-1-ARCHITECTURE.md` §12.1).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.clock import utcnow
from app.models.channel import AppSetting
from app.models.enums import ConnectionStatus, JobStatus
from app.models.job import BackgroundJob

HEARTBEAT_KEY = "system.last_heartbeat"

# If the scheduler-driven heartbeat is older than this, the worker/scheduler
# pair is not doing its job even though both processes may be "up".
HEARTBEAT_STALE_AFTER = timedelta(minutes=15)


async def record_heartbeat(session: AsyncSession) -> datetime:
    """Upsert the liveness marker written by the `system.heartbeat` job."""
    now = utcnow()
    stmt = (
        pg_insert(AppSetting)
        .values(
            key=HEARTBEAT_KEY,
            value={"at": now.isoformat(), "worker_id": settings.WORKER_ID},
            description="Written by the system.heartbeat job; proves scheduler->worker flow.",
        )
        .on_conflict_do_update(
            index_elements=["key"],
            set_={"value": {"at": now.isoformat(), "worker_id": settings.WORKER_ID}},
        )
    )
    await session.execute(stmt)
    await session.flush()
    return now


async def get_last_heartbeat(session: AsyncSession) -> datetime | None:
    row = await session.get(AppSetting, HEARTBEAT_KEY)
    if row is None:
        return None
    raw = (row.value or {}).get("at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:  # pragma: no cover - defensive
        return None


async def check_database(session: AsyncSession) -> tuple[bool, str | None]:
    try:
        await session.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return False, str(exc)


async def get_migration_revision(session: AsyncSession) -> str | None:
    """Current Alembic revision, or None if migrations have never run."""
    try:
        result = await session.execute(text("SELECT version_num FROM alembic_version"))
        return result.scalar_one_or_none()
    except Exception:  # noqa: BLE001 - table absent means "not migrated"
        return None


async def get_job_counts(session: AsyncSession) -> dict[str, int]:
    result = await session.execute(
        select(BackgroundJob.status, func.count()).group_by(BackgroundJob.status)
    )
    counts = {status.value: 0 for status in JobStatus}
    for status, count in result.all():
        counts[status] = count
    # A retry pending is a QUEUED row that has already been attempted. Worth
    # surfacing separately because it means something is going wrong.
    retrying = await session.execute(
        select(func.count())
        .select_from(BackgroundJob)
        .where(BackgroundJob.status == JobStatus.QUEUED)
        .where(BackgroundJob.attempt > 0)
    )
    counts["RETRY_PENDING"] = retrying.scalar_one()
    return counts


async def get_youtube_connection_status(session: AsyncSession) -> str:
    """Current YouTube connection status, without importing the whole service."""
    from app.models.youtube import YouTubeConnection

    row = (await session.execute(select(YouTubeConnection).limit(1))).scalar_one_or_none()
    return row.status if row else ConnectionStatus.NOT_CONNECTED.value


async def get_system_status(session: AsyncSession) -> dict[str, Any]:
    db_ok, db_error = await check_database(session)
    if not db_ok:
        return {
            "status": "unhealthy",
            "database": {"connected": False, "error": db_error},
        }

    heartbeat = await get_last_heartbeat(session)
    heartbeat_stale = heartbeat is None or (utcnow() - heartbeat) > HEARTBEAT_STALE_AFTER
    counts = await get_job_counts(session)
    revision = await get_migration_revision(session)
    connection_status = await get_youtube_connection_status(session)

    return {
        "status": "healthy" if revision else "degraded",
        "app": {
            "name": settings.APP_NAME,
            "tagline": settings.APP_TAGLINE,
            "environment": settings.ENVIRONMENT,
        },
        "database": {"connected": True, "migration_revision": revision},
        "jobs": counts,
        "heartbeat": {
            "last_at": heartbeat.isoformat() if heartbeat else None,
            "stale": heartbeat_stale,
            "stale_after_seconds": int(HEARTBEAT_STALE_AFTER.total_seconds()),
        },
        "integrations": {
            "youtube": {
                "enabled": settings.YOUTUBE_API_ENABLED,
                "credentials_present": bool(
                    settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET
                ),
                "connection_status": connection_status,
                "connected": connection_status == ConnectionStatus.ACTIVE,
                "status": "implemented",
            },
            "llm": {
                "mechanical_mode": settings.LLM_MECHANICAL_MODE,
                "creative_mode": settings.LLM_CREATIVE_MODE,
                "status": "not_implemented_until_phase_4",
            },
        },
        "config_problems": settings.validate_runtime(),
    }
