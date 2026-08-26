"""Worker process.

Claims jobs, runs them with a concurrency limit, heartbeats while they run, and
shuts down without abandoning in-flight work.

Run with:  python -m app.jobs.worker
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import signal
import sys
import uuid

from app.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine, session_scope, wait_for_database
from app.jobs import queue
from app.jobs.registry import all_definitions, load_all_jobs
from app.jobs.runner import run_job

log = get_logger("worker")


class Worker:
    def __init__(
        self,
        worker_id: str | None = None,
        concurrency: int | None = None,
        job_types: list[str] | None = None,
    ) -> None:
        self.worker_id = worker_id or settings.WORKER_ID
        self.concurrency = concurrency or settings.WORKER_CONCURRENCY
        self.job_types = job_types if job_types is not None else settings.WORKER_JOB_TYPES
        self._running: set[asyncio.Task[None]] = set()
        self._stopping = asyncio.Event()

    # ------------------------------------------------------------ lifecycle ---
    async def start(self) -> bool:
        """Run until stopped. Returns False if it could not start at all."""
        load_all_jobs()
        log.info(
            "worker.starting",
            worker_id=self.worker_id,
            concurrency=self.concurrency,
            job_types=self.job_types or "ALL",
            registered_job_types=sorted(all_definitions()),
        )

        if not await wait_for_database():
            log.error(
                "worker.startup_aborted",
                reason="database unreachable",
                hint="check DATABASE_URL and that PostgreSQL is accepting connections",
            )
            return False

        # Anything this worker id left RUNNING belongs to a previous life.
        try:
            async with session_scope() as session:
                recovered = await queue.recover_worker_jobs(session, self.worker_id)
            if recovered:
                log.warning("worker.recovered_orphans", count=recovered)
        except Exception:
            # Recovery is best-effort: the reaper will pick these up anyway, so
            # a failure here must not stop the worker from doing useful work.
            log.exception("worker.recovery_failed")

        log.info("worker.started", worker_id=self.worker_id)
        try:
            await self._loop()
        finally:
            await self._drain()
            await dispose_engine()
            log.info("worker.stopped", worker_id=self.worker_id)
        return True

    def request_stop(self) -> None:
        if not self._stopping.is_set():
            log.info("worker.stop_requested", in_flight=len(self._running))
            self._stopping.set()

    # ----------------------------------------------------------------- loop ---
    async def _loop(self) -> None:
        while not self._stopping.is_set():
            if len(self._running) >= self.concurrency:
                await self._wait_for_slot()
                continue

            job_id = await self._claim_one()
            if job_id is None:
                await self._idle_sleep()
                continue

            task = asyncio.create_task(self._execute(job_id))
            self._running.add(task)
            task.add_done_callback(self._running.discard)

    async def _claim_one(self) -> uuid.UUID | None:
        try:
            async with session_scope() as session:
                job_obj = await queue.claim(session, self.worker_id, self.job_types)
                return job_obj.id if job_obj else None
        except Exception:
            log.exception("worker.claim_failed")
            await asyncio.sleep(min(5.0, settings.WORKER_POLL_INTERVAL_SECONDS * 5))
            return None

    async def _wait_for_slot(self) -> None:
        if not self._running:
            return
        await asyncio.wait(self._running, return_when=asyncio.FIRST_COMPLETED)

    async def _idle_sleep(self) -> None:
        jitter = random.uniform(0, settings.WORKER_POLL_JITTER_SECONDS)
        delay = settings.WORKER_POLL_INTERVAL_SECONDS + jitter
        with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
            await asyncio.wait_for(self._stopping.wait(), timeout=delay)

    # ------------------------------------------------------------- execution ---
    async def _execute(self, job_id: uuid.UUID) -> None:
        heartbeat_task = asyncio.create_task(self._heartbeat(job_id))
        try:
            status = await run_job(job_id, self.worker_id)
            log.debug("worker.job_finished", job_id=str(job_id), status=status)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker.job_crashed", job_id=str(job_id))
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

    async def _heartbeat(self, job_id: uuid.UUID) -> None:
        """Keep the job's liveness marker fresh so the reaper leaves it alone."""
        while True:
            await asyncio.sleep(settings.WORKER_HEARTBEAT_SECONDS)
            try:
                async with session_scope() as session:
                    still_ours = await queue.heartbeat(session, job_id, self.worker_id)
                if not still_ours:
                    # Reaped or requeued elsewhere. Stop heartbeating; the run
                    # itself will finish and record against a row we no longer own.
                    log.warning("worker.heartbeat_lost", job_id=str(job_id))
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("worker.heartbeat_failed", job_id=str(job_id), exc_info=True)

    async def _drain(self) -> None:
        if not self._running:
            return
        grace = settings.WORKER_SHUTDOWN_GRACE_SECONDS
        log.info("worker.draining", in_flight=len(self._running), grace_seconds=grace)
        _done, pending = await asyncio.wait(self._running, timeout=grace)
        if pending:
            # These stay RUNNING in the database; the reaper requeues them
            # rather than consuming a retry for an operator-initiated stop.
            log.warning("worker.drain_timeout", abandoned=len(pending))
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)


async def main() -> None:
    configure_logging(service="worker")
    worker = Worker()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:
            # Windows: add_signal_handler is unsupported on the Proactor loop.
            signal.signal(sig, lambda *_: worker.request_stop())

    started = await worker.start()
    # Non-zero exit tells Docker/systemd this was a real failure, without
    # dumping a stack trace for the ordinary "database not up yet" case.
    if not started:
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
