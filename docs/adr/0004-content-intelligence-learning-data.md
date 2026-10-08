# ADR 0004: Content Intelligence is observational, structured, and provenance-aware

## Status

Accepted and implemented in Phase 4.

## Decision

`ContentFeatureProfile` belongs to `ContentProject`, because creative choices exist
before publication. One profile captures a versioned, comparison-sized description of
what the project tried without duplicating scripts, scenes, renders, or assets.

Objective fields are derived from the current script, current render, its QC record,
published-video metadata, and stored production provenance. Editorial fields are
operator-reviewed. `field_provenance` records `SYSTEM_DERIVED` or `MANUAL` per field, and
`profile_version` identifies the schema used to classify the video.

The learning dataset is assembled at query time:

```text
ContentProject -> ContentFeatureProfile -> PublishedVideo
                                              |
                                  YouTubeVideoWindowMetric
                                              |
                                   observational record
```

The dataset describes what was tried and what happened. It does not recommend the next
video, rank strategies, infer causation, or convert a single example into a general
claim. Descriptive comparison responses expose both videos in each group and complete
window count so evidence availability remains visible.

## Derivation rules

- Use only the current script and current render, never an arbitrary historical asset.
- Prefer the render's actual narration provenance over current configuration values.
- Use QC's structured caption-event measurement rather than parsing files again.
- Leave an objective field null when no structured fact exists; do not infer CTA or
  editorial concepts from text heuristics.
- Refresh objective fields during the existing analytics worker job. Provider calls
  remain in workers; the scheduler only enqueues.

## Deferred

LLM classification, strategy recommendations, confidence thresholds, autonomous topic,
hook or title selection, experimentation, and causal interpretation are Phase 5 or later.
