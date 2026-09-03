"""Content production jobs.

One job runs the whole pipeline. It is deliberately not split into a job per
stage: the stages share large intermediate artifacts (audio, frames) on disk,
and a partial run leaves a project in a state no human can interpret. A single
job that either produces a reviewable video or fails visibly is easier to
operate — and every stage still persists its own inspectable output.

Failure is loud by design: a project that cannot render moves to FAILED with a
reason, rather than quietly producing a broken video.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import RetryableError, TerminalError
from app.core.state_machine import transition
from app.jobs.context import JobContext
from app.jobs.registry import job
from app.models.content import ContentProject, VideoRender
from app.models.enums import ProjectStatus, QualityVerdict
from app.services import production, publishing, quality

PRODUCTION_RETRYABLE = (RetryableError,)


@job(
    "content.produce_video",
    max_attempts=2,
    timeout_seconds=2400,
    retry_on=PRODUCTION_RETRYABLE,
    default_priority=8,
    description="Full production run: narration, timing, scenes, captions, composition, QC.",
)
async def produce_video(ctx: JobContext) -> dict[str, Any]:
    project_id = ctx.require("project_id")
    project = await ctx.session.get(ContentProject, project_id)
    if project is None:
        raise TerminalError(f"Project {project_id} not found")

    ctx.logger.info("content.production_started", topic=project.topic, status=project.status)

    if project.status not in {
        ProjectStatus.ASSETS_READY,
        ProjectStatus.RENDERING,
        ProjectStatus.PRODUCTION_PLANNING,
    }:
        raise TerminalError(
            f"Project is {project.status}; production requires ASSETS_READY. "
            "Approve the script first."
        )

    if project.status != ProjectStatus.RENDERING:
        if project.status == ProjectStatus.PRODUCTION_PLANNING:
            await transition(ctx.session, project, ProjectStatus.ASSETS_READY,
                             reason="No manual assets required", job_id=ctx.job_id)
        await transition(ctx.session, project, ProjectStatus.RENDERING,
                         reason="Production run started", job_id=ctx.job_id)

    try:
        render = await production.produce(ctx.session, project)
    except Exception as exc:
        # Roll back partial work, then record the failure against the project so
        # it surfaces in the UI instead of vanishing into job logs.
        await ctx.session.rollback()
        fresh = await ctx.session.get(ContentProject, project_id)
        if fresh is not None:
            fresh.failure_reason = str(exc)[:2000]
            if fresh.status == ProjectStatus.RENDERING:
                await transition(ctx.session, fresh, ProjectStatus.FAILED,
                                 reason=f"Render failed: {exc}"[:500], job_id=ctx.job_id)
        raise

    check = await quality.run_quality_checks(ctx.session, project, render)

    if check.verdict == QualityVerdict.FAIL.value:
        await transition(
            ctx.session,
            project,
            ProjectStatus.NEEDS_REVISION,
            reason=f"Quality gate failed: {'; '.join(check.blocking_issues[:3])}"[:500],
            job_id=ctx.job_id,
        )
        ctx.logger.warning("content.quality_failed", issues=check.blocking_issues)
        return {
            "render_id": str(render.id),
            "verdict": check.verdict,
            "blocking_issues": list(check.blocking_issues),
            "advanced_to_review": False,
        }

    await transition(
        ctx.session,
        project,
        ProjectStatus.VIDEO_REVIEW,
        reason=f"Render complete ({check.verdict})",
        job_id=ctx.job_id,
    )

    return {
        "render_id": str(render.id),
        "verdict": check.verdict,
        "warnings": list(check.warnings),
        "duration_seconds": float(render.duration_seconds) if render.duration_seconds else None,
        "output": render.output_path,
        "advanced_to_review": True,
    }


@job(
    "content.build_publishing_package",
    max_attempts=2,
    timeout_seconds=120,
    retry_on=PRODUCTION_RETRYABLE,
    description="Assemble the MANUAL_HANDOFF publishing package for an approved video.",
)
async def build_publishing_package(ctx: JobContext) -> dict[str, Any]:
    project_id = ctx.require("project_id")
    project = await ctx.session.get(ContentProject, project_id)
    if project is None:
        raise TerminalError(f"Project {project_id} not found")

    if project.current_render_id is None:
        raise TerminalError("Project has no render to publish")
    render = await ctx.session.get(VideoRender, project.current_render_id)
    if render is None:
        raise TerminalError("Current render not found")

    job_row = await publishing.create_handoff_package(ctx.session, project, render)

    if project.status == ProjectStatus.APPROVED_FOR_PUBLISHING:
        await transition(
            ctx.session,
            project,
            ProjectStatus.AWAITING_HUMAN_UPLOAD,
            reason="Publishing package ready for manual upload",
            job_id=ctx.job_id,
        )

    return {
        "publishing_job_id": str(job_row.id),
        "title": job_row.title,
        "state": job_row.state,
    }
