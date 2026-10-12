"""Prepare Chatterbox audio, canonical captions, and Motion Canvas timings."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from decimal import Decimal

from sqlalchemy import select

from app.config import settings
from app.db.session import session_scope
from app.models.content import ContentProject, Scene, Script
from app.models.enums import AssetType
from app.providers.alignment.faster_whisper import FasterWhisperAligner
from app.providers.base import WordTiming
from app.providers.renderer.motion_timing import build_motion_timing_payload
from app.services.canonical_alignment import align_to_canonical
from app.services.production import (
    INTER_SCENE_GAP,
    AlignedNarration,
    SceneAudio,
    SegmentAudio,
    _prepare_block,
    build_captions,
    concat_narration,
    project_dir,
    record_asset,
)

PROJECT_ID = uuid.UUID("01a11afb-aa75-7306-b2a2-e5e7af87b988")
SCRIPT_ID = uuid.UUID("01a12eae-17b5-7d77-b0b9-1a6002a9af06")
SOURCE = settings.MEDIA_ROOT / "renders" / str(PROJECT_ID) / str(SCRIPT_ID) / "audio"
CUES = {
    1: {
        "password": "your password",
        "readable_text": "readable text",
        "verifier": "a verifier",
    },
    2: {
        "password": "your password",
        "unique_salt": "unique random salt",
        "slow_hash": "slow password-hashing algorithm",
    },
    3: {
        "same_password": "same password",
        "different_salts": "different salts",
        "different_results": "different stored results",
        "login": "you log in",
        "compare": "compares",
        "stored_verifier": "stored verifier",
    },
    4: {
        "breach": "steal the database",
        "not_reversible": "reverse",
        "offline_guessing": "guess passwords offline",
        "argon2id": "Argon2id",
        "guess_cost": "deliberately expensive",
    },
    5: {
        "password": "The password itself",
        "database": "the database",
        "stolen": "be stolen",
    },
}


async def main() -> None:
    generation = json.loads((SOURCE / "generation-metadata.json").read_text(encoding="utf-8"))
    async with session_scope() as session:
        project = await session.get(ContentProject, PROJECT_ID)
        script = await session.get(Script, SCRIPT_ID)
        if project is None or script is None or project.current_script_id != script.id:
            raise RuntimeError("Video #2 project/current-script relationship is invalid")
        scenes = list(
            (
                await session.execute(
                    select(Scene).where(Scene.script_id == script.id).order_by(Scene.scene_number)
                )
            ).scalars()
        )
        if len(scenes) != 5 or any(scene.project_id != project.id for scene in scenes):
            raise RuntimeError("Video #2 scene ownership or count is invalid")

        output_audio = project_dir(project.id) / "audio"
        output_audio.mkdir(parents=True, exist_ok=True)
        cursor = 0.0
        parts: list[SceneAudio] = []
        for scene, generated in zip(scenes, generation["scenes"], strict=True):
            destination = output_audio / f"scene_{scene.scene_number:02d}.wav"
            shutil.copyfile(SOURCE / f"scene-{scene.scene_number:02d}.wav", destination)
            duration = await _prepare_block(destination)
            is_last = scene.scene_number == 5
            gap = 0.0 if is_last else INTER_SCENE_GAP
            hold = settings.END_HOLD_SECONDS if is_last else 0.0
            start = cursor
            end = start + duration
            cursor = end + gap
            scene.start_seconds = Decimal(str(round(start, 3)))
            scene.end_seconds = Decimal(str(round(end, 3)))
            provenance = {
                "provider": "chatterbox",
                "model": generation["model"],
                "voice": "builtin-default",
                "fallback_used": False,
            }
            await record_asset(
                session,
                project_id=project.id,
                scene_id=scene.id,
                asset_type=AssetType.NARRATION_AUDIO,
                path=destination,
                provider="chatterbox",
                duration_seconds=duration,
                metadata={
                    **provenance,
                    "scene": scene.scene_number,
                    "text": scene.narration,
                    "settings": generation["settings"],
                    "audio_prompt_path": None,
                    "model_load_seconds": generation["model_load_seconds"],
                    "generation_seconds": generated["generation_seconds"],
                    "peak_vram_bytes": generated["peak_vram_bytes"],
                    "script_id": str(script.id),
                },
            )
            parts.append(
                SceneAudio(
                    scene=scene,
                    path=destination,
                    duration=duration,
                    start=start,
                    end=end,
                    segments=[SegmentAudio(destination, duration, 0.0)],
                    gap_after=gap,
                    hold_after=hold,
                    provenance=provenance,
                )
            )

        total_visual = parts[-1].end + parts[-1].hold_after
        if not 30.0 <= total_visual <= 35.0:
            raise RuntimeError(
                f"Prepared narration yields {total_visual:.3f}s; outside approved 30-35s target"
            )

        narration = await concat_narration(session, project, parts)
        aligner = FasterWhisperAligner()
        global_words: list[WordTiming] = []
        qualities = []
        motion_scenes = []
        for part in parts:
            heard = await aligner.align(part.path, part.scene.narration)
            aligned = align_to_canonical(
                part.scene.narration,
                heard.words,
                block_start=0.0,
                block_end=part.duration,
                label=f"video2-scene-{part.scene.scene_number}",
            )
            qualities.append(aligned.quality)
            global_words.extend(
                WordTiming(
                    word=word.word,
                    start=part.start + word.start,
                    end=min(part.start + word.end, part.end),
                )
                for word in aligned.words
            )
            local_timing = build_motion_timing_payload(
                aligned.words,
                part.duration,
                CUES[part.scene.scene_number],
            )
            motion_scenes.append(
                {
                    "scene_id": str(part.scene.id),
                    "scene_number": part.scene.scene_number,
                    "start_seconds": round(part.start, 3),
                    "speech_duration_seconds": round(part.duration, 3),
                    "visual_duration_seconds": round(part.visual_duration, 3),
                    "narration": part.scene.narration,
                    "on_screen_text": part.scene.on_screen_text,
                    "visual_instruction": part.scene.visual_instruction,
                    "template_id": part.scene.template_id,
                    "template_props": part.scene.template_props,
                    "timing": local_timing,
                }
            )

        aligned_narration = AlignedNarration(global_words, qualities)
        captions = await build_captions(session, project, aligned_narration)
        payload = {
            "schema_version": 1,
            "project_id": str(project.id),
            "script_id": str(script.id),
            "script_version": script.version,
            "source": "canonical-caption-word-timing",
            "fps": settings.VIDEO_FPS,
            "total_speech_duration_seconds": round(sum(p.duration for p in parts), 3),
            "total_visual_duration_seconds": round(total_visual, 3),
            "inter_scene_gap_seconds": INTER_SCENE_GAP,
            "end_hold_seconds": settings.END_HOLD_SECONDS,
            "alignment": aligned_narration.as_metadata(),
            "narration_path": str(narration),
            "caption_path": str(captions),
            "scenes": motion_scenes,
        }
        timing_path = project_dir(project.id) / "motion-canvas-production.json"
        timing_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        await session.flush()
        print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
