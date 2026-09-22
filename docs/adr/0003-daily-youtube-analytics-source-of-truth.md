# ADR 0003: Daily YouTube metrics are the analytics source of truth

## Status

Accepted for Phase 3; no analytics schema is created in Phase 2.5.

## Decision

Phase 3 will persist one idempotent raw analytics row per published video and
YouTube metric date, modeled as `YouTubeVideoDailyMetric` with authoritative
uniqueness on `(published_video_id, metric_date)`.

D1, D3, D7, D28, and D90 performance windows will initially be derived from
those daily rows. Materialized age-bucket snapshots may be introduced later as
a derived optimization, but they will not replace daily metrics as canonical
storage.

## Rationale

Daily rows preserve the API's natural time grain, allow corrected or delayed
data to be upserted deterministically, and support new comparison windows
without a schema change or lost detail. Storing only one row per age bucket
would discard information and make later recomputation difficult.
