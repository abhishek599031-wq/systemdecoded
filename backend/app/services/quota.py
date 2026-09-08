"""Knowing, before a render starts, whether the provider can finish it.

**Why this exists.** A render asked Gemini for five narration blocks. Block one
succeeded, the free tier's daily allowance ran out, and the remaining four came
from the fallback. The result passed every quality gate and was still unusable:
it was not the voice that had been chosen. The single-voice guarantee in
`production` prevents *mixed* narration, but on its own it would have answered
this by re-reading the whole script in the fallback voice — a wasted render
either way. The real fix is to not start.

**What the API gives us.** Nothing useful. Gemini's documented daily limit is
requests-per-day, reset at midnight Pacific, and there is no endpoint or
response header reporting how much is left; the documented way to see it is the
AI Studio dashboard, which a job cannot consult. So the accounting is ours: one
row per (provider, model, quota day), incremented as requests are made.

**The three answers**, and what each means:

    SUFFICIENT    the day's remaining allowance covers the whole render
    INSUFFICIENT  it does not, or the provider has already said it is spent
    UNKNOWN       we cannot tell

`UNKNOWN` blocks, deliberately. An unreadable ledger is not evidence of
available quota, and for a pipeline meant to run unattended the cost of
declining a render that would have worked is much lower than the cost of a
half-narrated one nobody is awake to notice.

**What this cannot know.** Requests made outside this system — in AI Studio, by
another project on the same key, or before this ledger existed — are invisible
to the count, so the count is a lower bound. The provider's own rejection is the
backstop: the first daily-quota 429 pins the day as exhausted, and every later
preflight blocks at zero cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.models.quota import ProviderQuotaUsage

log = get_logger("quota")

__all__ = [
    "QuotaDecision",
    "QuotaSnapshot",
    "QuotaState",
    "daily_limit_for",
    "is_metered",
    "mark_exhausted",
    "preflight",
    "quota_day",
    "read_usage",
    "record_request",
]

# Gemini's requests-per-day quota resets at midnight Pacific, so the accounting
# day is a Pacific date. Using UTC would roll the counter over at 5pm Pacific
# and report a fresh allowance for the last seven hours of the provider's day —
# optimistic in exactly the direction that causes a half-finished render.
QUOTA_TIMEZONE = ZoneInfo("America/Los_Angeles")

# Providers whose requests are counted. Kokoro runs locally and is unmetered, so
# a Kokoro render never consults any of this.
METERED_PROVIDERS = frozenset({"gemini"})


class QuotaState(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    UNKNOWN = "UNKNOWN"


def quota_day(now: datetime | None = None) -> date:
    """The provider's current quota day."""
    moment = now or datetime.now(UTC)
    return moment.astimezone(QUOTA_TIMEZONE).date()


def is_metered(provider: str) -> bool:
    return provider in METERED_PROVIDERS


def daily_limit_for(provider: str) -> int | None:
    """The configured daily request cap, or None when there is not one.

    `0` means "no daily cap" — the setting to use once billing is enabled.
    """
    if provider != "gemini":
        return None
    limit = settings.GEMINI_TTS_DAILY_REQUEST_LIMIT
    return limit if limit > 0 else None


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    provider: str
    model: str
    day: date
    used: int
    limit: int | None
    exhausted: bool

    @property
    def available(self) -> int | None:
        if self.limit is None:
            return None
        return max(0, self.limit - self.used)


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    """The preflight verdict, in a form a job result can carry verbatim."""

    state: QuotaState
    provider: str
    model: str | None
    required_requests: int
    available_requests: int | None
    limit: int | None
    used: int
    day: date
    reason: str

    @property
    def allowed(self) -> bool:
        return self.state is QuotaState.SUFFICIENT

    def as_detail(self) -> dict:
        return {
            "state": self.state.value,
            "provider": self.provider,
            "model": self.model,
            "required_requests": self.required_requests,
            "available_requests": self.available_requests,
            "daily_limit": self.limit,
            "requests_used_today": self.used,
            "quota_day": self.day.isoformat(),
            "reason": self.reason,
        }


async def read_usage(
    session: AsyncSession, provider: str, model: str, day: date | None = None
) -> QuotaSnapshot:
    """What we have spent against this model today."""
    day = day or quota_day()
    row = (
        await session.execute(
            select(ProviderQuotaUsage).where(
                ProviderQuotaUsage.provider == provider,
                ProviderQuotaUsage.model == model,
                ProviderQuotaUsage.usage_date == day,
            )
        )
    ).scalar_one_or_none()

    configured = daily_limit_for(provider)
    if row is None:
        return QuotaSnapshot(
            provider=provider, model=model, day=day,
            used=0, limit=configured, exhausted=False,
        )

    # The API's own number beats anything configured: it reflects the tier we
    # are actually on, and it updates itself if that changes.
    limit = row.observed_limit if row.observed_limit is not None else configured
    return QuotaSnapshot(
        provider=provider, model=model, day=day,
        used=row.requests_used, limit=limit,
        exhausted=row.exhausted_at is not None,
    )


async def _upsert(session: AsyncSession, provider: str, model: str, day: date, **values):
    """Insert or update today's row without a read-modify-write race."""
    increment = values.pop("_increment", 0)
    insert_values = {
        "provider": provider, "model": model, "usage_date": day,
        "requests_used": increment, **values,
    }
    update_values = dict(values)
    if increment:
        update_values["requests_used"] = ProviderQuotaUsage.requests_used + increment

    stmt = pg_insert(ProviderQuotaUsage).values(**insert_values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_provider_quota_usage_day",
        set_=update_values or {"requests_used": ProviderQuotaUsage.requests_used},
    )
    await session.execute(stmt)


