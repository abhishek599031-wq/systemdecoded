"""Health and readiness endpoints.

Two endpoints because they answer different questions:

- `/health/live`  — is this process running? Never touches the database, so a
                    database outage does not cause a restart loop.
- `/health/ready` — can it serve traffic? Checks the database and whether
                    migrations have run.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import settings
from app.db.session import get_db
from app.services import system_status

router = APIRouter(tags=["health"])


@router.get("/health/live", summary="Liveness probe")
async def live() -> dict[str, str]:
    return {
        "status": "alive",
        "app": settings.APP_NAME,
        "version": __version__,
        "environment": settings.ENVIRONMENT,
    }


@router.get("/health/ready", summary="Readiness probe")
async def ready(response: Response, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    db_ok, db_error = await system_status.check_database(db)
    revision = await system_status.get_migration_revision(db) if db_ok else None

    checks = {
        "database": {"ok": db_ok, "error": db_error},
        "migrations": {
            "ok": revision is not None,
            "revision": revision,
            "error": None if revision else "alembic_version table not found; run migrations",
        },
    }
    ready_now = all(check["ok"] for check in checks.values())
    if not ready_now:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ready" if ready_now else "not_ready", "checks": checks}
