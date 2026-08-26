"""Structured logging.

Console renderer in development, JSON in production. Every log line carries
whatever is bound into the structlog contextvars for the current task, which is
how `request_id`, `job_id`, `job_type` and `attempt` end up on every line
without being passed around explicitly (`PHASE-1-ARCHITECTURE.md` §9.2).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.config import settings

_configured = False


def configure_logging(
    *,
    level: str | None = None,
    fmt: str | None = None,
    service: str = "backend",
) -> None:
    global _configured

    log_level = getattr(logging, (level or settings.LOG_LEVEL).upper())
    log_format = fmt or settings.LOG_FORMAT

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )

    # Quieten dependencies that log a line per operation.
    for noisy in ("uvicorn.access", "sqlalchemy.engine.Engine", "apscheduler.executors.default"):
        logging.getLogger(noisy).setLevel(max(log_level, logging.WARNING))

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if log_format == "json":
        renderer: Any = structlog.processors.JSONRenderer()
        shared.append(structlog.processors.format_exc_info)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
        shared.append(structlog.processors.ExceptionPrettyPrinter())

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    structlog.contextvars.bind_contextvars(service=service, env=settings.ENVIRONMENT)
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    if not _configured:
        configure_logging()
    return structlog.get_logger(name)  # type: ignore[return-value]
