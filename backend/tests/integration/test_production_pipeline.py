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


# ------------------------------------------------- semantic narration blocks ---
class _CountingTTS:
    """A TTS stand-in that records what it was asked to say."""

    name = "counting"

    def __init__(self, prefers_whole_block: bool) -> None:
        self.prefers_whole_block = prefers_whole_block
        self.calls: list[str] = []

    async def list_voices(self) -> list[str]:
        return ["test"]

    async def synthesize(self, text, voice, out_path):
        from app.providers.base import AudioResult

        self.calls.append(text)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"")
        return AudioResult(
            path=out_path, duration_seconds=2.0, sample_rate=24000,
            provider=self.name, voice="test", model="test",
        )


async def revise_first_video_for(session, project) -> None:
    from app.services.revise_first_video import revise_first_video

    await revise_first_video(session, project)
    await session.commit()


async def _current_script(session, project) -> Script:
    """Re-select rather than reuse a returned object.

    A commit expires it, and `script.scenes` would then lazy-load outside the
    async context. `produce()` re-selects for the same reason.
    """
    return (
        await session.execute(
            select(Script).where(Script.project_id == project.id, Script.is_current.is_(True))
        )
    ).scalar_one()


async def _run_narration(session, monkeypatch, provider, tmp_path: Path) -> tuple[list, object]:
    from app.services import production

    project = await seed_first_video(session)
    await revise_first_video_for(session, project)
    script = await _current_script(session, project)

    monkeypatch.setattr(production, "ResilientTTS", lambda **kw: provider)
    # Write into the test's own directory. `project_dir` resolves under
    # MEDIA_ROOT, which in a container is the real media volume — a test has no
    # business leaving stub audio in there next to genuine renders.
    monkeypatch.setattr(production, "project_dir", lambda project_id: tmp_path / str(project_id))
    # Block preparation shells out to ffmpeg; this test is about how many
    # requests are made and what they contain, not about audio processing.
    monkeypatch.setattr(production, "_prepare_block", _fixed_duration)
    monkeypatch.setattr(production, "_concat_segments", _noop_concat)

    parts = await production.synthesize_narration(session, project, script)
    return parts, script


async def _fixed_duration(path) -> float:
    return 2.0


async def _noop_concat(parts, out_path, tail: float = 0.0) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"")


async def test_whole_block_provider_gets_one_request_per_scene(
    session, monkeypatch, tmp_path: Path
) -> None:
    """The reason this matters: Gemini's preview tier allows a couple of
    requests per minute, and clause-level synthesis made fourteen of them for a
    five-scene Short."""
    provider = _CountingTTS(prefers_whole_block=True)
    parts, script = await _run_narration(session, monkeypatch, provider, tmp_path)

    scenes = sorted(script.scenes, key=lambda s: s.scene_number)
    assert len(provider.calls) == len(scenes) == len(parts)
    assert 3 <= len(provider.calls) <= 5, "a Short should be a handful of semantic blocks"


async def test_each_block_is_a_whole_scene_of_narration(
    session, monkeypatch, tmp_path: Path
) -> None:
    """Not one flat paragraph, and not one clause at a time: one beat at a time,
    with its punctuation intact so the model can find the rhythm itself."""
    provider = _CountingTTS(prefers_whole_block=True)
    _, script = await _run_narration(session, monkeypatch, provider, tmp_path)

    scenes = sorted(script.scenes, key=lambda s: s.scene_number)
    assert provider.calls == [s.narration for s in scenes]
    assert any(text.count(".") + text.count("?") > 1 for text in provider.calls), (
        "blocks should contain multiple sentences, or they are not semantic blocks"
    )


async def test_clause_provider_still_gets_segmented_text(
    session, monkeypatch, tmp_path: Path
) -> None:
    """Kokoro's cadence needs the pipeline to shape its pauses, so the old
    segment-level path must survive for the fallback."""
    provider = _CountingTTS(prefers_whole_block=False)
    _, script = await _run_narration(session, monkeypatch, provider, tmp_path)

    assert len(provider.calls) > len(script.scenes)
    for text in provider.calls:
        assert text.strip()


# ------------------------------------------------------ one voice per video ---
class _FailsAfter(_CountingTTS):
    """Succeeds for `ok` blocks, then fails transiently.

    A *transient* failure on purpose. Daily quota exhaustion takes a stricter
    path — it blocks the render rather than switching voice — and is covered in
    tests/integration/test_quota_preflight.py.
    """

    name = "flaky-primary"

    def __init__(self, ok: int) -> None:
        super().__init__(prefers_whole_block=True)
        self.ok = ok

    async def synthesize(self, text, voice, out_path):
        from app.core.errors import RetryableError

        if len(self.calls) >= self.ok:
            self.calls.append(text)
            raise RetryableError("503 from the service")
        return await super().synthesize(text, voice, out_path)


async def test_a_mid_render_failure_re_reads_the_whole_script(
    session, monkeypatch, tmp_path: Path
) -> None:
    """The defect this prevents: scene 1 in one voice, scenes 2-5 in another.

    Falling back per block is locally correct and globally unusable — a narrator
    that changes a quarter of the way through is worse than either voice used
    throughout. The whole script is re-read instead.
    """
    from app.services import production

    primary = _FailsAfter(ok=1)
    fallback = _CountingTTS(prefers_whole_block=False)

    project = await seed_first_video(session)
    await revise_first_video_for(session, project)
    script = await _current_script(session, project)

    monkeypatch.setattr(production, "ResilientTTS", lambda **kw: primary)
    monkeypatch.setattr(production, "build_provider", lambda name: fallback)
    monkeypatch.setattr(production, "project_dir", lambda pid: tmp_path / str(pid))
    monkeypatch.setattr(production, "_prepare_block", _fixed_duration)
    monkeypatch.setattr(production, "_concat_segments", _noop_concat)

    parts = await production.synthesize_narration(session, project, script)

    voices = {(p.provenance or {}).get("provider") for p in parts}
    assert voices == {"counting"}, f"video must have one narrator, got {voices}"
    assert all((p.provenance or {}).get("fallback_used") for p in parts)
    assert all((p.provenance or {}).get("fallback_from") for p in parts)
    # Every scene was re-read, not just the ones that failed.
    assert len(fallback.calls) >= len(script.scenes)


async def test_no_fallback_configured_surfaces_the_failure(
    session, monkeypatch, tmp_path: Path
) -> None:
    """With TTS_FALLBACK_PROVIDER=none there is no second voice to switch to."""
    from app.core.errors import RetryableError
    from app.services import production

    primary = _FailsAfter(ok=0)

    project = await seed_first_video(session)
    await revise_first_video_for(session, project)
    script = await _current_script(session, project)

    monkeypatch.setattr(production.settings, "TTS_FALLBACK_PROVIDER", "none")
    monkeypatch.setattr(production, "ResilientTTS", lambda **kw: primary)
    monkeypatch.setattr(production, "project_dir", lambda pid: tmp_path / str(pid))
    monkeypatch.setattr(production, "_prepare_block", _fixed_duration)

    with pytest.raises(RetryableError):
        await production.synthesize_narration(session, project, script)
