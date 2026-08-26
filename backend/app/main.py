"""FastAPI application entrypoint.

Run with:  uvicorn app.main:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.middleware import RequestContextMiddleware
from app.api.router import api_router
from app.api.routes import health
from app.config import settings
from app.core.errors import AppError
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine
from app.jobs.registry import all_definitions, load_all_jobs

log = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(service="api")

    # Job types must be registered before the API can enqueue anything.
    load_all_jobs()

    problems = settings.validate_runtime()
    for problem in problems:
        log.error("config.problem", problem=problem)

    log.info(
        "api.starting",
        version=__version__,
        environment=settings.ENVIRONMENT,
        registered_job_types=len(all_definitions()),
        youtube_enabled=settings.YOUTUBE_API_ENABLED,
        config_problems=len(problems),
    )
    try:
        yield
    finally:
        await dispose_engine()
        log.info("api.stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.APP_NAME} API",
        description=(
            f"{settings.APP_TAGLINE}\n\n"
            "Autonomous YouTube content operations system. "
            "Phase 0 — foundation: job queue, worker, scheduler, health, migrations."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    _register_exception_handlers(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """One error envelope for every failure mode.

    Clients should never have to branch on which layer produced an error, so
    application errors, framework HTTP errors, validation errors and unexpected
    exceptions all serialise to `{"error": {code, message, ...}}`.
    """

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        log.warning(
            "error.app",
            code=exc.code,
            status_code=exc.status_code,
            message=exc.message,
            path=request.url.path,
        )
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload(request_id))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed.",
                    "detail": exc.errors(),
                    "request_id": request_id,
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": f"http_{exc.status_code}",
                    "message": str(exc.detail),
                    "request_id": request_id,
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        log.exception("error.unhandled", path=request.url.path, request_id=request_id)
        # Never leak internals in production; in development the detail is worth
        # far more than the (single-user, local) exposure risk.
        detail = repr(exc) if settings.DEBUG else None
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                    "detail": detail,
                    "request_id": request_id,
                }
            },
        )


app = create_app()
