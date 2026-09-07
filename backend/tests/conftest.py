"""Shared test configuration.

Environment redirection happens here, before any application module is
imported, so `app.config.settings` picks up the test database naturally and
every component — sessions, Alembic, the queue — points at the same place.

Database fixtures live in `tests/integration/conftest.py` so that
`pytest tests/unit` runs with no infrastructure at all.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Must happen before importing anything from `app`.
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://systemdecoded:systemdecoded@localhost:5432/systemdecoded_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("LOG_FORMAT", "console")
# Keep retry backoff sub-second so retry paths are testable without waiting.
os.environ.setdefault("JOB_RETRY_BASE_SECONDS", "0.01")
os.environ.setdefault("JOB_RETRY_MAX_SECONDS", "0.05")
# Gemini request pacing exists to stay under a real API's rate limit. Every
# Gemini call in the suite is mocked, so there is no limit to respect and the
# only thing pacing would do is make the tests take minutes.
os.environ["GEMINI_MIN_REQUEST_INTERVAL_SECONDS"] = "0"

from app.jobs.registry import load_all_jobs  # noqa: E402

load_all_jobs()
