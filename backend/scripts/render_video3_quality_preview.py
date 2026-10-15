"""Render a silent low-FPS Video #3 animatic without invoking TTS."""

from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.models.content import ContentProject, Scene, Script
from app.providers.base import WordTiming
from app.providers.renderer.generic_scene import build_generic_runtime_props
from app.providers.renderer.playwright_frames import PlaywrightFrameRenderer
from app.providers.renderer.scene_contract import build_scene_spec

PROJECT_ID = uuid.UUID("01a133b5-5a42-789f-a301-f26c1e869149")
FPS = 6
WPM = 185.0
OUTPUT = Path("/media/proofs/video3_quality_preview")
WORDS = re.compile(r"\b[\w'-]+\b")


def estimated_words(text: str, start: float) -> tuple[list[WordTiming], float]:
    tokens = WORDS.findall(text)
    duration = len(tokens) / WPM * 60
    step = duration / max(1, len(tokens))
    return [
        WordTiming(word=word, start=start + index * step, end=start + (index + 1) * step)
        for index, word in enumerate(tokens)
    ], duration


async def encode_preview(frame_dir: Path, output: Path) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-framerate",
        str(FPS),
        "-i",
        str(frame_dir / "f_%05d.png"),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"Preview encoding failed: {stderr.decode(errors='replace')}")


async def main() -> None:
    async with session_scope() as session:
        project = await session.get(ContentProject, PROJECT_ID)
        script = (
            await session.execute(
                select(Script).where(Script.project_id == PROJECT_ID, Script.is_current.is_(True))
            )
        ).scalar_one()
        scenes = list(
            (
                await session.execute(
                    select(Scene).where(Scene.script_id == script.id).order_by(Scene.scene_number)
                )
            ).scalars()
        )
        if project.status != "SCRIPT_REVIEW":
            raise RuntimeError(
                f"Preview requires an unapproved SCRIPT_REVIEW candidate, got {project.status}"
            )

        OUTPUT.mkdir(parents=True, exist_ok=True)
        render_root = OUTPUT / "rendered"
        flat = OUTPUT / "preview_frames"
        shutil.rmtree(render_root, ignore_errors=True)
        shutil.rmtree(flat, ignore_errors=True)
        flat.mkdir(parents=True)
        frame_number = 0
        cursor = 0.0
        summary: list[str] = []
        async with PlaywrightFrameRenderer(fps=FPS) as renderer:
            for scene in scenes:
                words, speech_duration = estimated_words(scene.narration, cursor)
                hold = 1.6 if scene.template_props.get("end_card") else 0.18
                visual_duration = speech_duration + hold
                runtime = build_generic_runtime_props(
                    scene,
                    words=words,
                    scene_start=cursor,
                    speech_duration=speech_duration,
                    visual_duration=visual_duration,
                )
                spec = build_scene_spec(
                    project,
                    script,
                    scene,
                    width=settings.VIDEO_WIDTH,
                    height=settings.VIDEO_HEIGHT,
                    runtime_props=runtime,
                )
                result = await renderer.render(spec, render_root, duration=visual_duration)
                representative_index = (
                    min(
                        len(result.frames) - 1,
                        max(0, round(speech_duration * FPS) - 1),
                    )
                    if scene.template_props.get("end_card")
                    else len(result.frames) - 1
                )
                representative = result.frames[representative_index]
                shutil.copy2(
                    representative, OUTPUT / f"representative_scene_{scene.scene_number}.png"
                )
                detail_names = {
                    2: "scene_2_full_cache_poisoning_flow.png",
                    3: "scene_3_complete_signature_verification.png",
                    4: "scene_4_failed_verification_flow.png",
                    5: "scene_5_comparison.png",
                }
                if scene.scene_number in detail_names:
                    shutil.copy2(representative, OUTPUT / detail_names[scene.scene_number])
                for source in result.frames:
                    shutil.copy2(source, flat / f"f_{frame_number:05d}.png")
                    frame_number += 1
                cursor += speech_duration + 0.18
                summary.append(
                    f"scene={scene.scene_number} speech={speech_duration:.3f} "
                    f"visual={visual_duration:.3f} frames={len(result.frames)}"
                )
        final_frame = flat / f"f_{frame_number - 1:05d}.png"
        shutil.copy2(final_frame, OUTPUT / "end_card.png")
        video = OUTPUT / "video3_final_quality_preview_silent.mp4"
        await encode_preview(flat, video)
        print(
            f"script={script.id} version={script.version} fps={FPS} "
            f"frames={frame_number} output={video}"
        )
        print("\n".join(summary))


if __name__ == "__main__":
    asyncio.run(main())
