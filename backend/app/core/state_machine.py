"""Content project state machine (ARCH §8.3).

Four rules, all enforced here rather than by convention:

1. Exactly one function writes `ContentProject.status`. Nothing else may.
2. Legal transitions are declared explicitly; an illegal one raises.
3. Every transition writes a `ProjectTransition` row in the same transaction as
   the status change, so a project's state always has a recorded cause.
4. Entering a state may enqueue the next job in that same transaction — which
   is why the queue lives in Postgres (ADR 0001).
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import InvalidStateTransition
from app.core.logging import get_logger
from app.models.content import ContentProject, ProjectTransition
from app.models.enums import ProjectStatus as S

log = get_logger("state_machine")

# Declared rather than inferred. The test suite asserts this graph has no
# unreachable states and no non-terminal sinks.
LEGAL: dict[S, set[S]] = {
    S.IDEA: {S.IDEA_APPROVED, S.REJECTED, S.ARCHIVED},
    S.IDEA_APPROVED: {S.RESEARCHING, S.ARCHIVED},
    S.RESEARCHING: {S.RESEARCH_READY, S.NEEDS_REVISION, S.FAILED},
    S.RESEARCH_READY: {S.SCRIPT_GENERATING},
    S.SCRIPT_GENERATING: {S.SCRIPT_REVIEW, S.AWAITING_LLM_INPUT, S.FAILED},
    S.AWAITING_LLM_INPUT: {S.SCRIPT_GENERATING, S.FAILED},
    S.SCRIPT_REVIEW: {S.SCRIPT_APPROVED, S.NEEDS_REVISION, S.REJECTED},
    S.SCRIPT_APPROVED: {S.PRODUCTION_PLANNING},
    S.PRODUCTION_PLANNING: {S.ASSETS_REQUIRED, S.ASSETS_READY, S.FAILED},
    S.ASSETS_REQUIRED: {S.ASSETS_READY, S.NEEDS_REVISION},
    S.ASSETS_READY: {S.RENDERING},
    S.RENDERING: {S.VIDEO_REVIEW, S.NEEDS_REVISION, S.FAILED},
    S.VIDEO_REVIEW: {S.APPROVED_FOR_PUBLISHING, S.NEEDS_REVISION, S.REJECTED},
    S.APPROVED_FOR_PUBLISHING: {S.SCHEDULED, S.PUBLISHING, S.AWAITING_HUMAN_UPLOAD},
    S.SCHEDULED: {S.PUBLISHING, S.AWAITING_HUMAN_UPLOAD, S.NEEDS_REVISION},
    S.PUBLISHING: {S.AWAITING_HUMAN_UPLOAD, S.PUBLISHED, S.FAILED},
    S.AWAITING_HUMAN_UPLOAD: {S.PUBLISHED, S.FAILED, S.NEEDS_REVISION},
    S.PUBLISHED: {S.ANALYTICS_COLLECTING, S.COMPLETED},
    S.ANALYTICS_COLLECTING: {S.COMPLETED},
    # Revision can re-enter the pipeline at whichever stage produced the fault.
    S.NEEDS_REVISION: {
        S.RESEARCHING,
        S.SCRIPT_GENERATING,
        S.SCRIPT_REVIEW,
        S.PRODUCTION_PLANNING,
        S.ASSETS_READY,
        S.RENDERING,
        S.ARCHIVED,
        S.REJECTED,
    },
    S.FAILED: {S.NEEDS_REVISION, S.ARCHIVED},
    S.COMPLETED: set(),
    S.REJECTED: set(),
    S.ARCHIVED: set(),
}


def can_transition(from_status: str, to_status: str) -> bool:
    try:
        return S(to_status) in LEGAL[S(from_status)]
    except (KeyError, ValueError):
        return False


async def transition(
    session: AsyncSession,
    project: ContentProject,
    to_status: S | str,
    *,
    actor: str = "SYSTEM",
    reason: str | None = None,
    job_id: uuid.UUID | None = None,
) -> ContentProject:
    """Move a project to a new state, recording why.

    Raises InvalidStateTransition rather than silently allowing an impossible
    move — a project in a state nothing can explain is worse than a loud error.
    """
    target = S(to_status)
    current = S(project.status)

    if target == current:
        return project

    if target not in LEGAL.get(current, set()):
        raise InvalidStateTransition(
            f"Cannot move project from {current.value} to {target.value}.",
            detail={
                "from": current.value,
                "to": target.value,
                "allowed": sorted(s.value for s in LEGAL.get(current, set())),
            },
        )

    session.add(
        ProjectTransition(
            project_id=project.id,
            from_status=current.value,
            to_status=target.value,
            actor=actor,
            reason=reason,
            job_id=job_id,
        )
    )
    project.status = target.value
    if reason:
        project.status_detail = reason[:2000]
    await session.flush()

    log.info(
        "project.transition",
        project_id=str(project.id),
        **{"from": current.value, "to": target.value},
        actor=actor,
        reason=reason,
    )
    return project


def unreachable_states() -> set[S]:
    """States no transition leads to. Used by the tests to catch dead branches."""
    reachable = {S.IDEA}
    for targets in LEGAL.values():
        reachable |= targets
    return set(LEGAL) - reachable


def non_terminal_sinks() -> set[S]:
    """States with no way out that are not deliberately terminal."""
    return {s for s, targets in LEGAL.items() if not targets} - S.terminal()
