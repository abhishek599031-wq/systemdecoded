# ADR 0002 — YouTube API client: httpx vs google-api-python-client

- **Status:** Accepted
- **Date:** 2026-08-26
- **Phase:** 1

## Context

Phase 1 needs OAuth 2.0 (authorization code exchange, refresh, revoke) and one
YouTube Data API call (`channels.list(mine=true)`). Phase 3 adds YouTube Analytics
`reports.query`. The obvious default is Google's own `google-api-python-client` plus
`google-auth-oauthlib`.

## The problem with the official SDK here

**It is synchronous.** `google-api-python-client` is built on `httplib2` and
`google-auth`'s transport layer is blocking. This entire backend is asyncio —
FastAPI, SQLAlchemy async, and a worker that runs jobs concurrently on one event
loop. A blocking HTTP call inside a job handler stalls every other coroutine on
that loop, including the worker's own heartbeat, which is what stops the reaper
from requeueing the job (`PHASE-1-ARCHITECTURE.md` §9.2).

Working around that means wrapping every call in `run_in_executor` or
`asyncio.to_thread`, which adds a thread pool, makes cancellation and timeouts
harder to reason about, and defeats the point of the async stack.

The SDK's other big selling points do not apply at our scale:

- **Discovery-document API surface** — we call three endpoints, all documented REST.
- **Batching** — irrelevant for one channel.
- **Credential storage** — we deliberately store tokens ourselves, encrypted with
  our own key (`app/core/crypto.py`); the SDK's file-based credential stores are
  the wrong shape for a server-side, database-backed, multi-process system.

## Options

- **A — `google-api-python-client` + `google-auth-oauthlib`.** Official, well
  documented. Synchronous; needs thread offloading; brings a large dependency tree
  (`httplib2`, `uritemplate`, `pyasn1`, `protobuf`-adjacent packages).
- **B — `httpx` directly against the documented REST + OAuth endpoints.** Async
  native, no new dependencies (httpx is already in the stack), full control over
  timeouts, error classification and retry semantics.
- **C — `authlib`.** Async-capable OAuth client. A real dependency for something we
  can express in ~150 lines, and its abstractions still leave us writing the
  YouTube calls by hand.

## Decision

**Option B — `httpx` directly.**

Reasons, in order of weight:

1. **Async correctness.** Every YouTube call happens inside a job handler or a
   request handler on the shared event loop. A blocking client is an actual
   reliability bug in this architecture, not a style preference.
2. **Explicit error classification.** The queue distinguishes retryable from
   terminal failures, and unrecognised exceptions are terminal by default
   (ARCH §9.2). Owning the HTTP layer lets us map `invalid_grant` to a terminal
   connection-expiry, and a 503 to a retryable one, precisely. The SDK raises
   `HttpError` for nearly everything, so we would be parsing it back apart anyway.
3. **Zero new dependencies.** httpx is already used and already in the image.
4. **The surface is genuinely small.** OAuth authorization-code flow with PKCE is
   four HTTP calls, all stable and specified. This is not a case where an SDK is
   saving meaningful work.

## Consequences

- We own OAuth correctness. Mitigated by: PKCE (S256), single-use server-side
  `state`, exact-match `redirect_uri`, and tests covering the callback, refresh and
  `invalid_grant` paths against a mocked token endpoint.
- We track Google's API by hand. Low risk: these endpoints are versioned and stable,
  and the architecture document records the verified quota and behaviour constraints
  (ARCH §3.1–3.5).
- If Google ships a supported async client, swapping is contained to
  `app/integrations/youtube/` — nothing above `YouTubeOAuthClient` /
  `YouTubeDataClient` knows how the HTTP is made.

## Revisit if

- We need resumable uploads with complex chunking behaviour in Phase 2 and the
  hand-rolled implementation proves fragile (note: the resumable protocol is itself
  plain HTTP with `Content-Range`, so this is unlikely), or
- Google deprecates the REST endpoints in favour of an SDK-only surface.
