"""Metered-provider request accounting.

Gemini's free tier caps requests per *day*, and the API exposes no endpoint or
response header reporting how much of that allowance is left — the documented
way to see it is the AI Studio dashboard, which a render job cannot consult. So
the count has to be ours.

One row per (provider, model, quota day), incremented as requests are made.
This lives in Postgres rather than in memory or a file because it must survive a
worker restart, be shared between worker processes, and increment atomically
when two renders overlap — the same reasons the job queue is a table.

Deliberately not `AppSetting`: that is for operator-set flags, and a counter
that two workers increment concurrently wants a real row and an UPSERT, not a
read-modify-write on a JSONB blob.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ProviderQuotaUsage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """How many requests we have spent against one model, on one quota day."""

    __tablename__ = "provider_quota_usage"

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)

    # The provider's quota day, not ours. Gemini's RPD resets at midnight
    # Pacific, so this is a Pacific date — see app/services/quota.py.
    usage_date: Mapped[date] = mapped_column(Date, nullable=False)

    requests_used: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    # The limit the API itself reported, when it has told us. A 429 carries the
    # real `quotaValue`, which is more trustworthy than anything configured, and
    # catches a tier change without an edit to .env.
    observed_limit: Mapped[int | None] = mapped_column(Integer)

    # Set the moment the provider tells us the day's allowance is gone. This is
    # the one fully reliable signal: our own count can be short if requests were
    # made outside this system, but an explicit rejection cannot be argued with.
    exhausted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "provider", "model", "usage_date", name="uq_provider_quota_usage_day"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ProviderQuotaUsage {self.provider}/{self.model} "
            f"{self.usage_date} used={self.requests_used}>"
        )
