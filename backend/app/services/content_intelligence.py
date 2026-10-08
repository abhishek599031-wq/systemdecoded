"""Content feature derivation and observational learning-record assembly."""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.models.analytics import YouTubeVideoWindowMetric
from app.models.content import (
    ContentProject,
    ProductionAsset,
    PublishedVideo,
    QualityCheck,
    Scene,
    Script,
    VideoRender,
)
from app.models.content_intelligence import ContentFeatureProfile
from app.models.enums import (
    AnalyticsWindowStatus,
    AnalyticsWindowType,
    AssetType,
    FeatureProvenance,
)

PROFILE_VERSION = 1

SYSTEM_FIELDS = (
    "duration_seconds",
    "narration_duration_seconds",
    "scene_count",
    "script_word_count",
    "narration_word_count",
    "narration_words_per_minute",
    "voice_provider",
    "voice_model",
    "voice_name",
    "tts_speed",
    "caption_event_count",
    "render_width",
    "render_height",
    "template_families",
    "content_format",
    "published_title",
    "cta_present",
)

EDITORIAL_FIELDS = (
    "topic_category",
    "topic",
    "subtopic",
    "hook_type",
    "hook_text",
    "title_style",
    "explanation_pattern",
    "audience_level",
    "technical_depth",
    "cta_type",
    "visual_storytelling_style",
    "question_answered",
)


def _word_count(text: str | None) -> int | None:
    if text is None:
        return None
    return len(re.findall(r"\b[\w'-]+\b", text))


def _caption_event_count(check: QualityCheck | None) -> int | None:
    if check is None:
        return None
    for item in check.checks or []:
        if item.get("name") == "captions_present":
            measured = item.get("measured")
            return int(measured) if isinstance(measured, (int, float)) else None
    return None


async def _tts_speed(
    session: AsyncSession,
    project_id: uuid.UUID,
    render: VideoRender | None,
    narration: dict[str, Any],
) -> Decimal | None:
    if render is None or render.script_id is None:
        return None
    query = (
        select(ProductionAsset)
        .join(Scene, ProductionAsset.scene_id == Scene.id)
        .where(
            ProductionAsset.project_id == project_id,
            ProductionAsset.asset_type == AssetType.NARRATION_AUDIO,
            Scene.script_id == render.script_id,
        )
        .order_by(ProductionAsset.created_at.desc())
    )
    if render.finished_at:
        query = query.where(ProductionAsset.created_at <= render.finished_at)
    assets = list((await session.execute(query)).scalars())
    expected_provider = narration.get("provider")
    expected_voice = narration.get("voice")
    for asset in assets:
        metadata = asset.asset_metadata or {}
        if expected_provider and metadata.get("provider") != expected_provider:
            continue
        if expected_voice and metadata.get("voice") != expected_voice:
            continue
        speed = metadata.get("speed")
        if speed is not None:
            return Decimal(str(speed))
    return None


