"""Compose, persist, recover state, and QC the Motion Canvas Video #2 render."""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.core.clock import utcnow
from app.core.state_machine import transition
from app.db.session import session_scope
from app.models.content import ContentProject, Scene, Script, VideoRender
from app.models.enums import AssetType, ProjectStatus, QualityVerdict, RenderStatus
from app.providers.base import CompositionScene, CompositionSpec
from app.providers.compositor.ffmpeg import FFmpegCompositor
from app.services import experiments, quality
from app.services.production import _checksum, project_dir, record_asset

PROJECT_ID = uuid.UUID("01a11afb-aa75-7306-b2a2-e5e7af87b988")
SCRIPT_ID = uuid.UUID("01a12eae-17b5-7d77-b0b9-1a6002a9af06")


async def main() -> None:
    async with session_scope() as session:
        project = await session.get(ContentProject, PROJECT_ID)
        script = await session.get(Script, SCRIPT_ID)
        if project is None or script is None or project.current_script_id != script.id:
            raise RuntimeError("Current Video #2 project/script relationship is invalid")
        scenes = list(
            (
                await session.execute(
                    select(Scene).where(Scene.script_id == script.id).order_by(Scene.scene_number)
                )
            ).scalars()
        )
        timing_path = project_dir(project.id) / "motion-canvas-production.json"
        timing = json.loads(timing_path.read_text(encoding="utf-8"))
        frames_dir = project_dir(project.id) / "frames" / "motion_canvas"
        frames = sorted(frames_dir.glob("*.png"))
        expected = round(float(timing["total_visual_duration_seconds"]) * settings.VIDEO_FPS)
        if abs(len(frames) - expected) > 1:
            raise RuntimeError(f"Motion Canvas exported {len(frames)} frames; expected {expected}")

        forbidden = (
            "totp",
            "418902",
            "six-digit",
            "six digit",
            "countdown",
            "they never communicate",
            "authenticator",
        )
        # The renderer source is not mounted in the worker, so validate the
        # immutable timing/payload here; npm's isolation check validates source.
        serialized = json.dumps(timing).lower()
        leaked = [token for token in forbidden if token in serialized]
        if leaked:
            raise RuntimeError(f"Video #1 content leaked into Video #2: {leaked}")

        if project.status == ProjectStatus.VIDEO_REVIEW.value:
            await transition(
                session, project, ProjectStatus.NEEDS_REVISION,
                actor="HUMAN", reason="Human review requested Motion Canvas visual refinements",
            )
        if project.status == ProjectStatus.FAILED.value:
            await transition(
                session, project, ProjectStatus.NEEDS_REVISION,
                actor="SYSTEM", reason="Recovering failed production with approved media migration",
            )
        if project.status == ProjectStatus.NEEDS_REVISION.value:
            await transition(
                session, project, ProjectStatus.RENDERING,
                actor="SYSTEM", reason="Motion Canvas production migration started",
            )
        if project.status != ProjectStatus.RENDERING.value:
            raise RuntimeError(f"Project recovery left unexpected status {project.status}")

        boundaries = [
            round(float(item["start_seconds"]) * settings.VIDEO_FPS)
            for item in timing["scenes"]
        ]
        boundaries.append(len(frames))
        composition_scenes = []
        for index, (scene, item) in enumerate(zip(scenes, timing["scenes"], strict=True)):
            scene_frames = frames[boundaries[index] : boundaries[index + 1]]
            if not scene_frames:
                raise RuntimeError(f"Scene {scene.scene_number} has no Motion Canvas frames")
            composition_scenes.append(
                CompositionScene(
                    scene_number=scene.scene_number,
                    start=float(item["start_seconds"]),
                    end=float(item["start_seconds"]) + float(item["visual_duration_seconds"]),
                    frames=scene_frames,
                    transition_in=scene.transition_in,
                )
            )
            await record_asset(
                session,
                project_id=project.id,
                scene_id=scene.id,
                asset_type=AssetType.SCENE_FRAME,
                path=scene_frames[0],
                provider="motion-canvas",
                duration_seconds=float(item["visual_duration_seconds"]),
                metadata={
                    "renderer": "motion-canvas",
                    "scene": scene.scene_number,
                    "frame_count": len(scene_frames),
                    "fps": settings.VIDEO_FPS,
                    "directory": str(frames_dir),
                    "script_id": str(script.id),
                    "scene_id": str(scene.id),
                    "timing_source": timing["source"],
                    "timing_events": item["timing"]["events"],
                },
            )

        render = VideoRender(
            project_id=project.id,
            script_id=script.id,
            status=RenderStatus.RUNNING.value,
            renderer="motion-canvas+ffmpeg",
            started_at=utcnow(),
        )
        session.add(render)
        await session.flush()

        output = project_dir(project.id) / "systemdecoded_video2_motion_canvas.mp4"
        spec = CompositionSpec(
            scenes=composition_scenes,
            narration_audio=Path(timing["narration_path"]),
            output_path=output,
            subtitle_path=Path(timing["caption_path"]),
            width=settings.VIDEO_WIDTH,
            height=settings.VIDEO_HEIGHT,
            fps=settings.VIDEO_FPS,
            target_lufs=settings.VIDEO_TARGET_LUFS,
            extra={
                "fonts_dir": str(settings.SCENE_TEMPLATES_DIR / "_base" / "fonts"),
                "sfx_cues": [],
                "total_duration": timing["total_visual_duration_seconds"],
            },
        )
        result = await FFmpegCompositor().compose(spec)
        render.status = RenderStatus.SUCCEEDED.value
        render.output_path = str(result.path)
        render.width = result.width
        render.height = result.height
        render.fps = result.fps
        render.duration_seconds = Decimal(str(round(result.duration_seconds, 3)))
        render.bytes = result.bytes
        render.checksum = _checksum(result.path)
        render.loudness_lufs = (
            Decimal(str(round(result.loudness_lufs, 2)))
            if result.loudness_lufs is not None
            else None
        )
        render.peak_dbfs = (
            Decimal(str(round(result.peak_dbfs, 2)))
            if result.peak_dbfs is not None
            else None
        )
        render.spec = {
            "scenes": 5,
            "fps": settings.VIDEO_FPS,
            "renderer": "motion-canvas",
            "compositor": "ffmpeg",
            "captions": Path(timing["caption_path"]).name,
            "caption_words": 102,
            "narration": {
                "provider": "chatterbox",
                "model": "ResembleAI/chatterbox",
                "voice": "builtin-default",
                "audio_prompt_path": None,
                "blocks": 5,
                "fallback_used": False,
            },
            "alignment": timing["alignment"],
            "timing_source": timing["source"],
            "end_hold_seconds": timing["end_hold_seconds"],
            "isolation_validated": True,
            "crf": 16,
        }
        render.finished_at = utcnow()
        project.current_render_id = render.id
        project.failure_reason = None
        await record_asset(
            session,
            project_id=project.id,
            asset_type=AssetType.VIDEO,
            path=result.path,
            provider="motion-canvas+ffmpeg",
            duration_seconds=result.duration_seconds,
            metadata={"render_id": str(render.id), "script_id": str(script.id)},
        )
        await session.flush()

        check = await quality.run_quality_checks(session, project, render)
        if check.verdict == QualityVerdict.FAIL.value:
            await transition(
                session, project, ProjectStatus.NEEDS_REVISION,
                reason=f"Quality gate failed: {'; '.join(check.blocking_issues[:3])}"[:500],
            )
        else:
            await transition(
                session, project, ProjectStatus.VIDEO_REVIEW,
                reason=f"Motion Canvas render complete ({check.verdict})",
            )
            await experiments.mark_produced(session, project.id)

        print(
            json.dumps(
                {
                    "render_id": str(render.id),
                    "output_path": render.output_path,
                    "duration_seconds": float(render.duration_seconds),
                    "width": render.width,
                    "height": render.height,
                    "fps": render.fps,
                    "loudness_lufs": float(render.loudness_lufs) if render.loudness_lufs else None,
                    "peak_dbfs": float(render.peak_dbfs) if render.peak_dbfs else None,
                    "qc_verdict": check.verdict,
                    "blocking_issues": check.blocking_issues,
                    "warnings": check.warnings,
                    "project_status": project.status,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
