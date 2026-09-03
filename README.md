# SystemDecoded

> **Complex Technology. Decoded.**

An autonomous YouTube content operations system for a technology edutainment channel.

**Current status: Phase 2 — Content production. Complete.**
The system produces a finished, review-ready YouTube Short end to end: script →
local TTS narration → timings measured from that audio → word-aligned captions →
scene visuals rendered from HTML templates → FFmpeg composition → automated quality
gates → human review → publishing handoff. Plus everything from Phases 0–1:
PostgreSQL, migrations, FastAPI, a Postgres-backed job queue, worker, scheduler,
Next.js studio, and YouTube OAuth with encrypted tokens.

---

## Table of contents

- [What SystemDecoded is](#what-systemdecoded-is)
- [Business objective](#business-objective)
- [Architecture](#architecture)
- [Technology stack](#technology-stack)
- [Project structure](#project-structure)
- [Quick start](#quick-start)
- [Environment variables](#environment-variables)
- [Docker commands](#docker-commands)
- [Database and migrations](#database-and-migrations)
- [Backend](#backend)
- [Frontend](#frontend)
- [Worker](#worker)
- [Scheduler](#scheduler)
- [Job architecture](#job-architecture)
- [Media production](#media-production)
- [Testing](#testing)
- [Implementation status](#implementation-status)
- [Roadmap](#roadmap)
- [YouTube integration](#youtube-integration)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Documentation](#documentation)

---

## What SystemDecoded is

A content operations system that removes most of the labour of running a technical
edutainment channel, concentrates the remaining human judgment into two short decision
points per video, and accumulates a structured, evidence-linked memory of what works.

The target is **not** "videos produced with no human involvement". It is:

```
<= 10 minutes of human judgment per published video
 0 minutes of human labour on research assembly, timing, rendering,
   captioning, scheduling, analytics collection, or strategy bookkeeping
```

Autonomy is earned incrementally: every human approval gate carries a configurable
`autonomy_level` that can be flipped to automatic once evidence supports it, with no
redesign. Two constraints make full automation the wrong Phase 1 target — free local
LLMs cannot write hooks that hold an audience, and YouTube's inauthentic-content policy
explicitly penalises templated mass production. Both are addressed in
[docs/PHASE-1-ARCHITECTURE.md](docs/PHASE-1-ARCHITECTURE.md).

**Channel positioning**

| | |
|---|---|
| Name | SystemDecoded |
| Tagline | Complex Technology. Decoded. |
| Niche | AI + Technology Edutainment |
| Audience | English-speaking global, roughly 18–35, technically curious |
| Format | YouTube Shorts, ~20–45 seconds |
| Style | Complicated technology explained through short, highly visual stories |

---

## Business objective

Build a commercially valuable audience — not raw view count. The system optimises for
the metrics that actually lead to revenue:

| Revenue path | Leading indicator |
|---|---|
| AdSense / Premium | `estimatedMinutesWatched`, `averageViewPercentage` |
| Sponsorship / affiliate | `subscribersGained` per 1k views, Tier-1 audience share |
| SaaS partnerships / own product | subscriber quality and return behaviour |
| Long-term brand equity | publish consistency, non-templated variety |

---

## Architecture

Three processes, one database, one media volume, one codebase. No microservices, no
Kubernetes, no message broker.

```mermaid
graph TB
    subgraph Client
        UI[Next.js studio]
    end
    subgraph Backend["backend container"]
        API[FastAPI]
        SVC[Domain services]
    end
    subgraph Worker["worker container"]
        W[Job worker pool]
    end
    subgraph Sched["scheduler container"]
        APS[APScheduler triggers]
    end
    subgraph Data
        PG[(PostgreSQL)]
        FS[/media volume/]
    end

    UI --> API
    API --> SVC --> PG
    API -. enqueue .-> PG
    APS -. enqueue .-> PG
    W -. "claim (SKIP LOCKED)" .-> PG
    W --> FS
```

The system is three nested loops running at different frequencies:

```mermaid
graph LR
    A[Production loop<br/>continuous] --> B[Planning loop<br/>daily]
    B --> C[Strategy loop<br/>monthly]
    C --> B
    B --> A
```

Phase 0 implements the substrate all three run on. Full design:
[docs/PHASE-1-ARCHITECTURE.md](docs/PHASE-1-ARCHITECTURE.md).

### Layering

```
api/          HTTP only. Pydantic in, Pydantic out. No business logic.
services/     Domain logic. Owns transactions and state transitions.
jobs/         Job definitions. Each is a thin wrapper around a service call.
providers/    Swappable external capabilities behind interfaces (LOCAL/MANUAL/EXTERNAL_API).
integrations/ Concrete third-party clients (YouTube, web fetch).
models/       SQLAlchemy ORM.
core/         Errors, logging, clock, ids, security.
```

**Hard rule:** a job is never more than a thin wrapper around a service call. Every
pipeline step stays invokable synchronously from a test or the API, which is what makes
the system debuggable.

---

## Technology stack

| Concern | Choice | Why |
|---|---|---|
| Backend | Python 3.12, FastAPI | Async, typed, OpenAPI for free |
| ORM | SQLAlchemy 2.0 (async) | Mature, explicit |
| Database | PostgreSQL 16 | JSONB, `SKIP LOCKED`, partial indexes |
| Driver | psycopg 3 | One driver serves sync (Alembic) and async (app) |
| Migrations | Alembic | Standard |
| Job queue | **Custom, Postgres-backed** | See [ADR 0001](docs/adr/0001-background-job-queue.md) |
| Scheduling | APScheduler | Triggers only; the queue does the work |
| Logging | structlog | Structured, contextvar-bound |
| YouTube client | **httpx, direct REST** | See [ADR 0002](docs/adr/0002-youtube-client.md) — the official SDK is synchronous |
| Secrets | cryptography (Fernet) | OAuth tokens encrypted at rest |
| TTS | Kokoro-82M (`kokoro-onnx`) | Apache-2.0, CPU real-time, no torch |
| Alignment | faster-whisper `base.en` | Forced alignment for caption timing |
| Scene rendering | Playwright + Chromium | HTML/CSS/SVG templates driven frame-by-frame via `seek(t)` |
| Composition | FFmpeg | Composition and encoding only; concat demuxer, clean cuts |
| Sound design | numpy synthesis | Internally generated, so licensing stays clean |
| Frontend | Next.js 15, React 19, Tailwind 4 | App Router, server components |
| Packaging | Docker Compose | Identical topology locally and on a VPS |

**Not used, deliberately:** Redis, Celery, LangGraph, any vector database, any agent
framework, any paid AI API. Each exclusion is argued in the architecture document.

---

## Project structure

```
.
├── docker-compose.yml           # postgres, migrate, backend, worker, scheduler, frontend
├── .env.example                 # every setting, documented
├── README.md
├── docs/
│   ├── PHASE-1-ARCHITECTURE.md  # the technical contract
│   └── adr/
│       ├── 0001-background-job-queue.md
│       └── 0002-youtube-client.md
├── backend/
│   ├── pyproject.toml
│   ├── Dockerfile
│   ├── alembic.ini
│   ├── alembic/versions/        # 0001_foundation … 0003_content_production
│   ├── app/
│   │   ├── main.py              # FastAPI app, error handlers, lifespan
│   │   ├── config.py            # all environment configuration
│   │   ├── core/                # errors, logging, clock, uuid7
│   │   ├── db/                  # engine, session, declarative base
│   │   ├── models/              # BackgroundJob, JobEvent, Channel, YouTubeConnection
│   │   ├── schemas/             # Pydantic request/response
│   │   ├── api/routes/          # health, system, jobs, channel, youtube
│   │   ├── services/            # system_status, youtube_connection
│   │   ├── jobs/                # queue, runner, worker, scheduler, registry, tasks
│   │   ├── providers/           # (Phase 4) LLM/TTS/renderer adapters
│   │   └── integrations/youtube/ # oauth, data_api, error classification
│   └── tests/
│       ├── unit/                # no infrastructure required
│       └── integration/         # requires PostgreSQL
├── frontend/
│   ├── app/                     # layout, dashboard, YouTubePanel
│   └── lib/api.ts               # typed API client
├── scene_templates/             # (Phase 2) the channel's visual identity
└── media/                       # assets, renders, cache (volume-mounted)
```

---

## Quick start

**Requirements:** Docker Desktop (with its Linux engine running) and Docker Compose v2.

```bash
cp .env.example .env
docker compose up --build
```

That is the whole setup. The `migrate` service applies migrations before the backend,
worker and scheduler start, so no manual database step is needed.

| Service | URL |
|---|---|
| Studio UI | http://localhost:3030 |
| API docs | http://localhost:8080/docs |
| Liveness | http://localhost:8080/health/live |
| Readiness | http://localhost:8080/health/ready |

Ports are configurable via `BACKEND_PORT` / `FRONTEND_PORT` in `.env` (defaults above).
If you change them, also update `NEXT_PUBLIC_API_URL` and `CORS_ORIGINS` to match —
`NEXT_PUBLIC_API_URL` is what your browser uses to reach the API, and `CORS_ORIGINS`
must list the frontend's actual origin or the backend will reject its requests.

**Verify it works.** Open http://localhost:3030 and use the *Queue diagnostics* buttons.
`Retry then succeed` queues a job that fails twice and succeeds on the third attempt;
watch the attempt counter climb in the jobs table. Or from the terminal:

```bash
curl -X POST http://localhost:8080/api/v1/jobs \
  -H 'Content-Type: application/json' \
  -d '{"job_type":"system.ping","payload":{"hello":"world"}}'

curl http://localhost:8080/api/v1/jobs | python -m json.tool
```

### Running without Docker

```bash
# PostgreSQL must be reachable and a database must exist.
cd backend
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

export DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/systemdecoded
alembic upgrade head

uvicorn app.main:app --reload      # terminal 1
python -m app.jobs.worker          # terminal 2
python -m app.jobs.scheduler       # terminal 3

cd ../frontend && npm install && npm run dev   # terminal 4
```

---

## Environment variables

Every setting lives in [.env.example](.env.example) with inline documentation.
The defaults run the full local stack unedited.

| Group | Key variables |
|---|---|
| App | `APP_NAME`, `ENVIRONMENT`, `DEBUG` |
| Database | `DATABASE_URL`, `POSTGRES_*`, `DB_POOL_SIZE` |
| Logging | `LOG_LEVEL`, `LOG_FORMAT` (`console` \| `json`) |
| Worker | `WORKER_ID`, `WORKER_CONCURRENCY`, `WORKER_JOB_TYPES`, `WORKER_HEARTBEAT_SECONDS` |
| Queue | `JOB_DEFAULT_MAX_ATTEMPTS`, `JOB_DEFAULT_TIMEOUT_SECONDS`, `JOB_RETRY_BASE_SECONDS` |
| Scheduler | `SCHEDULER_ENABLED`, `SCHEDULER_TIMEZONE` |
| Secrets | `SECRETS_KEY` (encrypts OAuth tokens; required in production) |
| YouTube | `YOUTUBE_API_ENABLED`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` |
| LLM | `LLM_MECHANICAL_MODE`, `LLM_CREATIVE_MODE`, `LLM_LOCAL_*` |

**The application starts and runs fully with all YouTube and LLM values blank.** Those
endpoints report themselves as unconfigured rather than failing or faking data.

Inside Docker, `DATABASE_URL` uses the service hostname `postgres`. Running the backend
directly on your host, use `localhost`.

---

## Docker commands

```bash
docker compose up --build          # build and start everything
docker compose up -d               # start detached
docker compose down                # stop (keeps data)
docker compose down -v             # stop and DELETE the database volume

docker compose logs -f backend     # follow one service
docker compose logs -f worker scheduler

docker compose ps                  # status and health
docker compose restart worker

docker compose up -d postgres      # just the database (for host-side tests)
docker compose exec backend bash   # shell in the backend container
```

---

## Database and migrations

Schema changes are always Alembic migrations — never `create_all`.

```bash
# Inside Docker
docker compose run --rm migrate                                    # upgrade to head
docker compose exec backend alembic revision --autogenerate -m "add content_project"
docker compose exec backend alembic downgrade -1
docker compose exec backend alembic current
docker compose exec backend alembic history

# On the host (backend/ directory, venv active)
alembic upgrade head
```

**Conventions**

- **UUIDv7 primary keys** — time-ordered, so inserts stay local in the B-tree and
  "newest first" needs no extra index.
- **Enums are `VARCHAR`, not native PostgreSQL enums.** The workflow state machine gains
  states through Phases 2–8; native enums would make that a schema migration instead of a
  code change. Rationale in `app/models/enums.py`.
- **All timestamps are `timestamptz`, stored UTC.** Naive datetimes are a bug.
- **Every model must be imported in `app/models/__init__.py`** or Alembic cannot see it.

The initial migration seeds one `channel` row (deterministic UUID) so there is no
startup race and no bootstrap step.

Current head: `0003_content_production` — adds the content hierarchy: projects,
transitions, research, scripts, scenes, assets, renders, quality checks and publishing.

---

## Backend

FastAPI on `:8000`. Interactive docs at `/docs`.

| Endpoint | Purpose |
|---|---|
| `GET /health/live` | Liveness. Never touches the database. |
| `GET /health/ready` | Readiness: database + migrations applied. 503 when not ready. |
| `GET /api/v1/system/status` | Aggregated dashboard status |
| `GET /api/v1/system/info` | Build info and capability flags |
| `GET /api/v1/jobs` | List jobs (filter by status, type, project) |
| `GET /api/v1/jobs/types` | Registered job types with their retry policies |
| `POST /api/v1/jobs` | Enqueue a job |
| `GET /api/v1/jobs/{id}` | Job detail with full event history |
| `POST /api/v1/jobs/{id}/requeue` | Operator retry of a terminal job |
| `POST /api/v1/jobs/{id}/cancel` | Cancel a queued job |
| `GET /api/v1/channel` · `PATCH` | Read/update channel settings |
| `GET /api/v1/projects` | List content projects |
| `GET /api/v1/projects/{id}` | Full review payload: script, scenes, sources, QC, timeline |
| `GET /api/v1/projects/{id}/video` | Stream the rendered MP4 |
| `POST /api/v1/projects/{id}/produce` | Queue a production run |
| `POST /api/v1/projects/{id}/review` | Approve / request revision / reject |
| `POST /api/v1/projects/{id}/published` | Link a manually uploaded YouTube video |
| `GET /api/v1/youtube/status` | Connection status and health. Never returns tokens. |
| `GET /api/v1/youtube/oauth/start` | Begin OAuth (`?json=true` returns the URL instead of redirecting) |
| `GET /api/v1/youtube/oauth/callback` | Google's redirect target; bounces back to the studio |
| `POST /api/v1/youtube/sync` | Re-sync channel metadata from YouTube |
| `POST /api/v1/youtube/refresh` | Force an access-token refresh |
| `POST /api/v1/youtube/disconnect` | Revoke and delete the stored connection |

**Error envelope.** Every failure, from any layer, serialises identically:

```json
{
  "error": {
    "code": "not_found",
    "message": "Job 5f2c… not found",
    "detail": null,
    "request_id": "0f1e…"
  }
}
```

Every response carries an `X-Request-ID` header, and every log line emitted while
handling that request carries the same `request_id`.

---

## Frontend

Next.js 15 App Router on `:3000`. Phase 0 ships one screen — a dashboard showing system
health, channel configuration, job counts, a live job table, and buttons that exercise
the real queue.

The remaining screens (Projects, Review Queue, Ideas & Planner, Channel) arrive with the
data that makes them meaningful. Building empty screens teaches nothing.

```bash
cd frontend
npm install
npm run dev        # :3000
npm run typecheck
npm run build
```

---

## Worker

```bash
python -m app.jobs.worker
docker compose logs -f worker
```

Claims jobs, runs them with a concurrency limit, heartbeats while they run, and drains
in-flight work on shutdown. On startup it requeues anything its own worker id left
`RUNNING` — the signature of a previous crash.

The worker is built from `Dockerfile.worker`, the only image carrying FFmpeg,
Chromium and the speech models. The API and scheduler stay on the slim image.

Scale by running more worker containers with **distinct `WORKER_ID` values**.
`WORKER_JOB_TYPES` restricts which types a worker claims, which is how media rendering
moves to a dedicated worker in Phase 2 with no code change.

---

## Scheduler

```bash
python -m app.jobs.scheduler
```

APScheduler owns *when*; the queue owns *what*. Triggers never do work — they enqueue a
job and return. Every enqueue carries a slot-derived idempotency key, so a restart or an
accidental second scheduler cannot double-enqueue the same tick.

| Job | Cadence |
|---|---|
| `system.reap_stale_jobs` | every 2 minutes |
| `system.heartbeat` | every 5 minutes |
| `system.purge_job_history` | daily at 04:00 |
| `youtube.refresh_tokens` | every 30 minutes (no-ops unless near expiry) |
| `youtube.sync_channel` | daily at 05:00 |
| `youtube.purge_oauth_states` | daily at 04:30 |

The remaining Phase 2–7 schedule (publish reconciliation, analytics collection,
content planner) is added in the phase that makes each one real.

---

## Job architecture

A custom PostgreSQL queue, chosen over Procrastinate after evaluation —
see [ADR 0001](docs/adr/0001-background-job-queue.md). Three requirements decided it:
transactional enqueue, real domain linkage (`project_id` as an indexed column, not JSONB),
and one migration system instead of two.

### Lifecycle

```mermaid
stateDiagram-v2
    [*] --> QUEUED : enqueue()
    QUEUED --> RUNNING : claim() - SKIP LOCKED, spends an attempt
    RUNNING --> SUCCEEDED : handler returns
    RUNNING --> QUEUED : retryable failure, attempts remain
    RUNNING --> FAILED : terminal error OR attempts exhausted
    RUNNING --> QUEUED : reaped (worker died)
    QUEUED --> CANCELLED : operator
    FAILED --> QUEUED : operator requeue
    SUCCEEDED --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
```

Five states, not six. A job awaiting retry is simply claimable again, so it is `QUEUED`
with `attempt > 0`. `FAILED` therefore means exactly one thing: terminal, no further
automatic attempts. Full attempt history lives in `job_event`.

### Guarantees

| Requirement | Mechanism |
|---|---|
| Atomic claim | `SELECT … FOR UPDATE SKIP LOCKED` — N workers never collide |
| Retries | Exponential backoff with jitter; jitter prevents synchronised retry storms |
| Timeouts | Per-job `timeout_seconds`; heartbeat + reaper recover dead workers |
| Idempotency | Unique `idempotency_key`; a duplicate enqueue is a no-op |
| History | Append-only `job_event`; a retry never overwrites why the last attempt failed |
| Recovery | Worker requeues its own orphans on startup |
| Transactional enqueue | Enqueue commits in the caller's transaction |

### Retry classification — important

An **unrecognised exception is terminal by default.** Blind retries burn API quota and,
in the publishing path, risk duplicate YouTube uploads. Each job declares what it
considers retryable:

```python
@job("system.flaky", max_attempts=3, retry_on=(RetryableError,))
async def flaky(ctx: JobContext) -> dict:
    ...
```

`TerminalError` always wins, even if listed in `retry_on`.

### Writing a job

```python
from app.jobs.registry import job
from app.jobs.context import JobContext

@job("content.research", max_attempts=3, timeout_seconds=600)
async def research(ctx: JobContext) -> dict:
    project_id = ctx.require("project_id")     # missing key = terminal, not retried
    ctx.logger.info("research.started", project_id=project_id)
    # ctx.session commits together with the job's success record
    return {"sources_found": 7}
```

Then import it from `load_all_jobs()` in `app/jobs/registry.py`. The job name is
persisted in the database, so renaming one is a data migration, not a refactor.

---

## Testing

```bash
cd backend

pytest tests/unit                 # no infrastructure needed
pytest                            # all tests; integration needs PostgreSQL
pytest -v -k queue                # one area
pytest --tb=short -q

# The media test needs FFmpeg, Chromium and the speech models, so it runs in
# the worker container and is skipped everywhere else.
docker compose exec -e TEST_DATABASE_URL=postgresql+psycopg://systemdecoded:systemdecoded@postgres:5432/systemdecoded_test   worker pytest tests/ -m media

# Integration tests need a database. Either:
docker compose up -d postgres
# or point them somewhere else:
export TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/systemdecoded_test
```

Integration tests **create their own `systemdecoded_test` database and run the real
Alembic migrations against it** — so the schema under test is the schema that ships, and
every run exercises the migration. They are skipped with a clear reason, not failed, when
no database is reachable.

Coverage of the foundation:

| Area | What is asserted |
|---|---|
| Backoff | Exponential growth, ceiling, jitter bounds, monotonicity, input validation |
| UUIDv7 | Version/variant bits, uniqueness, time ordering, timestamp roundtrip |
| Retry classification | Terminal-by-default, `TerminalError` override, custom retry sets |
| Queue | Enqueue, idempotency, priority, `run_after`, type filtering, **concurrent claims never collide** |
| Failure handling | Retry with backoff, attempt exhaustion, terminal errors skipping remaining attempts |
| Recovery | Stale-heartbeat reaping, worker-restart orphan recovery, operator requeue/cancel |
| Runner | Success, retry-then-succeed, timeouts, missing handlers, **handler writes roll back on failure** |
| API | Health, status, job CRUD, error envelope shape, channel seed, YouTube honesty |
| Captions | Chunking on sentences/pauses/limits, ASS structure, per-word highlighting, brace escaping |
| State machine | No unreachable states, no dead ends, review cannot be skipped, FAILED must be retriaged |
| Production | Seed integrity, every claim sourced, templates exist, assets declare provenance |
| Publishing | MANUAL_HANDOFF default, idempotent packages, one live job per project, idempotent reconciliation |
| **Real render** | **`-m media`: Kokoro + Whisper + Chromium + FFmpeg produce an actual 1080×1920 MP4** |

---

## Implementation status

### Phase 0 — Foundation ✅

| Item | Status |
|---|---|
| Project structure | ✅ |
| FastAPI backend | ✅ |
| Next.js frontend | ✅ dashboard |
| PostgreSQL + SQLAlchemy 2.0 async | ✅ |
| Alembic migrations | ✅ `0001_foundation` |
| Configuration system | ✅ `app/config.py` + `.env.example` |
| Background job infrastructure | ✅ custom Postgres queue |
| Worker process | ✅ concurrency, heartbeat, graceful drain |
| Scheduler process | ✅ APScheduler, idempotent enqueue |
| Structured logging | ✅ structlog, request/job correlation |
| Health / readiness endpoints | ✅ |
| Error handling foundation | ✅ single envelope, retry classification |
| Dockerfiles + Compose | ✅ |
| Foundation database models | ✅ `BackgroundJob`, `JobEvent`, `Channel`, `AppSetting` |
| Automated tests | ✅ unit + integration |
| README + architecture docs | ✅ |

### Phase 1 — YouTube connection ✅

| Item | Status |
|---|---|
| OAuth 2.0 authorization-code flow with PKCE (S256) | ✅ |
| Single-use, server-side, expiring `state` | ✅ |
| Tokens encrypted at rest (Fernet) | ✅ |
| Automatic channel metadata sync from `channels.list(mine=true)` | ✅ |
| Uploads playlist ID captured for Phase 2 reconciliation | ✅ |
| Scheduled token refresh + `invalid_grant` handling | ✅ |
| Partial-scope detection | ✅ |
| Connect / sync / refresh / disconnect UI | ✅ |

### Not built yet — and honestly labelled in the UI

Analytics · content pipeline · LLM providers · media production ·
planner · experiments · insights.

---

## Roadmap

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **0** ✅ | Foundation | A job runs, retries, times out, and appears in history |
| **1** ✅ | YouTube connection | Channel connects; metadata auto-populates; token survives refresh |
| **2** ✅ | One real video, end to end | **One real Short published on the real channel** |
| **3** | Analytics ingestion | Age-bucketed snapshots landing daily |
| **4** | LLM-assisted generation | A script produced by the pipeline with one paste step |
| **5** | Research & quality gates | Every claim traceable to a cited source |
| **6** | Autonomous planner | A 5-video backlog maintained with no "generate" click |
| **7** | Learning loop | First evidence-backed strategy change, correctly sample-gated |
| **8** | Progressive autonomy | Idea → ready with no human touch |

**Phase 2 is the milestone that matters.** Everything before it is scaffolding;
everything after is leverage. Get a real video onto the real channel before building
generation intelligence — it surfaces a dozen practical problems no design solves in
advance.

---

## YouTube integration

**Implemented in Phase 1.** OAuth 2.0 authorization-code flow with PKCE, encrypted token
storage, automatic channel sync, and scheduled token refresh.

### Setup

1. Google Cloud project with **YouTube Data API v3** and **YouTube Analytics API** enabled.
2. An **OAuth 2.0 Client ID**, type *Web application* → `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`.
3. Consent screen set to **External** + **In Production** (see the 7-day warning below).
4. Register the redirect URI **exactly**, including the port:
   `http://localhost:8080/api/v1/youtube/oauth/callback`
5. Generate a `SECRETS_KEY` — without it the app refuses to store tokens:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
6. Set `YOUTUBE_API_ENABLED=true`, then **recreate** the containers so they pick up `.env`:
   ```bash
   docker compose up -d --force-recreate backend worker scheduler
   ```
   `docker compose restart` does **not** re-read `.env`.

Then open the studio and click **Connect YouTube Channel**.

### Flow

```mermaid
sequenceDiagram
    participant U as You
    participant S as Studio
    participant B as Backend
    participant G as Google

    U->>S: Click "Connect YouTube Channel"
    S->>B: GET /youtube/oauth/start?json=true
    B->>B: generate state + PKCE verifier, store server-side
    B-->>S: authorization_url
    S->>G: browser navigates to consent screen
    G-->>B: GET /youtube/oauth/callback?code&state
    B->>B: consume state (single-use), verify expiry
    B->>G: exchange code + code_verifier (server-side)
    G-->>B: access + refresh tokens
    B->>B: encrypt both, store
    B->>G: channels.list(mine=true)
    G-->>B: channel id, title, handle, statistics, uploads playlist
    B-->>S: redirect to /?youtube=connected
```

### Security properties

| Property | Why |
|---|---|
| PKCE (S256) | An intercepted authorization code is useless without the verifier |
| `state` server-side, single-use, 15-minute TTL | Nothing that can set cookies can forge a callback; replay is rejected |
| Code exchange server-side only | The client secret never reaches the browser |
| Tokens encrypted at rest (Fernet) | A database dump alone yields no working credentials |
| `access_type=offline` + `prompt=consent` | Guarantees a refresh token, so background jobs keep working |
| Tokens never logged or returned | `/status` is safe to poll and safe to screenshot |

### Connection health

Token loss is an **expected event, not an error path**. The connection carries an explicit
status (`ACTIVE` / `EXPIRED` / `REVOKED` / `ERROR`), the dashboard surfaces it, and
`invalid_grant` moves it to `EXPIRED` rather than crash-looping a job. One click reconnects.

`youtube.refresh_tokens` runs every 30 minutes and no-ops unless the token is actually near
expiry, so it costs nothing when idle.

### Two constraints that shape the design

**Refresh tokens expire after 7 days while the consent screen is in Testing.** Setting it
to *In Production* removes that; unverified is fine for a single user — you click through a
warning screen. Set `GOOGLE_CONSENT_PUBLISHING_STATUS=production` once you have, and the
dashboard warning disappears. The system treats token loss as visible, recoverable state
regardless.

**API uploads from an unaudited project are permanently locked to private, with no
appeal.** Videos must be re-uploaded via a verified project or through YouTube itself. So
V1 publishing is `MANUAL_HANDOFF`: the system produces the finished MP4 and metadata, you
upload in ~60 seconds via Studio, and a reconciliation job matches the video back to the
project by reading the uploads playlist. Reads are unrestricted, so analytics and the
learning loop remain fully automatic.

---

## Media production

Phase 2 turns an approved script into a finished, review-ready Short. Every stage
persists inspectable output, so a bad video can always be traced to the stage that
caused it.

```mermaid
graph LR
    A[Script + scenes] --> B[Segment into clauses<br/>app.services.prosody]
    B --> C[Kokoro TTS<br/>per segment]
    C --> D[MEASURE real<br/>audio durations]
    D --> E[Scene timings]
    E --> F[faster-whisper<br/>per-scene alignment]
    F --> G[ASS captions]
    E --> H[Playwright renders<br/>every frame at 30fps]
    H --> I[FFmpeg concat<br/>clean cuts]
    G --> J[Burn captions<br/>+ SFX + loudnorm]
    I --> J
    J --> K[1080x1920 MP4]
    K --> L[Quality gates]
    L --> M[Human review]
```

**Step D is the one that matters.** Scene timings are measured from the generated
audio, never estimated from word counts. Guessing produces drift that compounds
across a 30-second video and is miserable to debug afterwards.

**Narration is synthesized per clause, not per paragraph.** Feeding a whole scene to
the model produces identical cadence for every sentence, which is most of what makes
synthetic narration sound synthetic. `app/services/prosody.py` splits narration into
spoken units and assigns each a pause by *intent* — clause (~140ms), sentence
(~280ms), beat (~420ms), reveal (~500ms) — so emphasis lands where the script means
it to.

### Local dependencies

All of it is free and runs on CPU. None of it lives in the API image — only the
worker carries the media stack (`backend/Dockerfile.worker`, ~3GB).

| Tool | Role | Notes |
|---|---|---|
| **Kokoro-82M** (`kokoro-onnx`) | Narration | Apache-2.0, CPU real-time. `kokoro-onnx` rather than `kokoro` — the latter pulls in torch (~2GB) |
| **faster-whisper** (`base.en`) | Forced alignment | We know the words; we need their position in audio we generated |
| **Playwright + Chromium** | Scene rendering | Drives each template's `seek(t)` once per output frame — real motion, not a Ken Burns push |
| **FFmpeg** | Composition, captions, audio | Composition and encoding only — visual design lives in the templates |
| **Internally generated SFX** | Sound design | Synthesised with numpy (`app/services/sfx.py`); no third-party audio, so licensing stays trivially clean |

Model weights are **not** baked into the image. They download once into
`media/cache/models/` (a persisted volume) on first run, so the image stays lean and
changing a model needs no rebuild. First production run therefore takes ~2 minutes
longer than later ones.

```bash
docker compose build worker      # ~3GB, only needed when deps change
docker compose up -d worker
```

### Scene templates

`scene_templates/` is the channel's visual identity and sits at the repo root
rather than inside `backend/`, because it is a design asset iterated in a browser.
It is mounted into the container, so editing a template needs **no rebuild** — just
re-run production.

```
scene_templates/
├── _base/
│   ├── tokens.css     # the design system: palette, type scale, safe area
│   ├── scene.js       # props loading + deterministic seek(t)
│   └── fonts/         # Inter, bundled locally (no network at render time)
├── code_reveal/       # a single focal object — hooks and payoffs
├── diagram_flow/      # A → B → C chains; links can be marked `blocked`
└── parallel_compute/  # two independent columns reaching the same answer
```

**Preview any template in a browser** — this is the whole point of the HTML approach:

```
scene_templates/code_reveal/index.html?safe=1&t=1
```

- `?t=0..1` places the scene at a point in its reveal (the same function the renderer drives)
- `?safe=1` outlines the Shorts safe area
- `?props=<base64-json>` injects real props; with none, each template renders its own demo props

Adding a template is: create `scene_templates/<id>/index.html`, implement
`window.build(props)` and `window.draw(t)`, then reference `<id>` as a scene's
`template_id`. Nothing else changes.

`draw(t)` is the animation contract. It must be **deterministic** — the same `t`
always produces the same pixels, with no `requestAnimationFrame` and no `Date.now()`
— because the renderer calls it once per output frame. Animation should demonstrate
the mechanism being explained: a signal that travels and is cut, two columns
resolving in step, a counter rolling over. Decoration for its own sake is worse than
none.

### Visual rules

These are deliberate constraints, not preferences:

- **One accent colour.** Electric cyan on deep navy-black. One accent reads as
  premium; three read as a template.
- **No real logos, product screenshots or scraped UI.** Generic abstracted mockups
  only. Trademark exposure on a channel whose purpose is monetization is not worth it.
- **Safe area is enforced, not eyeballed.** Scene artwork stops at
  `--content-bottom`; the band below it is reserved for burned-in captions, so
  graphics and captions can never overlap. QC blocks renders that violate it.
- **Captured keyframes must be visually final.** Reveal animations run at a high
  opacity floor — a frame captured mid-animation renders as washed out.

### Running a production job

```bash
# Seed the first project (one-off, Phase 2 only)
docker compose exec backend python -c "
import asyncio
from app.db.session import session_scope
from app.services.seed_first_video import seed_first_video

async def main():
    async with session_scope() as s:
        print((await seed_first_video(s)).id)

asyncio.run(main())
"

# Approve the script, then queue a production run
curl -X POST localhost:8080/api/v1/projects/<id>/approve-script
curl -X POST localhost:8080/api/v1/projects/<id>/produce
```

Watch it run:

```bash
docker compose logs -f worker
```

Then open **http://localhost:3030/review** to watch the video with the Shorts
safe-area overlay, read the script and cited sources, inspect the QC report, and
approve or send it back.

### Where media lives

```
media/
├── cache/models/                    # Kokoro + Whisper weights (downloaded once)
└── renders/<project-id>/
    ├── audio/scene_NN.wav           # per-scene narration
    ├── audio/narration.wav          # concatenated track
    ├── frames/scene_NN_kN.png       # rendered scene keyframes
    ├── captions.ass                 # word-timed captions
    ├── _work/                       # per-scene clips (kept for debugging)
    └── systemdecoded_XXXXXXXX.mp4   # the final video
```

Intermediates are deliberately kept. When a video looks wrong, the per-scene clips
and frames are on disk and playable, which is far faster than re-deriving what a
filter graph did.

### Quality gates

A render must pass before it reaches human review. Everything is measured off the
actual file with `ffprobe` — a QC that trusts the pipeline cannot catch the pipeline
being wrong.

| Blocking | Warning |
|---|---|
| File exists · 1080×1920 · duration in band · **video stream covers the audio** · audio present · no clipping · captions present · all scenes rendered · timings measured from audio · no long silences · safe area respected · every asset licensed · **audio ends cleanly** · **final frame decodes** · factual sources recorded | Loudness far from −14 LUFS · closing hold shorter than 0.5s |

`video_covers_audio` earns its place: container duration follows the *longest* stream,
so a video track that ends early leaves the file looking the right length while the
picture has already run out. Nothing else catches it.

A failing render moves the project to `NEEDS_REVISION` with the blocking issues
attached, rather than silently producing a broken video.

### Publishing

`MANUAL_HANDOFF` is the default and, for now, the only mode that can grow the
channel — API uploads from an unaudited Google Cloud project are permanently locked
to private with no appeal (ARCH §3.1).

1. Approve the video in the Review UI.
2. The system builds a package: MP4 + title + description with cited sources + tags
   + synthetic-media disclosure + publishing notes.
3. Upload via YouTube Studio (~60 seconds).
4. Paste the video ID into the Review UI, and the system links it back to the project.

---

## Troubleshooting

**`docker compose up` fails: cannot connect to the Docker daemon**
Docker Desktop's Linux engine is not running. Start Docker Desktop and wait for the whale
icon to stop animating. On Windows this also requires WSL 2 — check `wsl --list --verbose`
shows a `docker-desktop` distro. If it is missing, Docker Desktop has not completed
first-run setup; open it and follow the prompts.

**Backend exits with `connection refused` on startup**
The `migrate` service must succeed first. `docker compose logs migrate`. If PostgreSQL is
still initialising, `docker compose up` again — the healthcheck gates the dependency.

**Tests skip with "PostgreSQL not reachable"**
Expected without a database. `docker compose up -d postgres`, or set `TEST_DATABASE_URL`.

**`alembic upgrade head` says "Target database is not up to date"**
`alembic current` then `alembic history`. Locally, `docker compose down -v` wipes the
volume for a clean start — **this deletes all data**.

**Jobs stay `QUEUED` and never run**
The worker is not running or is filtering them out. `docker compose logs worker` and check
`WORKER_JOB_TYPES` is empty or includes the type.

**Jobs stuck in `RUNNING`**
A worker died mid-job. The reaper requeues them within ~2 minutes. Force it with
`POST /api/v1/jobs` for `system.reap_stale_jobs`.

**Windows: `Psycopg cannot use the 'ProactorEventLoop' to run in async mode`**
Already handled — `backend/app/__init__.py` switches the process to
`WindowsSelectorEventLoopPolicy` on import, because psycopg 3's async mode cannot run on
Windows' default event loop. If you see this error, something is running async database
code without importing the `app` package first. Import `app` (or call
`app._use_selector_event_loop_on_windows()`) before creating the loop. No effect on Linux,
so the Docker path never hits this.

**`password authentication failed for user "systemdecoded"`**
You are pointing at a PostgreSQL instance that has no `systemdecoded` role — usually a
pre-existing local install rather than the Compose one. Either use the Compose database
(`docker compose up -d postgres`) or create the role and database yourself:

```sql
CREATE ROLE systemdecoded LOGIN PASSWORD 'systemdecoded';
CREATE DATABASE systemdecoded OWNER systemdecoded;
CREATE DATABASE systemdecoded_test OWNER systemdecoded;
```

**Production job fails with `Scene N clip failed` / `Invalid argument`**
An FFmpeg filter-graph error. The per-scene clips in
`media/renders/<project-id>/_work/` are kept precisely for this — play them to see
which scene is wrong. Filter chains must be `;`-separated; joining them bare makes
FFmpeg read `[f0][1:v]` as a link label and reject the whole graph.

**First production run is slow / stalls at `alignment.model_loading`**
Kokoro and Whisper weights download on first use into `media/cache/models/`
(~500MB total). Later runs reuse them. `docker compose logs -f worker` shows progress.

**Rendered scenes look washed out**
A keyframe was captured mid-reveal. Scene templates must keep their opacity floor
near 1.0 so every captured still is visually final — the animation exists for
future frame-sequence capture, not for the stills path.

**Text is covered by the Shorts UI on a real phone**
Check it in the Review UI with the safe-area overlay enabled. Scene artwork must
stop at `--content-bottom` (600px); the band below is reserved for captions.

**OAuth fails with `redirect_uri_mismatch`**
The URI in `.env` must match one registered in Google Cloud Console byte for byte —
scheme, host, port and path. Check what the backend actually sends:
`curl -s localhost:8080/api/v1/youtube/status` and look at `redirect_uri`. The backend is
published on **8080**, so the URI must say 8080, not 8000.

**Connected, then `EXPIRED` about a week later**
The OAuth consent screen is still in *Testing*, where Google expires refresh tokens after
7 days. Set it to *In Production*, then set `GOOGLE_CONSENT_PUBLISHING_STATUS=production`.

**`.env` changes have no effect**
`docker compose restart` reuses the existing container environment. Use
`docker compose up -d --force-recreate backend worker scheduler`.

**`SECRETS_KEY is unset` in config problems**
Generate one and put it in `.env`; the app refuses to store OAuth tokens unencrypted:
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

**Frontend shows "Backend unreachable"**
`NEXT_PUBLIC_API_URL` must be reachable from your **browser**, not from inside the
container — `http://localhost:8080`, not `http://backend:8000`.

**Port already in use**
Change `POSTGRES_PORT`, `BACKEND_PORT` or `FRONTEND_PORT` in `.env`.

---

## Known limitations

- **Rendering is slow**: capturing ~1000 frames through Chromium takes ~4 minutes per
  video on CPU. Fine at 5 Shorts/week; it would need attention at higher volume.
- **Script writing is manual in Phase 2.** The LLM providers arrive in Phase 4;
  `seed_first_video.py` is a one-off seeder, not a content generator.
- **No background music.** Deliberate: a bed would compete with narration, and
  internally generated interface tones fit the brand better. Two subtle cues only.
- **No authentication.** Single-user, intended for localhost or a private VPS behind a
  reverse proxy. Do not expose it to the internet as-is.
- **`project_id` on `background_job` has no foreign key yet** — `content_project` does not
  exist until Phase 2, which adds the constraint.
- **Worker polls** rather than using `LISTEN/NOTIFY`. ~1s latency, irrelevant for
  minutes-long media jobs. A drop-in improvement if it ever matters.
- **No backups configured.** On a VPS, `pg_dump` nightly and sync `media/`. The database
  is the only genuinely irreplaceable asset.
- **`SECRETS_KEY` must be set** for the YouTube integration; the app refuses to store
  tokens unencrypted and reports it as a config problem.

---

## Documentation

| Document | Contents |
|---|---|
| [docs/PHASE-1-ARCHITECTURE.md](docs/PHASE-1-ARCHITECTURE.md) | Product vision, challenged assumptions, verified API constraints, full database design, state machine, planner, analytics loop, media pipeline, risks |
| [docs/adr/0001-background-job-queue.md](docs/adr/0001-background-job-queue.md) | Procrastinate vs custom Postgres queue |
| [docs/adr/0002-youtube-client.md](docs/adr/0002-youtube-client.md) | httpx vs google-api-python-client |

Keep this README current. Update it in the same change that alters architecture, not
afterwards.
