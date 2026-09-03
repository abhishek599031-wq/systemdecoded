"""Production pipeline against the database, and one genuine end-to-end render.

The media test is marked and skipped when FFmpeg/Chromium/Kokoro are absent, so
`pytest` still works in the slim API image. Run the full set inside the worker
container, which is the only image carrying the media stack:

    docker compose exec worker pytest tests/ -m media
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.errors import InvalidStateTransition
from app.core.state_machine import transition
from app.models.content import (
    ContentProject,
    ProductionAsset,
    ProjectTransition,
    PublishedVideo,
    PublishingJob,
    QualityCheck,
    ResearchNote,
    Scene,
    Script,
    VideoRender,
)
from app.models.enums import (
    AssetOrigin,
    AssetType,
    ProjectStatus,
    PublishingMode,
    PublishState,
    QualityVerdict,
    RenderStatus,
)
from app.services import publishing, quality
from app.services.seed_first_video import TOPIC_KEY, seed_first_video


def media_stack_available() -> bool:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        return False
    import importlib.util as u

    return all(u.find_spec(m) for m in ("kokoro_onnx", "faster_whisper", "playwright"))


requires_media = pytest.mark.skipif(
    not media_stack_available(),
    reason="FFmpeg/Chromium/Kokoro not present; run inside the worker container",
)


# --------------------------------------------------------------------- seed ---
async def test_seed_creates_a_complete_project(session) -> None:
    project = await seed_first_video(session)
    await session.commit()

    assert project.topic_key == TOPIC_KEY
    assert project.status == ProjectStatus.IDEA

    script = (
        await session.execute(select(Script).where(Script.project_id == project.id))
    ).scalar_one()
    assert script.selected_hook
    assert script.title_candidates
    assert script.word_count > 0

    scenes = (
        await session.execute(
            select(Scene).where(Scene.script_id == script.id).order_by(Scene.scene_number)
        )
    ).scalars().all()
    assert len(scenes) >= 4
    assert [s.scene_number for s in scenes] == list(range(1, len(scenes) + 1))
    # Timings must be absent until real audio exists.
    assert all(s.start_seconds is None for s in scenes)


async def test_seed_is_idempotent(session) -> None:
    first = await seed_first_video(session)
    await session.commit()
    second = await seed_first_video(session)
    assert first.id == second.id


async def test_every_claim_has_a_source(session) -> None:
    """The factual-grounding gate is meaningless if claims float free."""
    project = await seed_first_video(session)
    await session.commit()
    notes = (
        await session.execute(select(ResearchNote).where(ResearchNote.project_id == project.id))
    ).scalars().all()
    assert notes
    assert all(n.source_id is not None for n in notes)


async def test_scenes_reference_existing_templates(session) -> None:
    from app.config import settings

    project = await seed_first_video(session)
    await session.commit()
    script = (
        await session.execute(select(Script).where(Script.project_id == project.id))
    ).scalar_one()
    for scene in script.scenes:
        template = settings.SCENE_TEMPLATES_DIR / scene.template_id / "index.html"
        assert template.exists(), f"scene {scene.scene_number} -> missing {scene.template_id}"


# ------------------------------------------------------------ state machine ---
async def _transitions(session, project) -> list[ProjectTransition]:
    """Query the audit rows directly.

    Deliberately not via `project.transitions`: the relationship was never
    populated on a freshly created object, so touching it triggers a lazy load
    and MissingGreenlet under the async driver.
    """
    return list(
        (
            await session.execute(
                select(ProjectTransition)
                .where(ProjectTransition.project_id == project.id)
                .order_by(ProjectTransition.created_at)
            )
        ).scalars().all()
    )


async def test_transition_records_an_audit_row(session) -> None:
    project = await seed_first_video(session)
    await session.commit()

    await transition(session, project, ProjectStatus.IDEA_APPROVED, actor="HUMAN", reason="ok")
    await session.commit()

    assert project.status == ProjectStatus.IDEA_APPROVED
    rows = await _transitions(session, project)
    assert rows[-1].from_status == ProjectStatus.IDEA
    assert rows[-1].to_status == ProjectStatus.IDEA_APPROVED
    assert rows[-1].actor == "HUMAN"
    assert rows[-1].reason == "ok"


async def test_illegal_transition_is_refused(session) -> None:
    project = await seed_first_video(session)
    await session.commit()
    with pytest.raises(InvalidStateTransition):
        await transition(session, project, ProjectStatus.PUBLISHED)


async def test_transition_to_the_same_state_is_a_no_op(session) -> None:
    project = await seed_first_video(session)
    await session.commit()
    before = len(await _transitions(session, project))
    await transition(session, project, ProjectStatus.IDEA)
    await session.commit()
    assert len(await _transitions(session, project)) == before


# ------------------------------------------------------------------ quality ---
async def _project_with_render(session, **render_kwargs) -> tuple[ContentProject, VideoRender]:
    project = await seed_first_video(session)
    script = (
        await session.execute(select(Script).where(Script.project_id == project.id))
    ).scalar_one()
    render = VideoRender(
        project_id=project.id, script_id=script.id,
        status=RenderStatus.SUCCEEDED.value, **render_kwargs,
    )
    session.add(render)
    await session.flush()
    project.current_render_id = render.id
    await session.commit()
    return project, render


async def test_quality_fails_when_the_file_is_missing(session) -> None:
    project, render = await _project_with_render(session, output_path="/media/does-not-exist.mp4")
    check = await quality.run_quality_checks(session, project, render)
    await session.commit()

    assert check.verdict == QualityVerdict.FAIL
    assert any("video_exists" in issue for issue in check.blocking_issues)


async def test_quality_check_is_persisted_and_linked(session) -> None:
    project, render = await _project_with_render(session, output_path="/media/missing.mp4")
    check = await quality.run_quality_checks(session, project, render)
    await session.commit()

    stored = (
        await session.execute(select(QualityCheck).where(QualityCheck.render_id == render.id))
    ).scalar_one()
    assert stored.id == check.id
    assert stored.checks


# --------------------------------------------------------------- publishing ---
async def test_publishing_package_defaults_to_manual_handoff(session) -> None:
    """API upload would be permanently private until the audit passes."""
    project, render = await _project_with_render(
        session, output_path="/media/x.mp4", width=1080, height=1920
    )
    job = await publishing.create_handoff_package(session, project, render)
    await session.commit()

    assert job.provider_mode == PublishingMode.MANUAL_HANDOFF
    assert job.state == PublishState.AWAITING_HUMAN_UPLOAD
    assert job.title
    assert job.idempotency_key
    assert job.contains_synthetic_media is True
    assert "locked to private" in job.publishing_notes or "unaudited" in job.publishing_notes


async def test_publishing_package_is_idempotent(session) -> None:
    project, render = await _project_with_render(session, output_path="/media/x.mp4")
    first = await publishing.create_handoff_package(session, project, render)
    await session.commit()
    second = await publishing.create_handoff_package(session, project, render)
    await session.commit()
    assert first.id == second.id


async def test_only_one_live_publishing_job_per_project(session) -> None:
    """Database-level guard against a duplicate upload (ARCH §13.4)."""
    from sqlalchemy.exc import IntegrityError

    project, render = await _project_with_render(session, output_path="/media/x.mp4")
    await publishing.create_handoff_package(session, project, render)
    await session.commit()

    session.add(
        PublishingJob(
            project_id=project.id, render_id=render.id,
            provider_mode=PublishingMode.MANUAL_HANDOFF.value,
            state=PublishState.PENDING.value, idempotency_key="different-key",
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_description_cites_its_sources(session) -> None:
    project, render = await _project_with_render(session, output_path="/media/x.mp4")
    job = await publishing.create_handoff_package(session, project, render)
    await session.commit()
    assert "Sources:" in job.description
    assert "RFC 6238" in job.description


async def test_recording_a_published_video_is_idempotent(session) -> None:
    project, render = await _project_with_render(session, output_path="/media/x.mp4")
    await publishing.create_handoff_package(session, project, render)
    await session.commit()

    first = await publishing.record_published_video(session, project, "abc123")
    await session.commit()
    second = await publishing.record_published_video(session, project, "abc123")
    await session.commit()

    assert first.id == second.id
    rows = (await session.execute(select(PublishedVideo))).scalars().all()
    assert len(rows) == 1


async def test_recording_publication_closes_the_publishing_job(session) -> None:
    project, render = await _project_with_render(session, output_path="/media/x.mp4")
    job = await publishing.create_handoff_package(session, project, render)
    await session.commit()

    await publishing.record_published_video(session, project, "vid-999")
    await session.commit()
    await session.refresh(job)

    assert job.state == PublishState.DONE
    assert job.youtube_video_id == "vid-999"


# ------------------------------------------------------------------- assets ---
async def test_recorded_assets_always_declare_provenance(session, tmp_path: Path) -> None:
    """The licensing gate depends on this being impossible to omit."""
    from app.services.production import record_asset

    project = await seed_first_video(session)
    await session.commit()

    sample = tmp_path / "frame.png"
    sample.write_bytes(b"not-really-a-png")

    asset = await record_asset(
        session, project_id=project.id, asset_type=AssetType.SCENE_FRAME, path=sample
    )
    await session.commit()

    assert asset.origin == AssetOrigin.GENERATED
    assert asset.license
    assert asset.checksum
    assert asset.bytes == len(b"not-really-a-png")


async def test_quality_flags_assets_without_a_licence(session, tmp_path: Path) -> None:
    project, render = await _project_with_render(session, output_path="/media/missing.mp4")
    session.add(
        ProductionAsset(
            project_id=project.id, asset_type=AssetType.MUSIC.value,
            origin=AssetOrigin.LICENSED.value, license="",
            file_path=str(tmp_path / "track.mp3"),
        )
    )
    await session.commit()

    check = await quality.run_quality_checks(session, project, render)
    await session.commit()
    assert check.verdict == QualityVerdict.FAIL


# --------------------------------------------------------- real media render ---
@pytest.mark.media
@requires_media
async def test_end_to_end_render_produces_a_real_mp4(session) -> None:
    """The one test that exercises Kokoro, Whisper, Chromium and FFmpeg for real.

    Deliberately not mocked: every previous defect in this pipeline (a filter
    graph missing separators, washed-out keyframes, captions crossing clause
    boundaries) was invisible to mocks and only showed up in an actual file.
    """
    from app.providers.compositor.ffmpeg import probe
    from app.services import production
    from app.services.revise_first_video import revise_first_video

    project = await seed_first_video(session)
    # Exercise the current script version, which is what production renders.
    await revise_first_video(session, project)
    for target in [
        ProjectStatus.IDEA_APPROVED, ProjectStatus.RESEARCHING, ProjectStatus.RESEARCH_READY,
        ProjectStatus.SCRIPT_GENERATING, ProjectStatus.SCRIPT_REVIEW,
        ProjectStatus.SCRIPT_APPROVED, ProjectStatus.PRODUCTION_PLANNING,
        ProjectStatus.ASSETS_READY, ProjectStatus.RENDERING,
    ]:
        await transition(session, project, target, reason="test")
    await session.commit()

    render = await production.produce(session, project)
    await session.commit()

    assert render.status == RenderStatus.SUCCEEDED
    output = Path(render.output_path)
    assert output.exists() and output.stat().st_size > 100_000

    probed = await probe(output)
    assert (probed.width, probed.height) == (1080, 1920)
    assert probed.has_audio
    assert probed.video_codec == "h264"
    assert probed.audio_codec == "aac"
    assert 20 <= probed.duration <= 45
    # The picture must span the whole file. A short video stream is invisible
    # to every other assertion, because container duration follows the longest
    # stream — this is the check that catches an incomplete frame sequence.
    assert probed.duration - probed.video_duration <= 0.25, (
        f"video stream {probed.video_duration:.2f}s vs file {probed.duration:.2f}s"
    )

    # Timings must have come from the audio, not from an estimate.
    script = (
        await session.execute(
            select(Script).where(Script.project_id == project.id, Script.is_current.is_(True))
        )
    ).scalar_one()
    assert all(s.start_seconds is not None and s.end_seconds is not None for s in script.scenes)

    check = await quality.run_quality_checks(session, project, render)
    await session.commit()
    assert check.verdict != QualityVerdict.FAIL, check.blocking_issues
