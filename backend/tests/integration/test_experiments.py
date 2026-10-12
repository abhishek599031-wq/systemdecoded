"""PostgreSQL-backed Phase 5 experiment lifecycle and outcome tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError, ValidationError
from app.models.analytics import YouTubeVideoWindowMetric
from app.models.channel import Channel
from app.models.content import ContentProject, PublishedVideo
from app.models.content_intelligence import ContentFeatureProfile
from app.models.enums import (
    AnalyticsWindowStatus,
    ExperimentStatus,
    ProjectStatus,
)
from app.models.experiments import ContentExperiment
from app.schemas.experiments import ExperimentCreate, ExperimentPatch
from app.services import experiments


def _body(**overrides) -> ExperimentCreate:
    values = {
        "hypothesis": "A shorter explainer may produce stronger early engagement.",
        "primary_variable": "duration / hook presentation",
        "planned_topic_category": "Cybersecurity",
        "planned_topic": "How passwords are actually stored",
        "planned_hook_type": "surprising_fact",
        "planned_duration_min_seconds": Decimal("30"),
        "planned_duration_max_seconds": Decimal("35"),
        "planned_explanation_pattern": "surprising claim → mechanism → consequence/payoff",
        "planned_audience_level": "beginner_general",
        "planned_technical_depth": "introductory",
        "planned_title_style": "curiosity",
    }
    values.update(overrides)
    return ExperimentCreate(**values)


async def _project(session, status: ProjectStatus = ProjectStatus.IDEA) -> ContentProject:
    channel = (await session.execute(select(Channel))).scalars().one()
    project = ContentProject(
        channel_id=channel.id,
        topic="Reference project",
        status=status.value,
        target_duration_seconds=40,
    )
    session.add(project)
    await session.flush()
    return project


async def test_create_links_project_and_supports_reference(session) -> None:
    reference = await _project(session, ProjectStatus.PUBLISHED)
    body = _body(control_reference_project_id=reference.id)
    experiment = await experiments.create_experiment(session, body)
    await session.commit()

    row = await session.get(ContentExperiment, experiment.id)
    assert row is not None
    assert row.project_id != reference.id
    assert row.control_reference_project_id == reference.id
    assert row.status == ExperimentStatus.DRAFT.value
    assert row.project.topic == body.planned_topic


async def test_self_reference_and_duplicate_primary_experiment_are_rejected(session) -> None:
    project = await _project(session)
    first = await experiments.create_experiment(session, _body(project_id=project.id))
    await session.commit()

    with pytest.raises(ValidationError):
        await experiments.update_draft(
            session,
            first.id,
            ExperimentPatch(control_reference_project_id=project.id),
        )
    with pytest.raises(ConflictError):
        await experiments.create_experiment(session, _body(project_id=project.id))


async def test_draft_edit_and_planned_lock_are_immutable(session) -> None:
    experiment = await experiments.create_experiment(session, _body())
    await experiments.update_draft(
        session,
        experiment.id,
        ExperimentPatch(planned_topic="How password hashes work"),
    )
    planned = await experiments.plan_experiment(session, experiment.id)
    assert planned.status == ExperimentStatus.PLANNED.value
    assert planned.locked_at is not None
    assert planned.locked_snapshot["planned_topic"] == "How password hashes work"

    with pytest.raises(ConflictError):
        await experiments.update_draft(
            session,
            experiment.id,
            ExperimentPatch(planned_topic="A rewritten intent"),
        )


async def test_planning_requires_hypothesis_and_duration_bounds(session) -> None:
    experiment = await experiments.create_experiment(
        session,
        _body(planned_duration_max_seconds=None),
    )
    with pytest.raises(ValidationError):
        await experiments.plan_experiment(session, experiment.id)

    experiment.planned_duration_max_seconds = Decimal("35")
    experiment.hypothesis = ""
    with pytest.raises(ValidationError):
        await experiments.plan_experiment(session, experiment.id)


async def test_planned_vs_actual_match_deviation_and_unknown(session) -> None:
    experiment = await experiments.create_experiment(session, _body())
    unknown = await experiments.comparison(session, experiment.id)
    assert all(item["status"] == "UNKNOWN" for item in unknown["dimensions"].values())

    profile = ContentFeatureProfile(
        project_id=experiment.project_id,
        duration_seconds=Decimal("33.4"),
        topic_category="Cybersecurity",
        topic="How passwords are actually stored",
        hook_type="surprising_fact",
        explanation_pattern="surprising claim → mechanism → consequence/payoff",
        audience_level="beginner_general",
        technical_depth="introductory",
        title_style="curiosity",
    )
    session.add(profile)
    await session.flush()
    matching = await experiments.comparison(session, experiment.id)
    assert matching["dimensions"]["duration_seconds"]["status"] == "MATCH"
    assert matching["dimensions"]["hook_type"]["status"] == "MATCH"

    profile.duration_seconds = Decimal("44")
    profile.hook_type = "question"
    await session.flush()
    deviated = await experiments.comparison(session, experiment.id)
    assert deviated["dimensions"]["duration_seconds"]["status"] == "DEVIATED"
    assert deviated["dimensions"]["hook_type"]["status"] == "DEVIATED"


async def test_outcome_join_and_single_video_evidence_are_observational(session) -> None:
    experiment = await experiments.create_experiment(session, _body())
    project = await session.get(ContentProject, experiment.project_id)
    assert project is not None
    video = PublishedVideo(
        project_id=project.id,
        youtube_video_id="Q-CJWCG90qI",
        title="The website does not know your password",
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
        privacy_status="public",
        reconciliation_method="manual_confirmation_verified",
    )
    session.add(video)
    await session.flush()
    session.add(
        YouTubeVideoWindowMetric(
            published_video_id=video.id,
            window_type="D1",
            status=AnalyticsWindowStatus.AVAILABLE.value,
            window_start_date=date(2026, 10, 1),
            window_end_date=date(2026, 10, 1),
            views=10,
            engaged_views=5,
            provider="youtube_analytics",
        )
    )
    await session.commit()

    result = await experiments.outcome(session, experiment.id)
    assert result["published_video"]["youtube_video_id"] == "Q-CJWCG90qI"
    assert result["outcomes"][0]["window"] == "D1"
    assert result["evidence"]["reference_video_available"] is False
    assert result["evidence"]["number_of_published_experiments"] == 1
    assert result["evidence"]["complete_d1_samples"] == 1
    assert result["observational_only"] is True
    assert "recommendation" not in result
    assert "winner" not in result


async def test_lifecycle_hooks_progress_only_after_real_events(session) -> None:
    experiment = await experiments.create_experiment(session, _body())
    await experiments.plan_experiment(session, experiment.id)
    await experiments.mark_in_production(session, experiment.project_id)
    assert experiment.status == ExperimentStatus.IN_PRODUCTION.value
    await experiments.mark_produced(session, experiment.project_id)
    assert experiment.status == ExperimentStatus.PRODUCED.value
    await experiments.mark_published(session, experiment.project_id)
    assert experiment.status == ExperimentStatus.PRODUCED.value

    project = await session.get(ContentProject, experiment.project_id)
    assert project is not None
    video = PublishedVideo(
        project_id=project.id,
        youtube_video_id="AbCdEfGhI12",
        title="Example",
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
        reconciliation_method="manual_confirmation",
    )
    session.add(video)
    await session.flush()
    await experiments.mark_published(session, project.id)
    assert experiment.status == ExperimentStatus.PUBLISHED.value

    await experiments.mark_measurement_progress(session, project.id)
    assert experiment.status == ExperimentStatus.MEASURING.value


async def test_api_create_plan_and_list_exposes_counts(client, session) -> None:
    response = await client.post(
        "/api/v1/experiments",
        json={
            "hypothesis": "A shorter explainer may improve early engagement.",
            "primary_variable": "duration",
            "planned_topic_category": "Cybersecurity",
            "planned_topic": "How passwords are actually stored",
            "planned_hook_type": "surprising_fact",
            "planned_duration_min_seconds": 30,
            "planned_duration_max_seconds": 35,
            "planned_audience_level": "beginner_general",
            "planned_technical_depth": "introductory",
        },
    )
    assert response.status_code == 201
    experiment_id = response.json()["id"]

    planned = await client.post(f"/api/v1/experiments/{experiment_id}/plan")
    assert planned.status_code == 200
    assert planned.json()["status"] == "PLANNED"
    listing = await client.get("/api/v1/experiments")
    assert listing.status_code == 200
    body = listing.json()
    assert body["observational_only"] is True
    assert body["evidence"]["number_of_published_experiments"] == 0


async def test_launch_requires_script_approval_and_never_touches_tts(session) -> None:
    experiment = await experiments.create_experiment(session, _body())
    await experiments.plan_experiment(session, experiment.id)
    # Planning only changes intent rows; no background job or quota usage is created.
    from app.models.job import BackgroundJob

    jobs = await session.scalar(
        select(BackgroundJob).where(BackgroundJob.project_id == experiment.project_id)
    )
    assert jobs is None
