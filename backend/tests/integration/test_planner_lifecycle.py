"""Durable Phase 6A resume and human-selection API coverage."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7
from app.models.channel import Channel
from app.models.content import ContentProject, Scene, Script
from app.models.experiments import ContentExperiment
from app.models.planner import ContentProposal, PlannerRun
from app.schemas.planner import SceneSpecSet, ScriptDraft
from app.services.script_revisions import create_review_revision


async def _failed_run(session: AsyncSession) -> tuple[PlannerRun, list[ContentProposal]]:
    run = PlannerRun(
        trigger="MANUAL",
        status="FAILED",
        provider="ollama",
        planner_version="6A.0",
        context_snapshot={"analytics": {"evidence_status": "LIMITED"}},
        error_message="script validation failed",
    )
    session.add(run)
    await session.flush()
    proposals = []
    for rank in (1, 2):
        row = ContentProposal(
            id=uuid7(),
            planner_run_id=run.id,
            rank=rank,
            state="SELECTED" if rank == 1 else "CANDIDATE",
            selected=rank == 1,
            topic=f"Candidate topic {rank}",
            topic_key=f"candidate topic {rank}",
            content_pillar="internet infrastructure",
            core_question="How does this mechanism work?",
            hook="A concise and surprising technical hook.",
            hook_type="curiosity_gap",
            target_duration_seconds=35,
            rationale="It fits the channel and can be explained visually.",
            why_now="It remains useful foundational knowledge.",
            novelty_assessment="No deterministic duplicate found.",
            research_feasibility="Primary standards documents are available.",
            visual_feasibility="A simple flow diagram communicates the mechanism.",
            experiment_hypothesis="A mechanism-first explanation may improve comprehension.",
            primary_variable="explanation_pattern",
            evidence_limitations="Channel evidence remains limited.",
            scoring={"duplicate_guard": 3},
        )
        session.add(row)
        proposals.append(row)
    await session.commit()
    return run, proposals


async def test_failed_run_resumes_without_regenerating_candidates(
    client: AsyncClient, session: AsyncSession
) -> None:
    run, proposals = await _failed_run(session)
    response = await client.post(f"/api/v1/planner/runs/{run.id}/resume")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["error"] is None
    assert body["previous_error"] == "script validation failed"
    assert [item["id"] for item in body["proposals"]] == [str(row.id) for row in proposals]
    assert body["job_id"]

    repeated = await client.post(f"/api/v1/planner/runs/{run.id}/resume")
    assert repeated.status_code == 409


async def test_human_override_preserves_ai_choice_and_queues_selected_candidate(
    client: AsyncClient, session: AsyncSession
) -> None:
    run, proposals = await _failed_run(session)
    response = await client.post(
        f"/api/v1/planner/runs/{run.id}/proposals/{proposals[1].id}/select"
    )
    assert response.status_code == 202
    body = response.json()
    first, second = body["proposals"]
    assert first["ai_selected"] is True
    assert first["selected"] is False
    assert second["selected"] is True
    assert "Original AI recommendation" in body["decision_summary"]
    assert body["job_id"]


async def test_human_override_supersedes_existing_unlocked_draft(
    client: AsyncClient, session: AsyncSession
) -> None:
    run, proposals = await _failed_run(session)
    channel = (await session.execute(select(Channel).limit(1))).scalar_one()
    old_project = ContentProject(
        channel_id=channel.id,
        topic=proposals[0].topic,
        status="SCRIPT_REVIEW",
        target_duration_seconds=35,
    )
    session.add(old_project)
    await session.flush()
    proposals[0].project_id = old_project.id
    old_experiment = ContentExperiment(
        project_id=old_project.id,
        status="DRAFT",
        hypothesis="The first explanation may help viewers understand the mechanism.",
        primary_variable="explanation_pattern",
    )
    session.add(old_experiment)
    await session.commit()

    response = await client.post(
        f"/api/v1/planner/runs/{run.id}/proposals/{proposals[1].id}/select"
    )

    assert response.status_code == 202
    await session.refresh(old_project)
    await session.refresh(old_experiment)
    assert old_project.status == "REJECTED"
    assert old_experiment.status == "CANCELLED"
    assert response.json()["proposals"][1]["selected"] is True


async def test_review_revision_preserves_generated_script_and_replaces_current(
    session: AsyncSession,
) -> None:
    channel = (await session.execute(select(Channel).limit(1))).scalar_one()
    project = ContentProject(
        channel_id=channel.id,
        topic="A planner topic",
        status="SCRIPT_REVIEW",
        target_duration_seconds=35,
    )
    session.add(project)
    await session.flush()
    original = Script(
        project_id=project.id,
        version=1,
        is_current=True,
        narration="Original generated draft.",
        authoring_mode="local_llm",
    )
    session.add(original)
    await session.flush()
    project.current_script_id = original.id

    narration = (
        "one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen"
    )
    draft = ScriptDraft.model_validate(
        {
            "title": "Reviewed factual title",
            "description": "A sufficiently detailed reviewed description for this script.",
            "scenes": [
                {"scene_number": number, "narration": narration}
                for number in range(1, 6)
            ],
        }
    )
    specs = SceneSpecSet.model_validate(
        {
            "scenes": [
                {
                    "scene_number": number,
                    "visual_type": "FLOW",
                    "visual_purpose": "Show the reviewed mechanism clearly.",
                    "labels": ["REVIEWED"],
                    "nodes": [{"id": "reviewed", "label": "REVIEWED"}],
                    "connectors": [],
                    "timing_anchors": [
                        {"id": "start", "phrase": "one two", "intent": "reveal"}
                    ],
                    "animation_intent": "Reveal the reviewed concept.",
                }
                for number in range(1, 6)
            ]
        }
    )

    revised = await create_review_revision(
        session,
        project=project,
        draft=draft,
        scene_specs=specs,
        review_notes="Factual correction",
        words_per_minute=180.0,
    )
    await session.flush()

    assert revised.version == 2
    assert revised.word_count == 90
    assert revised.estimated_duration_seconds == 30
    assert project.current_script_id == revised.id
    assert original.is_current is False
    scenes = list(
        (
            await session.execute(
                select(Scene).where(Scene.script_id == revised.id).order_by(Scene.scene_number)
            )
        ).scalars()
    )
    assert len(scenes) == 5
    assert all(scene.template_id == "generic_scene_spec" for scene in scenes)


@pytest.mark.parametrize(
    ("decision", "expected"), [("revise", "NEEDS_REVISION"), ("reject", "REJECTED")]
)
async def test_script_review_actions_use_real_project_transitions(
    client: AsyncClient,
    session: AsyncSession,
    decision: str,
    expected: str,
) -> None:
    channel = (await session.execute(select(Channel).limit(1))).scalar_one()
    project = ContentProject(
        channel_id=channel.id,
        topic="Reviewable planner draft",
        status="SCRIPT_REVIEW",
        target_duration_seconds=35,
    )
    session.add(project)
    await session.commit()

    response = await client.post(
        f"/api/v1/projects/{project.id}/review-script",
        json={"decision": decision, "notes": "Human review note"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == expected
