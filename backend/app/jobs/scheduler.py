"""Scheduler process.

APScheduler owns *when*; the queue owns *what*. Triggers do no work themselves —
they enqueue a job and return immediately. That keeps scheduling cheap, gives
every periodic run the same retry/timeout/history treatment as any other job,
and means the scheduler can be restarted at any moment without losing work.

Every enqueue carries a slot-derived idempotency key, so a scheduler restart or
an accidental second scheduler cannot double-enqueue the same tick
(`PHASE-1-ARCHITECTURE.md` §9.3).

Run with:  python -m app.jobs.scheduler
"""

from __future__ import annotations

import asyncio
import signal
import sys
from dataclasses import dataclass

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.core.clock import utcnow
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine, session_scope
from app.jobs import queue
from app.jobs.registry import load_all_jobs

log = get_logger("scheduler")


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    job_type: str
    trigger: IntervalTrigger | CronTrigger
    slot_seconds: int
    description: str


def active_schedule() -> list[ScheduledJob]:
    """Periodic jobs for the phases implemented so far.

    ARCH §9.3 lists the full Phase 1-7 schedule. Entries are added in the phase
    that makes them real, not stubbed ahead of time — publish reconciliation,
    analytics collection and the content planner are still absent.
    """
    return [
        ScheduledJob(
            job_type="system.reap_stale_jobs",
            trigger=IntervalTrigger(minutes=2),
            slot_seconds=120,
            description="Requeue jobs whose worker died.",
        ),
        ScheduledJob(
            job_type="system.heartbeat",
            trigger=IntervalTrigger(minutes=5),
            slot_seconds=300,
            description="Prove the scheduler -> queue -> worker path is live.",
        ),
        ScheduledJob(
            job_type="system.purge_job_history",
            trigger=CronTrigger(hour=4, minute=0),
            slot_seconds=86_400,
            description="Trim job history past the retention window.",
        ),
        # --- Phase 1: YouTube ---
        # Hourly, while Google's access tokens last ~1 hour. The job itself
        # no-ops unless the token is actually near expiry, so this is cheap.
        ScheduledJob(
            job_type="youtube.refresh_tokens",
            trigger=IntervalTrigger(minutes=30),
            slot_seconds=1_800,
            description="Refresh the YouTube access token before it expires.",
        ),
        ScheduledJob(
            job_type="youtube.sync_channel",
            trigger=CronTrigger(hour=5, minute=0),
            slot_seconds=86_400,
            description="Re-sync channel metadata and statistics.",
        ),
        ScheduledJob(
            job_type="youtube.purge_oauth_states",
            trigger=CronTrigger(hour=4, minute=30),
            slot_seconds=86_400,
            description="Delete expired OAuth state rows.",
        ),
    ]


def _slot_key(job_type: str, slot_seconds: int) -> str:
    """Idempotency key naming the time slot this tick belongs to."""
    slot = int(utcnow().timestamp()) // slot_seconds * slot_seconds
    return f"sched:{job_type}:{slot}"


async def enqueue_scheduled(job_type: str, slot_seconds: int) -> None:
    try:
        async with session_scope() as session:
            job_obj, created = await queue.enqueue(
                session,
                job_type,
                {"scheduled": True},
                idempotency_key=_slot_key(job_type, slot_seconds),
            )
        if created:
            log.info("scheduler.enqueued", job_type=job_type, job_id=str(job_obj.id))
        else:
            log.debug("scheduler.already_enqueued", job_type=job_type)
    except Exception:
        log.exception("scheduler.enqueue_failed", job_type=job_type)


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.SCHEDULER_TIMEZONE)
    for entry in active_schedule():
        scheduler.add_job(
            enqueue_scheduled,
            trigger=entry.trigger,
            args=[entry.job_type, entry.slot_seconds],
            id=entry.job_type,
            name=entry.description,
            replace_existing=True,
            max_instances=1,
            coalesce=True,  # a backlog of missed ticks collapses to one
            misfire_grace_time=60,
        )
    return scheduler


async def main() -> None:
    configure_logging(service="scheduler")
    load_all_jobs()

    if not settings.SCHEDULER_ENABLED:
        log.warning("scheduler.disabled_by_config")
        return

    scheduler = build_scheduler()
    stopping = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopping.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stopping.set())

    scheduler.start()
    log.info(
        "scheduler.started",
        timezone=settings.SCHEDULER_TIMEZONE,
        jobs=[j.id for j in scheduler.get_jobs()],
    )
    try:
        await stopping.wait()
    finally:
        log.info("scheduler.stopping")
        scheduler.shutdown(wait=False)
        await dispose_engine()
        log.info("scheduler.stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
