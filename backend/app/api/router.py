"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.routes import analytics, channel, content_intelligence, jobs, projects, system, youtube

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(jobs.router)
api_router.include_router(channel.router)
api_router.include_router(projects.router)
api_router.include_router(youtube.router)
api_router.include_router(analytics.router)
api_router.include_router(content_intelligence.router)