async def refresh_profile(
    session: AsyncSession,
    project_id: uuid.UUID,
) -> ContentFeatureProfile:
    """Upsert objective features from the project's current production facts."""
    project = await session.get(ContentProject, project_id)
    if project is None:
        raise LookupError(f"Content project {project_id} was not found")

    profile = (
        await session.execute(
            select(ContentFeatureProfile).where(ContentFeatureProfile.project_id == project.id)
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = ContentFeatureProfile(project_id=project.id, profile_version=PROFILE_VERSION)
        session.add(profile)

    script = (
        await session.get(Script, project.current_script_id) if project.current_script_id else None
    )
    render = (
        await session.get(VideoRender, project.current_render_id)
        if project.current_render_id
        else None
    )
    scenes = (
        list(
            (
                await session.execute(
                    select(Scene).where(Scene.script_id == script.id).order_by(Scene.scene_number)
                )
            ).scalars()
        )
        if script
        else []
    )
    published = (
        await session.execute(select(PublishedVideo).where(PublishedVideo.project_id == project.id))
    ).scalar_one_or_none()
    quality = (
        (
            await session.execute(
                select(QualityCheck)
                .where(QualityCheck.render_id == render.id)
                .order_by(QualityCheck.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if render
        else None
    )

    narration = dict((render.spec or {}).get("narration") or {}) if render else {}
    starts = [scene.start_seconds for scene in scenes if scene.start_seconds is not None]
    ends = [scene.end_seconds for scene in scenes if scene.end_seconds is not None]
    narration_duration = max(ends) - min(starts) if starts and ends else None
    narration_words = _word_count(script.narration) if script else None
    narration_wpm = None
    if narration_words is not None and narration_duration and narration_duration > 0:
        narration_wpm = Decimal(narration_words) * Decimal(60) / narration_duration

    values: dict[str, Any] = {
        "duration_seconds": render.duration_seconds if render else None,
        "narration_duration_seconds": narration_duration,
        "scene_count": len(scenes) if script else None,
        "script_word_count": script.word_count if script else None,
        "narration_word_count": narration_words,
        "narration_words_per_minute": narration_wpm,
        "voice_provider": narration.get("provider"),
        "voice_model": narration.get("model"),
        "voice_name": narration.get("voice"),
        "tts_speed": await _tts_speed(session, project.id, render, narration),
        "caption_event_count": _caption_event_count(quality),
        "render_width": render.width if render else None,
        "render_height": render.height if render else None,
        "template_families": sorted({scene.template_id for scene in scenes}),
        "content_format": project.content_format,
        "published_title": published.title if published else None,
        # There is no explicit CTA field in the current production schema.
        # Text inspection would be a guess, so unknown remains NULL.
        "cta_present": None,
    }
    for field, value in values.items():
        setattr(profile, field, value)

    provenance = dict(profile.field_provenance or {})
    provenance.update({field: FeatureProvenance.SYSTEM_DERIVED.value for field in SYSTEM_FIELDS})
    profile.field_provenance = provenance
    profile.profile_version = PROFILE_VERSION
    profile.derived_at = utcnow()
    await session.flush()
    await session.refresh(profile)
    return profile


async def update_editorial_profile(
    session: AsyncSession,
    project_id: uuid.UUID,
    values: dict[str, Any],
) -> ContentFeatureProfile:
    profile = await refresh_profile(session, project_id)
    provenance = dict(profile.field_provenance or {})
    for field, value in values.items():
        if field not in EDITORIAL_FIELDS:
            raise ValueError(f"{field} is not an editable editorial feature")
        setattr(profile, field, value.value if hasattr(value, "value") else value)
        provenance[field] = FeatureProvenance.MANUAL.value
    profile.field_provenance = provenance
    await session.flush()
    await session.refresh(profile)
    return profile


def _json_value(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else value


def profile_payload(profile: ContentFeatureProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "project_id": profile.project_id,
        "profile_version": profile.profile_version,
        "system_derived": {field: _json_value(getattr(profile, field)) for field in SYSTEM_FIELDS},
        "editorial": {field: getattr(profile, field) for field in EDITORIAL_FIELDS},
        "field_provenance": profile.field_provenance,
        "derived_at": profile.derived_at,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def window_payload(row: YouTubeVideoWindowMetric) -> dict[str, Any]:
    payload = {
        "window": row.window_type,
        "status": row.status,
        "start_date": row.window_start_date,
        "end_date": row.window_end_date,
        "views": row.views,
        "engaged_views": row.engaged_views,
        "likes": row.likes,
        "comments": row.comments,
        "shares": row.shares,
        "subscribers_gained": row.subscribers_gained,
        "subscribers_lost": row.subscribers_lost,
        "estimated_minutes_watched": row.estimated_minutes_watched,
        "average_view_duration": row.average_view_duration,
        "average_view_percentage": row.average_view_percentage,
        "provider": row.provider,
        "synced_at": row.synced_at,
        "last_error": row.last_error,
    }
    return {key: _json_value(value) for key, value in payload.items()}


async def learning_record(
    session: AsyncSession,
    video: PublishedVideo,
    *,
    refresh_features: bool = False,
) -> dict[str, Any]:
    profile = (
        await refresh_profile(session, video.project_id)
        if refresh_features
        else (
            await session.execute(
                select(ContentFeatureProfile).where(
                    ContentFeatureProfile.project_id == video.project_id
                )
            )
        ).scalar_one_or_none()
    )
    windows = list(
        (
            await session.execute(
                select(YouTubeVideoWindowMetric).where(
                    YouTubeVideoWindowMetric.published_video_id == video.id
                )
            )
        ).scalars()
    )
    by_type = {row.window_type: window_payload(row) for row in windows}
    return {
        "video_id": video.youtube_video_id,
        "published_video_id": video.id,
        "project_id": video.project_id,
        "title": video.title,
        "published_at": video.published_at,
        "features": profile_payload(profile) if profile else None,
        "performance": {window.value: by_type.get(window.value) for window in AnalyticsWindowType},
    }


async def learning_records(
    session: AsyncSession,
    *,
    topic_category: str | None = None,
    hook_type: str | None = None,
    window_available: AnalyticsWindowType | None = None,
) -> list[dict[str, Any]]:
    videos = list(
        (
            await session.execute(
                select(PublishedVideo).order_by(PublishedVideo.published_at.desc())
            )
        ).scalars()
    )
    records = [await learning_record(session, video) for video in videos]
    if topic_category:
        records = [
            record
            for record in records
            if record["features"]
            and record["features"]["editorial"]["topic_category"] == topic_category
        ]
    if hook_type:
        records = [
            record
            for record in records
            if record["features"] and record["features"]["editorial"]["hook_type"] == hook_type
        ]
    if window_available:
        records = [
            record
            for record in records
            if record["performance"].get(window_available.value)
            and record["performance"][window_available.value]["status"]
            == AnalyticsWindowStatus.AVAILABLE.value
        ]
    return records


def _duration_band(value: Decimal | None) -> str:
    if value is None:
        return "unknown"
    if value <= 30:
        return "up_to_30s"
    if value <= 45:
        return "31_to_45s"
    return "over_45s"


async def descriptive_comparison(
    session: AsyncSession,
    group_by: str,
    window: AnalyticsWindowType,
) -> list[dict[str, Any]]:
    allowed = {
        "topic_category",
        "hook_type",
        "duration_band",
        "technical_depth",
        "explanation_pattern",
    }
    if group_by not in allowed:
        raise ValueError(f"Unsupported comparison group: {group_by}")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in await learning_records(session):
        profile = record["features"]
        if not profile:
            key = "unclassified"
        elif group_by == "duration_band":
            key = _duration_band(profile["system_derived"]["duration_seconds"])
        else:
            key = profile["editorial"].get(group_by) or "unclassified"
        grouped[str(key)].append(record)

    output = []
    for key, records in sorted(grouped.items()):
        complete = [
            record["performance"][window.value]
            for record in records
            if record["performance"].get(window.value)
            and record["performance"][window.value]["status"]
            == AnalyticsWindowStatus.AVAILABLE.value
        ]
        views = [item["views"] for item in complete if item["views"] is not None]
        output.append(
            {
                "group": key,
                "videos_in_group": len(records),
                "complete_windows_available": len(complete),
                "average_views": sum(views) / len(views) if views else None,
                "comparative_evidence_insufficient": len(records) < 2,
            }
        )
    return output
