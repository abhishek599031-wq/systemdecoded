"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.routes import channel, jobs, system, youtube

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(jobs.router)
api_router.include_router(channel.router)
api_router.include_router(youtube.router)
