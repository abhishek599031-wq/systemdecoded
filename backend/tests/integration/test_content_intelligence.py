"""PostgreSQL-backed feature profile and learning-record tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.core.clock import utcnow
from app.models.analytics import YouTubeVideoWindowMetric
from app.models.channel import Channel
from app.models.content import (
    ContentProject,
    ProductionAsset,
    PublishedVideo,
    QualityCheck,
    Scene,
    Script,
    VideoRender,
)
from app.models.enums import (
    AnalyticsWindowStatus,
    AssetType,
    FeatureProvenance,
    ProjectStatus,
    RenderStatus,
)
from app.services import content_intelligence


async def _content_fixture(session):
    channel = (await session.execute(select(Channel))).scalars().one()
    project = ContentProject(
        channel_id=channel.id,
        topic="Why authenticator codes expire",
        content_format="How It Works",
        status=ProjectStatus.PUBLISHED,
    )
    session.add(project)
    await session.flush()
    script = Script(
        project_id=project.id,
        version=1,
        selected_title="Nobody sent you that code",
        selected_hook="Nobody sent you that code",
        narration="One two three four",
        word_count=4,
    )
    session.add(script)
    await session.flush()
    session.add_all(
        [
            Scene(
                script_id=script.id,
                project_id=project.id,
                scene_number=1,
                narration="One two",
                template_id="code_reveal",
                start_seconds=Decimal("0"),
                end_seconds=Decimal("1"),
            ),
            Scene(
                script_id=script.id,
                project_id=project.id,
                scene_number=2,
                narration="three four",
                template_id="diagram_flow",
                start_seconds=Decimal("1"),
                end_seconds=Decimal("2"),
            ),
        ]
    )
    render = VideoRender(
        project_id=project.id,
        script_id=script.id,
        status=RenderStatus.SUCCEEDED,
        duration_seconds=Decimal("40"),
        width=1080,
        height=1920,
        fps=30,
        spec={
            "narration": {
                "provider": "gemini",
                "model": "gemini-tts",
                "voice": "Algieba",
            }
        },
    )
    session.add(render)
    await session.flush()
    project.current_script_id = script.id
    project.current_render_id = render.id
    session.add(
        QualityCheck(
            render_id=render.id,
            project_id=project.id,
            verdict="PASS",
            checks=[
                {
                    "name": "captions_present",
                    "passed": True,
                    "blocking": True,
                    "detail": "events",
                    "measured": 9,
                }
            ],
        )
    )
    session.add(
        ProductionAsset(
            project_id=project.id,
            scene_id=(
                await session.scalar(
                    select(Scene.id)
                    .where(Scene.script_id == script.id)
                    .order_by(Scene.scene_number)
                    .limit(1)
                )
            ),
            asset_type=AssetType.NARRATION_AUDIO,
            file_path="/tmp/scene.wav",
            provider="resilient(gemini)",
            asset_metadata={"provider": "gemini", "voice": "Algieba", "speed": 1.0},
        )
    )
    video = PublishedVideo(
        project_id=project.id,
        youtube_video_id="Q-CJWCG90qI",
        title="Nobody sent you that authenticator code",
        published_at=datetime(2026, 9, 8, 8, 3, 27, tzinfo=UTC),
        privacy_status="public",
        reconciliation_method="manual_confirmation_verified",
    )
    session.add(video)
    await session.flush()
    session.add_all(
        [
            YouTubeVideoWindowMetric(
                published_video_id=video.id,
                window_type="D1",
                status=AnalyticsWindowStatus.AVAILABLE,
                window_start_date=date(2026, 9, 8),
                window_end_date=date(2026, 9, 8),
                views=12,
                engaged_views=6,
                estimated_minutes_watched=Decimal("1"),
                average_view_duration=Decimal("19"),
                provider="youtube_analytics",
                synced_at=utcnow(),
            ),
            YouTubeVideoWindowMetric(
                published_video_id=video.id,
                window_type="D28",
                status=AnalyticsWindowStatus.PENDING,
                window_start_date=date(2026, 9, 8),
                window_end_date=date(2026, 10, 5),
                provider="youtube_analytics",
            ),
        ]
    )
    await session.commit()
    return project, video


async def test_derives_objective_features_and_preserves_manual_provenance(session) -> None:
    project, _ = await _content_fixture(session)
    profile = await content_intelligence.refresh_profile(session, project.id)
    assert profile.duration_seconds == Decimal("40")
    assert profile.narration_duration_seconds == Decimal("2")
    assert profile.scene_count == 2
    assert profile.script_word_count == 4
    assert profile.narration_word_count == 4
    assert profile.narration_words_per_minute == Decimal("120")
    assert profile.voice_provider == "gemini"
    assert profile.voice_model == "gemini-tts"
    assert profile.voice_name == "Algieba"
    assert profile.tts_speed == Decimal("1.0")
    assert profile.caption_event_count == 9
    assert profile.template_families == ["code_reveal", "diagram_flow"]
    assert profile.cta_present is None
    assert profile.profile_version == 1
    assert profile.field_provenance["duration_seconds"] == FeatureProvenance.SYSTEM_DERIVED

    updated = await content_intelligence.update_editorial_profile(
        session,
        project.id,
        {
            "topic_category": "Cybersecurity",
            "hook_type": "curiosity_gap",
            "technical_depth": "introductory",
        },
    )
    assert updated.topic_category == "Cybersecurity"
    assert updated.field_provenance["hook_type"] == FeatureProvenance.MANUAL


async def test_tts_speed_ignores_newer_assets_from_a_historical_script(session) -> None:
    project, _ = await _content_fixture(session)
    historical_script = Script(
        project_id=project.id,
        version=2,
        selected_title="Unused retry",
        selected_hook="Unused retry",
        narration="Historical narration",
        word_count=2,
    )
    session.add(historical_script)
    await session.flush()
    historical_scene = Scene(
        script_id=historical_script.id,
        project_id=project.id,
        scene_number=1,
        narration="Historical narration",
        template_id="code_reveal",
    )
    session.add(historical_scene)
    await session.flush()
    session.add(
        ProductionAsset(
            project_id=project.id,
            scene_id=historical_scene.id,
            asset_type=AssetType.NARRATION_AUDIO,
            file_path="/tmp/historical-scene.wav",
            provider="resilient(gemini)",
            asset_metadata={"provider": "gemini", "voice": "Algieba", "speed": 0.5},
        )
    )
    await session.commit()

    profile = await content_intelligence.refresh_profile(session, project.id)

    assert profile.tts_speed == Decimal("1.0")


async def test_learning_record_and_api_keep_pending_windows_explicit(client, session) -> None:
    project, video = await _content_fixture(session)
    await content_intelligence.update_editorial_profile(
        session,
        project.id,
        {"topic_category": "Cybersecurity", "hook_type": "curiosity_gap"},
    )
    await session.commit()

    response = await client.get(f"/api/v1/content-intelligence/videos/{video.id}/record")
    assert response.status_code == 200
    record = response.json()
    assert record["features"]["system_derived"]["duration_seconds"] == 40.0
    assert isinstance(record["features"]["system_derived"]["duration_seconds"], float)
    assert record["features"]["system_derived"]["voice_name"] == "Algieba"
    assert record["features"]["editorial"]["topic_category"] == "Cybersecurity"
    assert record["performance"]["D1"]["views"] == 12
    assert record["performance"]["D28"]["status"] == "PENDING"
    assert record["performance"]["D90"] is None

    listing = (await client.get("/api/v1/content-intelligence/records")).json()
    assert listing["total"] == 1
    comparison = (
        await client.get(
            "/api/v1/content-intelligence/comparisons",
            params={"group_by": "hook_type", "window": "D1"},
        )
    ).json()
    assert comparison["observational_only"] is True
    assert comparison["items"][0]["videos_in_group"] == 1
    assert comparison["items"][0]["comparative_evidence_insufficient"] is True


async def test_editorial_api_validates_controlled_values(client, session) -> None:
    project, _ = await _content_fixture(session)
    protected = await client.patch(
        f"/api/v1/content-intelligence/projects/{project.id}/profile",
        json={"duration_seconds": 1},
    )
    assert protected.status_code == 422
    bad = await client.patch(
        f"/api/v1/content-intelligence/projects/{project.id}/profile",
        json={"hook_type": "machine_guessed"},
    )
    assert bad.status_code == 422
    good = await client.patch(
        f"/api/v1/content-intelligence/projects/{project.id}/profile",
        json={
            "topic_category": "Cybersecurity",
            "hook_type": "curiosity_gap",
            "audience_level": "beginner_general",
            "technical_depth": "introductory",
            "title_style": "curiosity",
        },
    )
    assert good.status_code == 200
    assert good.json()["field_provenance"]["topic_category"] == "MANUAL"


async def test_learning_refresh_uses_existing_idempotent_background_job(client, session) -> None:
    _, video = await _content_fixture(session)
    channel = (await session.execute(select(Channel))).scalars().one()
    channel.analytics_enabled = True
    await session.commit()

    path = f"/api/v1/content-intelligence/videos/{video.id}/refresh"
    first = await client.post(path)
    second = await client.post(path)
    assert first.status_code == second.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["job_id"] == second.json()["job_id"]
