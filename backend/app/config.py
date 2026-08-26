"""Application configuration.

Single source of truth for environment-driven settings. Everything that differs
between local development and a VPS lives here and nowhere else.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, ClassVar, Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- app ---
    APP_NAME: str = "SystemDecoded"
    APP_TAGLINE: str = "Complex Technology. Decoded."
    ENVIRONMENT: Literal["development", "production", "test"] = "development"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"

    # ----------------------------------------------------------- database ---
    # psycopg3 serves both the sync (Alembic) and async (app) engines, so the
    # stack carries exactly one PostgreSQL driver.
    DATABASE_URL: str = (
        "postgresql+psycopg://systemdecoded:systemdecoded@localhost:5432/systemdecoded"
    )
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_ECHO: bool = False

    # ------------------------------------------------------------ logging ---
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "console"

    # ------------------------------------------------------------- worker ---
    WORKER_ID: str = "worker-1"
    WORKER_CONCURRENCY: int = Field(default=2, ge=1, le=32)
    WORKER_POLL_INTERVAL_SECONDS: float = Field(default=1.0, gt=0)
    WORKER_POLL_JITTER_SECONDS: float = Field(default=0.3, ge=0)
    WORKER_HEARTBEAT_SECONDS: float = Field(default=10.0, gt=0)
    WORKER_SHUTDOWN_GRACE_SECONDS: float = Field(default=30.0, ge=0)

    # Job types this worker will claim. Empty list == claim anything.
    # Lets media-heavy jobs move to a dedicated worker later with no code change.
    #
    # NoDecode: without it pydantic-settings JSON-decodes list fields inside the
    # dotenv source, before any validator runs — so a plain `WORKER_JOB_TYPES=`
    # in .env raises a parse error. NoDecode hands the raw string to
    # `_split_csv` below, which accepts both CSV and JSON.
    WORKER_JOB_TYPES: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # ---------------------------------------------------------- job queue ---
    JOB_DEFAULT_MAX_ATTEMPTS: int = Field(default=3, ge=1)
    JOB_DEFAULT_TIMEOUT_SECONDS: int = Field(default=300, gt=0)
    JOB_RETRY_BASE_SECONDS: float = Field(default=5.0, gt=0)
    JOB_RETRY_MAX_SECONDS: float = Field(default=3600.0, gt=0)
    JOB_HISTORY_RETENTION_DAYS: int = Field(default=90, ge=1)

    # ---------------------------------------------------------- scheduler ---
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_TIMEZONE: str = "UTC"

    # -------------------------------------------------------------- media ---
    MEDIA_ROOT: Path = Path("/media")

    # ------------------------------------------------------------ secrets ---
    # Used to encrypt OAuth tokens at rest (Phase 1). Required in production.
    SECRETS_KEY: str = ""

    # ---------------------------------------------------- youtube (phase 1) ---
    # The app must still start and run fully with all of these blank; only the
    # YouTube endpoints report themselves unconfigured.
    YOUTUBE_API_ENABLED: bool = False
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    # Must match a redirect URI registered in the Google Cloud OAuth client
    # *exactly* — scheme, host, port and path. Google compares it as a string.
    GOOGLE_REDIRECT_URI: str = "http://localhost:8080/api/v1/youtube/oauth/callback"

    # Declared, not detected: nothing in the API reports an OAuth consent
    # screen's publishing status. While it is "testing", Google expires every
    # refresh token after 7 days (ARCH §3.3), so the UI must warn about it.
    GOOGLE_CONSENT_PUBLISHING_STATUS: Literal["testing", "production"] = "testing"

    # Where the OAuth callback sends the browser once it is done.
    FRONTEND_URL: str = "http://localhost:3030"

    # Refresh an access token once it is within this window of expiring.
    OAUTH_REFRESH_SKEW_SECONDS: int = Field(default=600, ge=60)

    # -------------------------------------------------------- llm (phase 4) ---
    LLM_LOCAL_BASE_URL: str = "http://host.docker.internal:11434"
    LLM_LOCAL_MODEL: str = "qwen2.5:7b-instruct"
    LLM_CREATIVE_MODE: Literal["local", "manual", "external_api"] = "manual"
    LLM_MECHANICAL_MODE: Literal["local", "manual", "external_api"] = "local"

    # --------------------------------------------------------------- cors ---
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    @field_validator("CORS_ORIGINS", "WORKER_JOB_TYPES", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Parse list settings from .env.

        These fields are annotated `NoDecode`, so this validator owns decoding
        entirely — pydantic-settings will not JSON-parse them first. Accepts
        both `a,b,c` and `["a","b","c"]`, and treats an empty value as an empty
        list rather than an error.
        """
        if not isinstance(v, str):
            return v
        v = v.strip()
        if not v:
            return []
        if v.startswith("["):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                pass  # not valid JSON after all; fall through to CSV
        return [item.strip() for item in v.split(",") if item.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SYNC_DATABASE_URL(self) -> str:
        """Alembic runs synchronously; same driver, sync engine."""
        return self.DATABASE_URL

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    def validate_runtime(self) -> list[str]:
        """Return blocking misconfigurations. Called at startup.

        Kept separate from field validation so the app can *report* problems
        rather than refusing to import.
        """
        problems: list[str] = []
        if self.is_production:
            if not self.SECRETS_KEY:
                problems.append("SECRETS_KEY must be set in production")
            if self.DEBUG:
                problems.append("DEBUG must be false in production")
        if self.YOUTUBE_API_ENABLED:
            if not (self.GOOGLE_CLIENT_ID and self.GOOGLE_CLIENT_SECRET):
                problems.append(
                    "YOUTUBE_API_ENABLED is true but "
                    "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET are unset"
                )
            if not self.SECRETS_KEY:
                # Refusing to store tokens unencrypted is deliberate: this is
                # the one place the system holds a durable credential.
                problems.append(
                    "YOUTUBE_API_ENABLED is true but SECRETS_KEY is unset; "
                    "OAuth tokens cannot be encrypted at rest"
                )
            problems.extend(self._redirect_uri_problems())
        return problems

    # The path half of the redirect URI is fully determined by our own routing,
    # so a mismatch here is always a config typo and is worth catching at
    # startup rather than at the end of a consent round trip.
    OAUTH_CALLBACK_PATH: ClassVar[str] = "/api/v1/youtube/oauth/callback"

    def _redirect_uri_problems(self) -> list[str]:
        from urllib.parse import urlparse

        problems: list[str] = []
        parsed = urlparse(self.GOOGLE_REDIRECT_URI)
        if not parsed.scheme or not parsed.netloc:
            problems.append(
                f"GOOGLE_REDIRECT_URI is not an absolute URL: {self.GOOGLE_REDIRECT_URI}"
            )
            return problems
        if parsed.path != self.OAUTH_CALLBACK_PATH:
            problems.append(
                f"GOOGLE_REDIRECT_URI path is {parsed.path!r} but the callback route is "
                f"{self.OAUTH_CALLBACK_PATH!r}; Google matches redirect URIs exactly"
            )
        if self.is_production and parsed.scheme != "https":
            problems.append("GOOGLE_REDIRECT_URI must use https in production")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
