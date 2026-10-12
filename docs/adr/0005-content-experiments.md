# ADR 0005: Content experiments separate intent from observation

Status: Accepted and implemented in Phase 5.

## Decision

Create one optional, primary `ContentExperiment` for each `ContentProject`. The
experiment stores human-authored planned intent (hypothesis, primary variable,
topic, hook, duration range, audience, and explanation pattern). Planning locks a
versioned snapshot; DRAFT intent can be edited, while later states are lifecycle
events driven by the existing production, publication, and analytics workflows.

Actual creative facts remain in `ContentFeatureProfile`. Planned-versus-actual
comparison is descriptive only and returns `MATCH`, `DEVIATED`, or `UNKNOWN` per
dimension. Outcomes join the experiment to its project, published video, and the
authoritative D1/D3/D7/D28/D90 `YouTubeVideoWindowMetric` rows. The API also exposes
sample counts and reference-video availability.

## Consequences

- A database uniqueness constraint enforces one primary experiment per project.
- A control/reference project is optional and never changes production behavior.
- Existing human approval, QC, quota-preflight, publishing, OAuth, and analytics
  paths remain the source of truth.
- The UI reports evidence and outcomes but does not select winners, recommend
  strategies, claim causation, or enable autonomous publishing.
- A future non-day aggregate analytics query can be added without changing the
  experiment schema if exact window-level values are needed.
