"""Revision-aware production authorization and review evidence."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from app.models.content import (
    ContentProject,
    ProjectTransition,
    PublishingJob,
    Script,
    VideoRender,
)
from app.models.enums import ExperimentStatus, JobStatus, ProjectStatus, RenderStatus
from app.models.job import BackgroundJob
from app.schemas.experiments import ExperimentCreate
from app.services import experiments, production_lifecycle


async def _approved_project_with_produced_experiment(session):
    experiment = await experiments.create_experiment(
        session,
        ExperimentCreate(
            hypothesis="A signed-answer diagram will clarify DNSSEC.",
            primary_variable="visual explanation",
            planned_topic_category="Cybersecurity",
            planned_topic="How DNSSEC protects against cache poisoning",
            planned_hook_type="question",
            planned_duration_min_seconds=30,
            planned_duration_max_seconds=35,
            planned_audience_level="beginner_general",
            planned_technical_depth="introductory",
        ),
    )
    await experiments.plan_experiment(session, experiment.id)
    await experiments.mark_in_production(session, experiment.project_id)
    await experiments.mark_produced(session, experiment.project_id)
    project = await session.get(ContentProject, experiment.project_id)
    assert project is not None
    project.status = ProjectStatus.SCRIPT_APPROVED.value
    return project, experiment


async def test_revision_after_rejected_render_can_produce_without_losing_history(
    client, session
) -> None:
    project, experiment = await _approved_project_with_produced_experiment(session)
    old_script = Script(
        project_id=project.id,
        version=1,
        is_current=False,
        narration="Old narration.",
        authoring_mode="local_llm",
    )
    current_script = Script(
        project_id=project.id,
        version=2,
        is_current=True,
        narration="Approved revised narration.",
        authoring_mode="local_llm",
    )
    session.add_all([old_script, current_script])
    await session.flush()
    project.current_script_id = current_script.id

    old_render = VideoRender(
        project_id=project.id,
        script_id=old_script.id,
        status=RenderStatus.SUCCEEDED.value,
        output_path="/media/history/rejected-v1.mp4",
    )
    old_job = BackgroundJob(
        job_type="content.produce_video",
        payload={"project_id": str(project.id), "script_id": str(old_script.id)},
        status=JobStatus.SUCCEEDED.value,
        project_id=project.id,
        idempotency_key=f"content.produce_video:{project.id}:{old_script.id}:attempt:1",
        finished_at=datetime.now(UTC),
    )
    session.add_all([old_render, old_job])
    await session.flush()
    session.add(
        ProjectTransition(
            project_id=project.id,
            from_status=ProjectStatus.VIDEO_REVIEW.value,
            to_status=ProjectStatus.NEEDS_REVISION.value,
            actor="HUMAN",
            reason="Rejected render requires revision",
        )
    )
    project.current_render_id = old_render.id
    await session.commit()

    before = await client.get(f"/api/v1/projects/{project.id}")
    assert before.status_code == 200
    state = before.json()
    assert state["production"]["current_script_version"] == 2
    assert state["production"]["latest_rendered_script_version"] == 1
    assert state["production"]["production_required"] is True
    assert state["production"]["can_start_production"] is True
    assert state["production"]["action_label"] == "Produce Approved Revision — Script v2"
    assert state["render"]["current_script_match"] is False
    assert (
        state["production"]["latest_render_review_state"]
        == ProjectStatus.NEEDS_REVISION.value
    )

    first = await client.post(f"/api/v1/experiments/{experiment.id}/launch")
    repeated = await client.post(f"/api/v1/experiments/{experiment.id}/launch")
    assert first.status_code == repeated.status_code == 200
    assert first.json()["created"] is True
    assert repeated.json()["created"] is False
    assert repeated.json()["job_id"] == first.json()["job_id"]

    jobs = list(
        (
            await session.execute(
                select(BackgroundJob)
                .where(BackgroundJob.project_id == project.id)
                .order_by(BackgroundJob.created_at)
            )
        ).scalars()
    )
    assert len(jobs) == 2
    assert production_lifecycle.job_script_id(jobs[0]) == old_script.id
    assert production_lifecycle.job_script_id(jobs[1]) == current_script.id
    assert jobs[1].payload["script_version"] == 2

    # A terminal technical failure remains in history and permits a new,
    # explicitly requested attempt for the same approved revision.
    jobs[1].status = JobStatus.FAILED.value
    project = await session.get(ContentProject, project.id)
    assert project is not None
    project.status = ProjectStatus.FAILED.value
    await session.commit()
    retry = await client.post(f"/api/v1/experiments/{experiment.id}/launch")
    assert retry.status_code == 200
    assert retry.json()["created"] is True
    assert retry.json()["job_id"] != first.json()["job_id"]

    jobs = list(
        (
            await session.execute(
                select(BackgroundJob).where(BackgroundJob.project_id == project.id)
            )
        ).scalars()
    )
    assert len(jobs) == 3
    new_job = next(job for job in jobs if str(job.id) == retry.json()["job_id"])
    new_job.status = JobStatus.SUCCEEDED.value
    new_render = VideoRender(
        project_id=project.id,
        script_id=current_script.id,
        status=RenderStatus.SUCCEEDED.value,
        output_path="/media/renders/revision-v2.mp4",
    )
    session.add(new_render)
    await session.flush()
    project.current_render_id = new_render.id
    project.status = ProjectStatus.VIDEO_REVIEW.value
    await session.commit()

    after = (await client.get(f"/api/v1/projects/{project.id}")).json()
    assert after["render"]["script_version"] == 2
    assert after["render"]["current_script_match"] is True
    assert after["production"]["production_required"] is False
    assert [item["script_version"] for item in after["render_history"]] == [2, 1]
    assert await session.scalar(select(PublishingJob)) is None
    await session.refresh(experiment)
    assert experiment.status == ExperimentStatus.PRODUCED.value
    assert experiment.locked_at is not None


async def test_successful_render_for_current_script_hides_production_action(
    client, session
) -> None:
    project, _experiment = await _approved_project_with_produced_experiment(session)
    script = Script(
        project_id=project.id,
        version=1,
        is_current=True,
        narration="Current narration.",
        authoring_mode="local_llm",
    )
    session.add(script)
    await session.flush()
    project.current_script_id = script.id
    render = VideoRender(
        project_id=project.id,
        script_id=script.id,
        status=RenderStatus.SUCCEEDED.value,
        output_path="/media/renders/current.mp4",
    )
    session.add(render)
    await session.flush()
    project.current_render_id = render.id
    await session.commit()

    detail = (await client.get(f"/api/v1/projects/{project.id}")).json()
    assert detail["production"]["current_script_rendered"] is True
    assert detail["production"]["production_required"] is False
    assert detail["production"]["can_start_production"] is False
