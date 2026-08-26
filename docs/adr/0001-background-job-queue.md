# ADR 0001 — Background Job Queue: Custom PostgreSQL vs Procrastinate

- **Status:** Accepted
- **Date:** 2026-08-24
- **Phase:** 0
- **Supersedes:** the provisional recommendation in `PHASE-1-ARCHITECTURE.md` §2.3 (confirms it)

## Context

Phase 1 §2.3 recommended a custom Postgres `SKIP LOCKED` queue with `procrastinate` as the
fallback. Before writing queue code, we evaluated both against our actual requirements.

Workload reality: tens of jobs/day, seconds-to-minutes each, single machine, single user.
Throughput is not a differentiator — **both options are ~3 orders of magnitude over-provisioned.**
So the decision rests entirely on fit and operational complexity.

## Evaluation

| Requirement | Procrastinate | Custom Postgres queue |
|---|---|---|
| Retries | ✅ Built-in `RetryStrategy` with backoff | ⚠️ We write it (~30 lines, pure function, unit-testable) |
| Idempotency | ✅ `queueing_lock` unique on non-terminal jobs | ✅ `idempotency_key` UNIQUE + `ON CONFLICT DO NOTHING` |
| Persisted history | ⚠️ Events exist in its own schema; retention is its policy | ✅ We own `job_event`, append-only, our retention |
| Timeouts | ⚠️ Heartbeat/stale detection exists but per-job timeout budgets are not first-class | ✅ Explicit `timeout_seconds` per job type + reaper |
| Scheduled jobs | ✅ Built-in periodic tasks | ✅ APScheduler → `enqueue()` with slot-derived idempotency key |
| **Domain linkage** | ❌ `project_id` lives inside `args` JSONB; no FK, no join | ✅ Real `project_id` column + FK + index |
| **Dashboard visibility** | ⚠️ Query its tables directly; schema is theirs and may change across versions | ✅ Our schema, our indexes, plain joins |
| **Transactional enqueue** | ⚠️ Possible, but requires threading its connector through the SQLAlchemy session | ✅ Same `AsyncSession`, same transaction, trivially |
| Operational complexity | ⚠️ **Two migration systems in one database** (Alembic + `procrastinate schema`) | ✅ One migration system |
| Correctness risk | ✅ Battle-tested | ⚠️ We own it (mitigated by tests) |

## Decision

**Custom PostgreSQL `SKIP LOCKED` queue.**

Three requirements decide it, and all three are architectural rather than convenience:

1. **Transactional enqueue.** `PHASE-1-ARCHITECTURE.md` §8.3 rule 4 requires that a state
   transition and the job it triggers commit atomically. With our own table this is the
   default behaviour of the caller's session. Anything else reintroduces the lost-work and
   duplicate-work windows the state machine exists to eliminate.
2. **Domain linkage and dashboard visibility.** The dashboard's primary query is
   "show me every job for this project, newest first". With our own table that is a join on
   an indexed FK. With Procrastinate it is `args->>'project_id'` against a schema we do not own.
3. **One migration system.** Procrastinate manages its schema with its own tooling. Running
   that alongside Alembic in the same database is a genuine ongoing operational cost, and it
   is exactly the kind of incidental complexity Phase 0 should refuse.

The instruction was to "choose the simpler reliable option". Procrastinate is simpler *to write*;
the custom queue is simpler *to operate and to reason about*, because everything about a job
lives in one schema under one migration tool. Over the life of this project, operating cost
dominates.

The correctness risk is real and is mitigated deliberately: the claim/retry/reap logic is
~200 lines, the backoff calculation is a pure function with unit tests, and integration tests
exercise concurrent claiming, retry-then-succeed, exhaustion-to-DEAD, timeout reaping, and
idempotent enqueue against a real PostgreSQL instance.

## Consequences

- We own queue correctness. Covered by `backend/tests/integration/test_queue.py`.
- No Redis and no message broker in the stack. Reconsider only on a concrete need
  (cross-process rate limiting, SSE fan-out), not on principle.
- Worker polls with jitter. Latency is ~1s, which is irrelevant for minutes-long media jobs.
  `LISTEN/NOTIFY` is a drop-in improvement later if it ever matters.
- If this proves wrong, migrating to Procrastinate means rewriting `app/jobs/queue.py` and
  `worker.py` only — job handlers are written against a `JobContext`, not against the queue.

## Revisit if

- Job volume exceeds ~10k/day, or
- We need multi-machine workers with complex routing, or
- Queue bugs consume more than a day of debugging in aggregate.
