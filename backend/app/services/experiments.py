"""Phase 5 experiment intent, lifecycle, and descriptive outcome assembly."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import utcnow
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.analytics import YouTubeVideoWindowMetric
from app.models.channel import Channel
from app.models.content import ContentProject, PublishedVideo
from app.models.content_intelligence import ContentFeatureProfile
from app.models.enums import (
    AnalyticsWindowStatus,
    AnalyticsWindowType,
    ExperimentComparisonStatus,
    ExperimentStatus,
    ProjectStatus,
)
from app.models.experiments import ContentExperiment
from app.schemas.experiments import ExperimentCreate, ExperimentPatch

PLANNED_FIELDS = (
    "planned_topic_category",
    "planned_topic",
    "planned_hook_type",
    "planned_duration_min_seconds",
    "planned_duration_max_seconds",
    "planned_explanation_pattern",
    "planned_audience_level",
    "planned_technical_depth",
    "planned_title_style",
)

ACTUAL_FIELDS = {
    "planned_topic_category": "topic_category",
    "planned_topic": "topic",
    "planned_hook_type": "hook_type",
    "planned_explanation_pattern": "explanation_pattern",
    "planned_audience_level": "audience_level",
    "planned_technical_depth": "technical_depth",
    "planned_title_style": "title_style",
}

LEGAL: dict[ExperimentStatus, set[ExperimentStatus]] = {
    ExperimentStatus.DRAFT: {ExperimentStatus.PLANNED, ExperimentStatus.CANCELLED},
    ExperimentStatus.PLANNED: {
        ExperimentStatus.IN_PRODUCTION,
        ExperimentStatus.CANCELLED,
    },
    ExperimentStatus.IN_PRODUCTION: {
        ExperimentStatus.PRODUCED,
        ExperimentStatus.CANCELLED,
    },
    ExperimentStatus.PRODUCED: {ExperimentStatus.PUBLISHED},
    ExperimentStatus.PUBLISHED: {ExperimentStatus.MEASURING},
    ExperimentStatus.MEASURING: {ExperimentStatus.MEASURED},
    ExperimentStatus.MEASURED: set(),
    ExperimentStatus.CANCELLED: set(),
}


def _value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _planned_payload(experiment: ContentExperiment) -> dict[str, Any]:
    return {
        "hypothesis": experiment.hypothesis,
        "primary_variable": experiment.primary_variable,
        "control_reference_project_id": _value(experiment.control_reference_project_id),
        **{field: _value(getattr(experiment, field)) for field in PLANNED_FIELDS},
    }


def _project_payload(project: ContentProject | None) -> dict[str, Any] | None:
    if project is None:
        return None
    return {
        "id": project.id,
        "topic": project.topic,
        "status": project.status,
        "working_title": project.working_title,
    }


def serialize_experiment(
    experiment: ContentExperiment,
    *,
    project: ContentProject | None = None,
    control_reference: ContentProject | None = None,
) -> dict[str, Any]:
    project = project or experiment.project
    control_reference = control_reference or experiment.control_reference_project
    return {
        "id": experiment.id,
        "project_id": experiment.project_id,
        "experiment_version": experiment.experiment_version,
        "status": experiment.status,
        "hypothesis": experiment.hypothesis,
        "primary_variable": experiment.primary_variable,
        "planned": {
            field.removeprefix("planned_"): _value(getattr(experiment, field))
            for field in PLANNED_FIELDS
        },
        "control_reference": _project_payload(control_reference),
        "locked_at": experiment.locked_at,
        "locked_snapshot": experiment.locked_snapshot,
        "project": _project_payload(project),
        "created_at": experiment.created_at,
        "updated_at": experiment.updated_at,
    }


async def get_experiment(session: AsyncSession, experiment_id: uuid.UUID) -> ContentExperiment:
    experiment = (
        await session.execute(
            select(ContentExperiment)
            .options(
                selectinload(ContentExperiment.project),
                selectinload(ContentExperiment.control_reference_project),
            )
            .where(ContentExperiment.id == experiment_id)
        )
    ).scalar_one_or_none()
    if experiment is None:
        raise NotFoundError(f"Experiment {experiment_id} not found")
    return experiment


async def _validate_reference(
    session: AsyncSession,
    project_id: uuid.UUID,
    reference_id: uuid.UUID | None,
) -> ContentProject | None:
    if reference_id is None:
        return None
    if reference_id == project_id:
        raise ValidationError("An experiment cannot reference its own project.")
    reference = await session.get(ContentProject, reference_id)
    if reference is None:
        raise NotFoundError(f"Control reference project {reference_id} not found")
    return reference


def _validate_planned_values(experiment: ContentExperiment) -> None:
    if not experiment.hypothesis.strip():
        raise ValidationError("A human-readable hypothesis is required before planning.")
    if not experiment.primary_variable.strip():
        raise ValidationError("A primary experiment variable is required before planning.")
    if not experiment.planned_topic:
        raise ValidationError("A planned topic is required before planning.")
    minimum = experiment.planned_duration_min_seconds
    maximum = experiment.planned_duration_max_seconds
    if (minimum is None) != (maximum is None):
        raise ValidationError("Provide both planned duration bounds or neither.")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValidationError("Planned duration minimum cannot exceed maximum.")


async def create_experiment(
    session: AsyncSession,
    body: ExperimentCreate,
) -> ContentExperiment:
    if body.project_id is not None:
        project = await session.get(ContentProject, body.project_id)
        if project is None:
            raise NotFoundError(f"Content project {body.project_id} not found")
        existing = (
            await session.execute(
                select(ContentExperiment).where(ContentExperiment.project_id == project.id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("This project already has a primary experiment.")
    else:
        channel = (
            await session.execute(select(Channel).order_by(Channel.created_at).limit(1))
        ).scalar_one_or_none()
        if channel is None:
            raise ConflictError("No channel is configured for a new experiment.")
        topic = body.planned_topic or "Untitled experiment"
        midpoint = None
        if body.planned_duration_min_seconds is not None:
            midpoint = body.planned_duration_min_seconds
            if body.planned_duration_max_seconds is not None:
                midpoint = (midpoint + body.planned_duration_max_seconds) / Decimal(2)
        project = ContentProject(
            channel_id=channel.id,
            topic=topic,
            working_title=topic,
            content_pillar=body.planned_topic_category,
            content_format="How It Works",
            target_duration_seconds=round(float(midpoint or 32)),
            status=ProjectStatus.IDEA.value,
            created_by="HUMAN",
        )
        session.add(project)
        await session.flush()

    reference = await _validate_reference(session, project.id, body.control_reference_project_id)
    values = body.model_dump(exclude={"project_id"})
    for field in (
        "planned_hook_type",
        "planned_audience_level",
        "planned_technical_depth",
        "planned_title_style",
    ):
        if hasattr(values.get(field), "value"):
            values[field] = values[field].value
    experiment = ContentExperiment(project=project, control_reference_project=reference, **values)
    session.add(experiment)
    await session.flush()
    return experiment


async def update_draft(
    session: AsyncSession,
    experiment_id: uuid.UUID,
    body: ExperimentPatch,
) -> ContentExperiment:
    experiment = await get_experiment(session, experiment_id)
    if experiment.status != ExperimentStatus.DRAFT.value:
        raise ConflictError("Only DRAFT experiment intent can be edited.")
    values = body.model_dump(exclude_unset=True)
    await _validate_reference(
        session,
        experiment.project_id,
        values.get("control_reference_project_id", experiment.control_reference_project_id),
    )
    merged = {field: getattr(experiment, field) for field in PLANNED_FIELDS}
    merged.update({key: value for key, value in values.items() if key in PLANNED_FIELDS})
    minimum = merged["planned_duration_min_seconds"]
    maximum = merged["planned_duration_max_seconds"]
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValidationError("Planned duration minimum cannot exceed maximum.")
    for field, value in values.items():
        if hasattr(value, "value"):
            value = value.value
        setattr(experiment, field, value)
    await session.flush()
    return experiment


async def plan_experiment(session: AsyncSession, experiment_id: uuid.UUID) -> ContentExperiment:
    experiment = await get_experiment(session, experiment_id)
    if experiment.status != ExperimentStatus.DRAFT.value:
        raise ConflictError("Only DRAFT experiments can be marked PLANNED.")
    _validate_planned_values(experiment)
    experiment.status = ExperimentStatus.PLANNED.value
    experiment.locked_at = utcnow()
    experiment.locked_snapshot = {
        "experiment_version": experiment.experiment_version,
        **_planned_payload(experiment),
    }
    await session.flush()
    await session.refresh(experiment)
    return experiment


async def transition_status(
    session: AsyncSession,
    experiment: ContentExperiment,
    target: ExperimentStatus,
) -> ContentExperiment:
    current = ExperimentStatus(experiment.status)
    if target == current:
        return experiment
    if target not in LEGAL[current]:
        raise ConflictError(f"Cannot move experiment from {current.value} to {target.value}.")
    experiment.status = target.value
    await session.flush()
    return experiment


async def mark_in_production(session: AsyncSession, project_id: uuid.UUID) -> None:
    experiment = (
        await session.execute(
            select(ContentExperiment).where(ContentExperiment.project_id == project_id)
        )
    ).scalar_one_or_none()
    if experiment is not None and experiment.status == ExperimentStatus.PLANNED.value:
        await transition_status(session, experiment, ExperimentStatus.IN_PRODUCTION)


async def mark_produced(session: AsyncSession, project_id: uuid.UUID) -> None:
    experiment = (
        await session.execute(
            select(ContentExperiment).where(ContentExperiment.project_id == project_id)
        )
    ).scalar_one_or_none()
    if experiment is not None and experiment.status == ExperimentStatus.IN_PRODUCTION.value:
        await transition_status(session, experiment, ExperimentStatus.PRODUCED)


async def mark_published(session: AsyncSession, project_id: uuid.UUID) -> None:
    experiment = (
        await session.execute(
            select(ContentExperiment).where(ContentExperiment.project_id == project_id)
        )
    ).scalar_one_or_none()
    if experiment is not None and experiment.status == ExperimentStatus.PRODUCED.value:
        video = (
            await session.execute(
                select(PublishedVideo).where(PublishedVideo.project_id == project_id)
            )
        ).scalar_one_or_none()
        if video is None:
            return
        await transition_status(session, experiment, ExperimentStatus.PUBLISHED)


async def mark_measurement_progress(
    session: AsyncSession,
    project_id: uuid.UUID,
) -> None:
    experiment = (
        await session.execute(
            select(ContentExperiment).where(ContentExperiment.project_id == project_id)
        )
    ).scalar_one_or_none()
    if experiment is None or experiment.status not in {
        ExperimentStatus.PUBLISHED.value,
        ExperimentStatus.MEASURING.value,
    }:
        return
    video = (
        await session.execute(select(PublishedVideo).where(PublishedVideo.project_id == project_id))
    ).scalar_one_or_none()
    if video is None:
        return
    rows = list(
        (
            await session.execute(
                select(YouTubeVideoWindowMetric).where(
                    YouTubeVideoWindowMetric.published_video_id == video.id
                )
            )
        ).scalars()
    )
    await transition_status(session, experiment, ExperimentStatus.MEASURING)
    if all(
        any(
            row.window_type == window.value
            and row.status == AnalyticsWindowStatus.AVAILABLE.value
            for row in rows
        )
        for window in AnalyticsWindowType
    ):
        await transition_status(session, experiment, ExperimentStatus.MEASURED)


def _dimension(
    planned: Any,
    actual: Any,
) -> dict[str, Any]:
    if planned is None or actual is None:
        return {
            "planned": _value(planned),
            "actual": _value(actual),
            "status": ExperimentComparisonStatus.UNKNOWN.value,
        }
    return {
        "planned": _value(planned),
        "actual": _value(actual),
        "status": (
            ExperimentComparisonStatus.MATCH.value
            if str(planned) == str(actual)
            else ExperimentComparisonStatus.DEVIATED.value
        ),
    }


def comparison_payload(
    experiment: ContentExperiment,
    profile: ContentFeatureProfile | None,
) -> dict[str, Any]:
    actual = profile
    dimensions: dict[str, dict[str, Any]] = {}
    for planned_field, actual_field in ACTUAL_FIELDS.items():
        dimensions[planned_field.removeprefix("planned_")] = _dimension(
            getattr(experiment, planned_field),
            getattr(actual, actual_field) if actual else None,
        )
    planned_min = experiment.planned_duration_min_seconds
    planned_max = experiment.planned_duration_max_seconds
    actual_duration = actual.duration_seconds if actual else None
    if planned_min is None or planned_max is None or actual_duration is None:
        duration_status = ExperimentComparisonStatus.UNKNOWN.value
    elif planned_min <= actual_duration <= planned_max:
        duration_status = ExperimentComparisonStatus.MATCH.value
    else:
        duration_status = ExperimentComparisonStatus.DEVIATED.value
    dimensions["duration_seconds"] = {
        "planned_min": _value(planned_min),
        "planned_max": _value(planned_max),
        "actual": _value(actual_duration),
        "status": duration_status,
    }
    return {
        "planned": _planned_payload(experiment),
        "actual": (
            {
                actual_field: _value(getattr(actual, actual_field))
                for actual_field in ACTUAL_FIELDS.values()
            }
            | {"duration_seconds": _value(actual_duration)}
            if actual
            else None
        ),
        "dimensions": dimensions,
    }


async def _profile_for_project(
    session: AsyncSession,
    project_id: uuid.UUID,
) -> ContentFeatureProfile | None:
    return (
        await session.execute(
            select(ContentFeatureProfile).where(ContentFeatureProfile.project_id == project_id)
        )
    ).scalar_one_or_none()


async def comparison(
    session: AsyncSession,
    experiment_id: uuid.UUID,
) -> dict[str, Any]:
    experiment = await get_experiment(session, experiment_id)
    profile = await _profile_for_project(session, experiment.project_id)
    return {
        "experiment": serialize_experiment(experiment),
        **comparison_payload(experiment, profile),
    }


async def _evidence_counts(session: AsyncSession) -> dict[str, Any]:
    experiments = list((await session.execute(select(ContentExperiment))).scalars())
    published_count = 0
    complete = {window.value: 0 for window in AnalyticsWindowType}
    for experiment in experiments:
        video = (
            await session.execute(
                select(PublishedVideo).where(PublishedVideo.project_id == experiment.project_id)
            )
        ).scalar_one_or_none()
        if video is None:
            continue
        published_count += 1
        rows = list(
            (
                await session.execute(
                    select(YouTubeVideoWindowMetric).where(
                        YouTubeVideoWindowMetric.published_video_id == video.id
                    )
                )
            ).scalars()
        )
        for window in complete:
            if any(
                row.window_type == window
                and row.status == AnalyticsWindowStatus.AVAILABLE.value
                for row in rows
            ):
                complete[window] += 1
    return {
        "number_of_published_experiments": published_count,
        "complete_d1_samples": complete["D1"],
        "complete_d3_samples": complete["D3"],
        "complete_d7_samples": complete["D7"],
        "complete_d28_samples": complete["D28"],
        "complete_d90_samples": complete["D90"],
    }


async def outcome(session: AsyncSession, experiment_id: uuid.UUID) -> dict[str, Any]:
    experiment = await get_experiment(session, experiment_id)
    project = await session.get(ContentProject, experiment.project_id)
    profile = await _profile_for_project(session, experiment.project_id)
    video = (
        await session.execute(select(PublishedVideo).where(PublishedVideo.project_id == project.id))
    ).scalar_one_or_none() if project else None
    windows = []
    if video:
        rows = list(
            (
                await session.execute(
                    select(YouTubeVideoWindowMetric)
                    .where(YouTubeVideoWindowMetric.published_video_id == video.id)
                    .order_by(YouTubeVideoWindowMetric.window_end_date)
                )
            ).scalars()
        )
        windows = [
            {
                "window": row.window_type,
                "status": row.status,
                "start_date": row.window_start_date,
                "end_date": row.window_end_date,
                "views": row.views,
                "engaged_views": row.engaged_views,
                "estimated_minutes_watched": _value(row.estimated_minutes_watched),
                "average_view_duration": _value(row.average_view_duration),
                "average_view_percentage": _value(row.average_view_percentage),
            }
            for row in rows
        ]
    control = (
        await session.get(ContentProject, experiment.control_reference_project_id)
        if experiment.control_reference_project_id
        else None
    )
    control_video = (
        await session.execute(select(PublishedVideo).where(PublishedVideo.project_id == control.id))
    ).scalar_one_or_none() if control else None
    return {
        "experiment": serialize_experiment(experiment, project=project, control_reference=control),
        "comparison": comparison_payload(experiment, profile),
        "published_video": (
            {
                "id": video.id,
                "youtube_video_id": video.youtube_video_id,
                "title": video.title,
                "published_at": video.published_at,
            }
            if video
            else None
        ),
        "outcomes": windows,
        "evidence": {
            "reference_video_available": control_video is not None,
            **(await _evidence_counts(session)),
        },
        "observational_only": True,
    }


async def list_experiments(session: AsyncSession) -> list[dict[str, Any]]:
    rows = list(
        (
            await session.execute(
                select(ContentExperiment).order_by(ContentExperiment.created_at.desc())
            )
        ).scalars()
    )
    output: list[dict[str, Any]] = []
    for experiment in rows:
        project = await session.get(ContentProject, experiment.project_id)
        video = (
            await session.execute(
                select(PublishedVideo).where(PublishedVideo.project_id == experiment.project_id)
            )
        ).scalar_one_or_none()
        windows: dict[str, str | None] = {window.value: None for window in AnalyticsWindowType}
        if video:
            for row in (
                await session.execute(
                    select(YouTubeVideoWindowMetric).where(
                        YouTubeVideoWindowMetric.published_video_id == video.id
                    )
                )
            ).scalars():
                windows[row.window_type] = row.status
        item = serialize_experiment(experiment, project=project)
        item.update(
            {
                "published_video": (
                    {"id": video.id, "youtube_video_id": video.youtube_video_id}
                    if video
                    else None
                ),
                "window_status": windows,
            }
        )
        output.append(item)
    return output
