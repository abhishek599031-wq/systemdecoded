# ADR 0003: Daily history and fixed-window YouTube aggregates have distinct authority

## Status

Accepted in Phase 3 and amended in Phase 4 after measuring provider quantization.

## Decision

Phase 3 persists one idempotent raw analytics row per published video and
YouTube metric date, modeled as `YouTubeVideoDailyMetric` with authoritative
uniqueness on `(published_video_id, metric_date)`.

Daily rows remain canonical for time-series history, daily growth, spikes,
decay, and trend analysis. They are not sufficient for authoritative lifecycle
totals: YouTube returns some day-level watch metrics as integers, and summing
those already-quantized cells loses information.

Phase 4 therefore persists direct, dimensionless provider reports for the fixed
D1, D3, D7, D28, and D90 lifecycle windows. `YouTubeVideoWindowMetric` is unique
on `(published_video_id, window_type)` and stores the provider's aggregate for
the exact window. These rows complement rather than replace daily history.

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

Lifecycle windows anchor on the video's YouTube/Pacific publication reporting day. The
first provider-returned daily row never redefines D1. A window is
eligible only after daily reporting coverage reaches every required date.
Wall-clock maturity alone is insufficient because YouTube reporting can lag.
Eligible metrics are fetched with no `day` dimension and stored with explicit
`PENDING`, `AVAILABLE`, or `ERROR` state. No pending window receives fake zero
metrics.

Existing query-time daily projections remain useful diagnostics, but they are
not presented as provider-authoritative fixed-window watch time or averages.

The YouTube Data API snapshot is used to verify ownership and refresh current
metadata. Its cumulative public statistics are operational context, not canonical
daily history.

## Rationale

Daily rows preserve the API's natural time grain and allow corrected or delayed
data to be upserted deterministically. Direct aggregate rows preserve the
provider's fixed-window calculation before per-day integer quantization. Both
grains are required for a trustworthy learning dataset.

```text
                         YouTube Analytics
                                |
                 +--------------+--------------+
                 |                             |
          dimensions=day                no day dimension
                 |                             |
                 v                             v
       DailyMetric history            WindowMetric results
                 |                             |
                 v                             v
       trend/time-series             D1/D3/D7/D28/D90
                 |                             |
                 +--------------+--------------+
                                |
                                v
                       Learning Dataset
```
