# ADR 0003: Daily YouTube metrics are the analytics source of truth

## Status

Accepted and implemented in Phase 3.

## Decision

Phase 3 persists one idempotent raw analytics row per published video and
YouTube metric date, modeled as `YouTubeVideoDailyMetric` with authoritative
uniqueness on `(published_video_id, metric_date)`.

D1, D3, D7, D28, and D90 performance windows will initially be derived from
those daily rows. Materialized age-bucket snapshots may be introduced later as
a derived optimization, but they will not replace daily metrics as canonical
storage.

The stored metrics are `views`, `engaged_views`, `likes`, `comments`, `shares`,
`subscribers_gained`, `subscribers_lost`, `estimated_minutes_watched`,
`average_view_duration`, and `average_view_percentage`. Every metric is nullable:
missing provider data is not zero.

`views` and `engaged_views` are distinct facts. For Shorts, the current public
view definition counts starts/replays while `engagedViews` preserves the older
continued-watching methodology. Neither field substitutes for the other.

`metric_date` is exactly the Analytics API `day` value. It is not derived from
the UTC publication timestamp or the machine timezone. Pacific reporting time is
used only to choose request boundaries; `published_at` remains `timestamptz`.

Initial sync requests publication through the current reporting date and stores
only dates YouTube returns. Incremental sync refreshes the most recent 14 days and
recovers older gaps with idempotent upserts. No placeholder rows are created for
provider latency.

The derived windows anchor on the first API-returned reporting day. Additive
metrics are summed without substituting missing values. Playback averages are
weighted by `engaged_views` only when every daily numerator and denominator is
available; otherwise the aggregate is unavailable. Window responses also carry
date coverage and completeness, so partial provider history is visible.

The YouTube Data API snapshot is used to verify ownership and refresh current
metadata. Its cumulative public statistics are operational context, not canonical
daily history.

## Rationale

Daily rows preserve the API's natural time grain, allow corrected or delayed
data to be upserted deterministically, and support new comparison windows
without a schema change or lost detail. Storing only one row per age bucket
would discard information and make later recomputation difficult.
