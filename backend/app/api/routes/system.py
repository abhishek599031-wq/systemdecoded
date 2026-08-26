"""System status endpoint (dashboard data source)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.config import settings
from app.db.session import get_db
from app.services import system_status

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status", summary="Aggregated system status")
async def get_status(db: Annotated[AsyncSession, Depends(get_db)]) -> dict[str, Any]:
    status = await system_status.get_system_status(db)
    status["version"] = __version__
    return status


@router.get("/info", summary="Static build and configuration info")
async def get_info() -> dict[str, Any]:
    """Non-secret configuration. Never returns credentials or tokens."""
    return {
        "app": settings.APP_NAME,
        "tagline": settings.APP_TAGLINE,
        "version": __version__,
        "environment": settings.ENVIRONMENT,
        "phase": 0,
        "phase_description": "Foundation: queue, worker, scheduler, health, migrations",
        "capabilities": {
            "youtube_oauth": False,
            "youtube_upload": False,
            "analytics": False,
            "llm_generation": False,
            "media_production": False,
        },
    }