async def record_request(provider: str, model: str, count: int = 1) -> None:
    """Count requests against today's allowance, in their own transaction.

    Called from the provider on every response the API returns, including
    errors: a rejected request may still have been counted on their side, and
    for a preflight it is safer to overstate consumption than to understate it.
    The one exception is a daily-quota rejection, which `mark_exhausted` handles
    instead and which supersedes any count.

    Opens its own session rather than joining the render's transaction on
    purpose — the count must survive a render that later rolls back. Requests
    were still spent.
    """
    if not is_metered(provider):
        return

    from app.db.session import session_scope

    try:
        async with session_scope() as session:
            await _upsert(session, provider, model, quota_day(), _increment=count)
    except Exception as exc:  # noqa: BLE001 - accounting must not fail a render
        # Losing a count makes the next preflight optimistic, which is worth a
        # loud line in the log, but failing narration over a counter would be
        # a worse trade.
        log.error(
            "quota.record_failed",
            provider=provider, model=model, error=str(exc)[:200],
        )


async def mark_exhausted(provider: str, model: str, observed_limit: int | None = None) -> None:
    """Record that the provider has said today's allowance is gone.

    The one signal that cannot be argued with. Our own count is a lower bound —
    it cannot see requests made elsewhere — so this pins the day shut regardless
    of what the counter says.
    """
    if not is_metered(provider):
        return

    from app.db.session import session_scope

    day = quota_day()
    values: dict = {"exhausted_at": datetime.now(UTC)}
    if observed_limit is not None:
        values["observed_limit"] = observed_limit
        # Pin the counter to the limit as well, so `available` reads zero even
        # if we only ever saw part of the day's traffic.
        values["requests_used"] = observed_limit

    try:
        async with session_scope() as session:
            insert_values = {
                "provider": provider, "model": model, "usage_date": day,
                "requests_used": observed_limit or 0, **values,
            }
            stmt = pg_insert(ProviderQuotaUsage).values(**insert_values)
            await session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_provider_quota_usage_day", set_=values
                )
            )
        log.warning(
            "quota.exhausted",
            provider=provider, model=model,
            day=day.isoformat(), observed_limit=observed_limit,
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "quota.mark_exhausted_failed",
            provider=provider, model=model, error=str(exc)[:200],
        )


async def preflight(
    session: AsyncSession,
    provider: str,
    model: str | None,
    required_requests: int,
) -> QuotaDecision:
    """Decide whether the provider can cover a whole render, before it starts."""
    day = quota_day()

    if not is_metered(provider):
        return QuotaDecision(
            state=QuotaState.SUFFICIENT, provider=provider, model=model,
            required_requests=required_requests, available_requests=None,
            limit=None, used=0, day=day,
            reason=f"{provider} is not metered",
        )

    try:
        snapshot = await read_usage(session, provider, model or "", day)
    except Exception as exc:  # noqa: BLE001 - an unreadable ledger is not a green light
        log.error("quota.preflight_unreadable", provider=provider, error=str(exc)[:200])
        return QuotaDecision(
            state=QuotaState.UNKNOWN, provider=provider, model=model,
            required_requests=required_requests, available_requests=None,
            limit=None, used=0, day=day,
            reason=(
                "Could not read request accounting, so remaining quota is unknown. "
                "Blocking rather than risking a render that stops halfway."
            ),
        )

    if snapshot.exhausted:
        return QuotaDecision(
            state=QuotaState.INSUFFICIENT, provider=provider, model=model,
            required_requests=required_requests, available_requests=0,
            limit=snapshot.limit, used=snapshot.used, day=day,
            reason=(
                f"{provider} reported its daily quota exhausted on {day.isoformat()} "
                f"(resets at midnight Pacific)"
            ),
        )

    if snapshot.limit is None:
        # No cap configured. Either billing is on, or nobody has said what the
        # limit is — those need different answers, so they are different states.
        if settings.GEMINI_TTS_DAILY_REQUEST_LIMIT == 0:
            return QuotaDecision(
                state=QuotaState.SUFFICIENT, provider=provider, model=model,
                required_requests=required_requests, available_requests=None,
                limit=None, used=snapshot.used, day=day,
                reason="No daily cap configured (billing enabled)",
            )
        return QuotaDecision(
            state=QuotaState.UNKNOWN, provider=provider, model=model,
            required_requests=required_requests, available_requests=None,
            limit=None, used=snapshot.used, day=day,
            reason="Daily request limit is unknown for this provider",
        )

    available = snapshot.available or 0
    if available < required_requests:
        return QuotaDecision(
            state=QuotaState.INSUFFICIENT, provider=provider, model=model,
            required_requests=required_requests, available_requests=available,
            limit=snapshot.limit, used=snapshot.used, day=day,
            reason=(
                f"{provider} quota insufficient: required {required_requests} "
                f"requests, available {available} "
                f"({snapshot.used}/{snapshot.limit} used on {day.isoformat()}, "
                "resets at midnight Pacific)"
            ),
        )

    return QuotaDecision(
        state=QuotaState.SUFFICIENT, provider=provider, model=model,
        required_requests=required_requests, available_requests=available,
        limit=snapshot.limit, used=snapshot.used, day=day,
        reason=f"{available} of {snapshot.limit} requests available",
    )
