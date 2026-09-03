"""Publishing handoff (ARCH §3.1, §13.5).

MANUAL_HANDOFF is the default and, for now, the only mode that can actually
grow the channel: videos uploaded through the API from an unaudited Google
Cloud project are permanently locked to private with no appeal. So the system
produces a complete package, a human spends ~60 seconds uploading it, and a
reconciliation job matches the result back to the project by reading the
uploads playlist — a read call, which is unrestricted.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.models.channel import Channel
from app.models.content import (
    ContentProject,
    PublishedVideo,
    PublishingJob,
    ResearchNote,
    Script,
    VideoRender,
)
from app.models.enums import PublishingMode, PublishState

log = get_logger("publishing")


def _idempotency_key(project_id, render_id) -> str:
    raw = f"{project_id}:{render_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:48]


def build_description(
    script: Script, notes: list[ResearchNote], channel: Channel
) -> str:
    """Compose a description that is useful rather than keyword-stuffed."""
    lines: list[str] = []
    if script.description:
        lines.append(script.description.strip())
    else:
        lines.append((script.selected_hook or script.narration[:180]).strip())

    sourced = [n for n in notes if n.source is not None]
    if sourced:
        lines.append("")
        lines.append("Sources:")
        seen: set[str] = set()
        for note in sourced:
            src = note.source
            label = src.title if src else None
            if not label or label in seen:
                continue
            seen.add(label)
            lines.append(f"• {label}{f' — {src.url}' if src and src.url else ''}")

    if script.hashtags:
        lines.append("")
        lines.append(" ".join(f"#{h.lstrip('#')}" for h in script.hashtags))

    return "\n".join(lines).strip()


async def create_handoff_package(
    session: AsyncSession, project: ContentProject, render: VideoRender
) -> PublishingJob:
    """Create the publishing job and its metadata package.

    The partial unique index on `publishing_job` means a second live job for the
    same project is rejected by the database, not merely by this function.
    """
    existing = (
        await session.execute(
            select(PublishingJob).where(
                PublishingJob.project_id == project.id,
                PublishingJob.state.notin_([PublishState.DONE.value, PublishState.FAILED.value]),
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing

    script = (
        await session.execute(
            select(Script).where(Script.project_id == project.id, Script.is_current.is_(True))
        )
    ).scalar_one_or_none()
    if script is None:
        raise ConflictError("Cannot build a publishing package without a current script")

    notes = (
        await session.execute(
            select(ResearchNote).where(ResearchNote.project_id == project.id)
        )
    ).scalars().all()
    channel = (
        await session.execute(select(Channel).order_by(Channel.created_at).limit(1))
    ).scalar_one()

    title = script.selected_title or (
        script.title_candidates[0] if script.title_candidates else project.topic
    )

    job = PublishingJob(
        project_id=project.id,
        render_id=render.id,
        provider_mode=PublishingMode.MANUAL_HANDOFF.value,
        state=PublishState.AWAITING_HUMAN_UPLOAD.value,
        idempotency_key=_idempotency_key(project.id, render.id),
        title=title[:100],
        description=build_description(script, list(notes), channel),
        tags=list(script.hashtags or []),
        privacy_status="public",
        # Narration is synthetic. YouTube's disclosure rules target realistic
        # synthetic depictions of people/events rather than a narrated diagram,
        # but the flag is recorded so the decision is explicit and reviewable.
        contains_synthetic_media=True,
        publishing_notes=(
            "Upload manually via YouTube Studio.\n"
            "API upload is deliberately NOT used: this Google Cloud project is "
            "unaudited, and videos uploaded through the API would be permanently "
            "locked to private with no appeal.\n"
            "After uploading, the reconciliation job matches the video back to "
            "this project from the channel's uploads playlist."
        ),
    )
    session.add(job)
    await session.flush()

    log.info("publishing.package_created", project_id=str(project.id), title=title)
    return job


def package_as_dict(job: PublishingJob, render: VideoRender) -> dict[str, Any]:
    path = Path(render.output_path) if render.output_path else None
    return {
        "mode": job.provider_mode,
        "state": job.state,
        "video": {
            "path": str(path) if path else None,
            "filename": path.name if path else None,
            "exists": bool(path and path.exists()),
            "bytes": render.bytes,
            "duration_seconds": float(render.duration_seconds) if render.duration_seconds else None,
            "resolution": f"{render.width}x{render.height}" if render.width else None,
        },
        "title": job.title,
        "description": job.description,
        "tags": list(job.tags or []),
        "privacy_status": job.privacy_status,
        "contains_synthetic_media": job.contains_synthetic_media,
        "notes": job.publishing_notes,
    }


async def record_published_video(
    session: AsyncSession,
    project: ContentProject,
    youtube_video_id: str,
    *,
    method: str = "manual",
    title: str | None = None,
) -> PublishedVideo:
    """Associate an uploaded YouTube video with its project.

    `youtube_video_id` is UNIQUE, so the same video can never be attached twice
    even if reconciliation runs concurrently with a manual confirmation.
    """
    existing = (
        await session.execute(
            select(PublishedVideo).where(PublishedVideo.youtube_video_id == youtube_video_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    job = (
        await session.execute(
            select(PublishingJob).where(PublishingJob.project_id == project.id)
        )
    ).scalars().first()

    published = PublishedVideo(
        project_id=project.id,
        publishing_job_id=job.id if job else None,
        youtube_video_id=youtube_video_id,
        title=title or (job.title if job else None),
        reconciled_at=utcnow(),
        reconciliation_method=method,
    )
    session.add(published)

    if job is not None:
        job.state = PublishState.DONE.value
        job.youtube_video_id = youtube_video_id

    await session.flush()
    log.info(
        "publishing.reconciled",
        project_id=str(project.id),
        youtube_video_id=youtube_video_id,
        method=method,
    )
    return published
