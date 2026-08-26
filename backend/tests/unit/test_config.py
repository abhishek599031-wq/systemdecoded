"""Configuration parsing and runtime validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


def _settings(env_text: str, tmp_path: Path) -> Settings:
    """Build Settings from a temporary .env, exercising the dotenv source.

    Only useful for fields the test suite does not also set in the real process
    environment — real environment variables outrank dotenv. Fields that
    `tests/conftest.py` exports (ENVIRONMENT, DEBUG, DATABASE_URL, …) must be
    tested with `_settings_kwargs` instead.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(env_text, encoding="utf-8")
    return Settings(_env_file=str(env_file))  # type: ignore[call-arg]


def _settings_kwargs(**overrides: object) -> Settings:
    """Build Settings from direct arguments, which outrank every other source."""
    return Settings(**overrides)  # type: ignore[arg-type]


# --------------------------------------------------------------- list fields ---
# Regression: pydantic-settings JSON-decodes list fields inside the dotenv
# source before any validator runs, so a bare `WORKER_JOB_TYPES=` raised
# SettingsError. NoDecode + the CSV validator is the fix.
#
# WORKER_JOB_TYPES is exercised through `_settings` (a temp .env file) because
# nothing in this process's real environment sets it. CORS_ORIGINS is exercised
# through `_settings_kwargs` instead: docker-compose always sets a real
# CORS_ORIGINS env var for the backend container, and real environment
# variables outrank a dotenv file, so a dotenv-based test would silently assert
# against the container's value rather than the one it wrote.
def test_empty_worker_job_types_parses_to_empty_list(tmp_path: Path) -> None:
    assert _settings("WORKER_JOB_TYPES=\n", tmp_path).WORKER_JOB_TYPES == []


def test_comma_separated_list_is_split(tmp_path: Path) -> None:
    settings = _settings("WORKER_JOB_TYPES=media.render, media.tts ,content.script\n", tmp_path)
    assert settings.WORKER_JOB_TYPES == ["media.render", "media.tts", "content.script"]


def test_empty_cors_origins_parses_to_empty_list() -> None:
    assert _settings_kwargs(CORS_ORIGINS="").CORS_ORIGINS == []


def test_json_list_is_still_accepted() -> None:
    settings = _settings_kwargs(CORS_ORIGINS='["http://a.test","http://b.test"]')
    assert settings.CORS_ORIGINS == ["http://a.test", "http://b.test"]


def test_single_value_becomes_a_one_item_list() -> None:
    assert _settings_kwargs(CORS_ORIGINS="http://only.test").CORS_ORIGINS == [
        "http://only.test"
    ]


def test_defaults_apply_when_absent(tmp_path: Path) -> None:
    """Only asserts fields the real process environment does not set.

    CORS_ORIGINS is deliberately excluded: docker-compose sets it for the
    backend container, and real env vars outrank dotenv, so asserting its
    default here would test the container's config rather than the default.
    """
    settings = _settings("APP_NAME=SystemDecoded\n", tmp_path)
    assert settings.WORKER_JOB_TYPES == []


# ----------------------------------------------------------- runtime checks ---
def test_development_defaults_are_valid() -> None:
    assert _settings_kwargs(ENVIRONMENT="development").validate_runtime() == []


def test_production_requires_a_secrets_key() -> None:
    problems = _settings_kwargs(
        ENVIRONMENT="production", DEBUG=False, SECRETS_KEY=""
    ).validate_runtime()
    assert any("SECRETS_KEY" in p for p in problems)


def test_production_rejects_debug() -> None:
    problems = _settings_kwargs(
        ENVIRONMENT="production", DEBUG=True, SECRETS_KEY="abc"
    ).validate_runtime()
    assert any("DEBUG" in p for p in problems)


def test_youtube_enabled_without_credentials_is_reported() -> None:
    problems = _settings_kwargs(
        YOUTUBE_API_ENABLED=True, GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET=""
    ).validate_runtime()
    assert any("GOOGLE_CLIENT_ID" in p for p in problems)


def test_youtube_disabled_without_credentials_is_fine() -> None:
    """The whole point of Phase 0: missing YouTube credentials must block nothing."""
    assert _settings_kwargs(YOUTUBE_API_ENABLED=False).validate_runtime() == []


# ------------------------------------------------------------------- misc ---
def test_sync_database_url_matches_the_async_one() -> None:
    """psycopg3 serves both engines, so there is only ever one URL to keep right."""
    settings = _settings_kwargs(DATABASE_URL="postgresql+psycopg://u:p@h:5432/db")
    assert settings.SYNC_DATABASE_URL == settings.DATABASE_URL


@pytest.mark.parametrize(
    ("env", "expected"),
    [("production", True), ("development", False), ("test", False)],
)
def test_is_production_flag(env: str, expected: bool) -> None:
    assert _settings_kwargs(ENVIRONMENT=env).is_production is expected


def test_worker_concurrency_is_bounded() -> None:
    with pytest.raises(ValueError, match="WORKER_CONCURRENCY"):
        _settings_kwargs(WORKER_CONCURRENCY=0)
