"""Production pipeline: script -> narration -> timing -> scenes -> video.

The ordering here is the important part, and it is not arbitrary:

    1. Synthesize narration per *spoken segment* (clause/sentence).
    2. MEASURE the real audio durations.
    3. Derive scene timings from those measurements.
    4. Align each scene against its own audio for caption timing.
    4b. Reassemble with intent-shaped pauses (app.services.prosody).
    5. Render scene visuals.
    6. Composite.

Steps 2-3 are the ones people skip. Estimating scene timing from word counts
produces drift that compounds across a video and is miserable to debug, so
timings here are always measured, never guessed (ARCH §14.1).

Every stage persists its output as a `ProductionAsset` with declared origin and
licence, so "where did this video come from?" is always answerable.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.clock import utcnow
from app.core.errors import TerminalError
from app.core.logging import get_logger
from app.models.content import (
    ContentProject,
    ProductionAsset,
    Scene,
    Script,
    VideoRender,
)
from app.models.enums import AssetOrigin, AssetType, RenderStatus
from app.providers.alignment.faster_whisper import FasterWhisperAligner
from app.providers.base import (
    CompositionScene,
    CompositionSpec,
    SceneRenderSpec,
    VoiceSpec,
    WordTiming,
)
from app.providers.compositor import captions as caption_builder
from app.providers.compositor.ffmpeg import FFmpegCompositor
from app.providers.renderer.playwright_frames import PlaywrightFrameRenderer
from app.providers.tts.kokoro import KokoroTTS
from app.services.prosody import segment_narration

log = get_logger("production")

# Small pause between scenes so narration does not run together. Kept short —
# dead air is the fastest way to lose a Shorts viewer.
INTER_SCENE_GAP = 0.12


@dataclass(slots=True)
class SegmentAudio:
    """One spoken unit and the silence that follows it."""

    path: Path
    duration: float
    pause_after: float


@dataclass(slots=True)
class SceneAudio:
    scene: Scene
    path: Path
    duration: float
    start: float
    end: float
    segments: list[SegmentAudio]
    # The inter-scene pause. The picture must cover it — the audio timeline
    # includes these gaps, so excluding them from the visuals leaves the video
    # stream shorter than the audio and the last fraction of a second with no
    # frame to show.
    gap_after: float = 0.0
    # Extra time the visuals stay on screen after the narration stops. Used for
    # the closing hold so the video resolves rather than simply stopping.
    hold_after: float = 0.0

    @property
    def visual_duration(self) -> float:
        """How long this scene is on screen: speech + its pause + any hold."""
        return self.duration + self.gap_after + self.hold_after


def project_dir(project_id: uuid.UUID) -> Path:
    return settings.MEDIA_ROOT / "renders" / str(project_id)


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:32]


async def record_asset(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    asset_type: AssetType,
    path: Path,
    scene_id: uuid.UUID | None = None,
    origin: AssetOrigin = AssetOrigin.GENERATED,
    license: str = "internal-generated",
    provider: str | None = None,
    duration_seconds: float | None = None,
    attribution_text: str | None = None,
    source_url: str | None = None,
    metadata: dict | None = None,
) -> ProductionAsset:
    """Persist an asset with its provenance.

    Origin and licence are required arguments with safe defaults rather than
    optional extras — the licensing quality gate is only meaningful if it is
    impossible to record an asset without saying where it came from.
    """
    asset = ProductionAsset(
        project_id=project_id,
        scene_id=scene_id,
        asset_type=asset_type.value,
        origin=origin.value,
        license=license,
        attribution_text=attribution_text,
        source_url=source_url,
        file_path=str(path),
        bytes=path.stat().st_size if path.exists() else None,
        checksum=_checksum(path) if path.exists() else None,
        duration_seconds=Decimal(str(round(duration_seconds, 3))) if duration_seconds else None,
        provider=provider,
        asset_metadata=metadata,
    )
    session.add(asset)
    await session.flush()
    return asset


# --------------------------------------------------------------- narration ---
async def _concat_segments(parts: list[SegmentAudio], out_path: Path, tail: float = 0.0) -> None:
    """Join spoken segments with their intended pauses."""
    from app.providers.compositor.ffmpeg import _run

    inputs: list[str] = []
    filters: list[str] = []
    for index, part in enumerate(parts):
        inputs += ["-i", str(part.path)]
        pad = part.pause_after + (tail if index == len(parts) - 1 else 0.0)
        if pad > 0:
            filters.append(f"[{index}:a]apad=pad_dur={pad:.3f}[a{index}]")
        else:
            filters.append(f"[{index}:a]anull[a{index}]")

    joined = "".join(f"[a{i}]" for i in range(len(parts)))
    graph = ";".join(filters) + f";{joined}concat=n={len(parts)}:v=0:a=1[out]"

    code, _, err = await _run(
        ["ffmpeg", "-y", "-hide_banner", "-nostats", *inputs,
         "-filter_complex", graph, "-map", "[out]",
         "-ar", "24000", "-ac", "1", str(out_path)]
    )
    if code != 0:
        raise TerminalError(f"Segment concat failed: {err.strip()[-500:]}")


async def synthesize_narration(
    session: AsyncSession, project: ContentProject, script: Script, voice: VoiceSpec | None = None
) -> list[SceneAudio]:
    """Synthesize narration segment by segment and measure the result.

    Segment-level synthesis is what removes the uniform cadence of feeding a
    whole paragraph to the model: each clause gets its own natural contour, and
    the pause after it is chosen by intent rather than a fixed constant
    (app.services.prosody).
    """
    voice = voice or VoiceSpec(
        voice=settings.TTS_VOICE, speed=settings.TTS_SPEED, lang=settings.TTS_LANG
    )
    scenes = sorted(script.scenes, key=lambda s: s.scene_number)
    if not scenes:
        raise TerminalError("Script has no scenes")

    out_dir = project_dir(project.id) / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)
    tts = KokoroTTS()

    results: list[SceneAudio] = []
    cursor = 0.0
    for scene in scenes:
        props = scene.template_props or {}
        reveals = frozenset(props.get("reveal_segments") or [])
        segments = segment_narration(scene.narration, reveal_indexes=reveals)
        if not segments:
            raise TerminalError(f"Scene {scene.scene_number} has no speakable narration")

        parts: list[SegmentAudio] = []
        for index, segment in enumerate(segments):
            seg_path = out_dir / f"scene_{scene.scene_number:02d}_s{index:02d}.wav"
            result = await tts.synthesize(segment.text, voice, seg_path)
            parts.append(
                SegmentAudio(
                    path=seg_path,
                    duration=result.duration_seconds,
                    pause_after=segment.pause_seconds,
                )
            )

        scene_path = out_dir / f"scene_{scene.scene_number:02d}.wav"
        await _concat_segments(parts, scene_path)
        duration = sum(p.duration + p.pause_after for p in parts)

        is_last = scene is scenes[-1]
        hold = settings.END_HOLD_SECONDS if is_last else 0.0
        gap = 0.0 if is_last else INTER_SCENE_GAP

        start, end = cursor, cursor + duration
        cursor = end + gap

        # Timings come from measured audio, and are written back onto the scene.
        scene.start_seconds = Decimal(str(round(start, 3)))
        scene.end_seconds = Decimal(str(round(end, 3)))

        await record_asset(
            session,
            project_id=project.id,
            scene_id=scene.id,
            asset_type=AssetType.NARRATION_AUDIO,
            path=scene_path,
            provider=tts.name,
            duration_seconds=duration,
            metadata={
                "voice": result.voice,
                "speed": voice.speed,
                "scene": scene.scene_number,
                "segments": len(parts),
            },
        )
        results.append(
            SceneAudio(
                scene=scene, path=scene_path, duration=duration,
                start=start, end=end, segments=parts,
                gap_after=gap, hold_after=hold,
            )
        )

    await session.flush()
    log.info(
        "production.narration_done",
        scenes=len(results),
        segments=sum(len(r.segments) for r in results),
        narration_seconds=round(results[-1].end, 2),
        total_seconds=round(results[-1].end + results[-1].hold_after, 2),
        voice=voice.voice,
        speed=voice.speed,
    )
    return results


async def concat_narration(
    session: AsyncSession, project: ContentProject, parts: list[SceneAudio]
) -> Path:
    """Build the full narration track from every segment across every scene.

    Trailing silence matches the closing visual hold, so the audio does not run
    out before the picture resolves.
    """
    out_dir = project_dir(project.id) / "audio"
    combined = out_dir / "narration.wav"

    flat: list[SegmentAudio] = []
    for index, scene_audio in enumerate(parts):
        segs = list(scene_audio.segments)
        if index < len(parts) - 1:
            # The scene boundary pause lives on that scene's last segment.
            segs[-1] = SegmentAudio(
                path=segs[-1].path,
                duration=segs[-1].duration,
                pause_after=segs[-1].pause_after + INTER_SCENE_GAP,
            )
        flat.extend(segs)

    tail = parts[-1].hold_after
    await _concat_segments(flat, combined, tail=tail)

    total = parts[-1].end + tail
    await record_asset(
        session,
        project_id=project.id,
        asset_type=AssetType.NARRATION_AUDIO,
        path=combined,
        provider="ffmpeg-concat",
        duration_seconds=total,
        metadata={"role": "full_narration", "segments": len(flat), "tail_hold": tail},
    )
    return combined


# --------------------------------------------------------------- alignment ---
async def align_narration(
    session: AsyncSession, project: ContentProject, parts: list[SceneAudio]
) -> list[WordTiming]:
    """Align each scene against its own audio, then offset into the timeline.

    Deliberately per-scene rather than over the concatenated track. Aligning the
    whole narration at once made token counts drift (recognition merges or
    splits words differently across a 38-second read), which lost the script's
    punctuation and produced captions that ran across clause boundaries.

    Per-scene alignment keeps each comparison short enough to match exactly, and
    it makes scene boundaries hard caption breaks for free.
    """
    aligner = FasterWhisperAligner()
    words: list[WordTiming] = []

    for part in parts:
        result = await aligner.align(part.path, part.scene.narration)
        for w in result.words:
            words.append(
                WordTiming(
                    word=w.word,
                    start=part.start + w.start,
                    end=min(part.start + w.end, part.end),
                )
            )

    if not words:
        raise TerminalError("Alignment produced no word timings; captions would be empty")
    log.info("production.alignment_done", words=len(words), scenes=len(parts))
    return words


async def build_captions(
    session: AsyncSession, project: ContentProject, words: list[WordTiming]
) -> Path:
    ass_path = project_dir(project.id) / "captions.ass"
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(
        caption_builder.build_ass(
            words, width=settings.VIDEO_WIDTH, height=settings.VIDEO_HEIGHT
        ),
        encoding="utf-8",
    )
    await record_asset(
        session,
        project_id=project.id,
        asset_type=AssetType.CAPTION_FILE,
        path=ass_path,
        provider="ass-builder",
        metadata={"words": len(words)},
    )
    return ass_path


# ----------------------------------------------------------------- visuals ---
async def render_scenes(
    session: AsyncSession, project: ContentProject, parts: list[SceneAudio]
) -> dict[int, list[Path]]:
    """Render every scene as a real frame sequence at the output frame rate.

    Durations come from the measured narration, so the animation in each scene
    is paced to what is actually being said rather than to a guess.
    """
    out_dir = project_dir(project.id) / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered: dict[int, list[Path]] = {}

    async with PlaywrightFrameRenderer(fps=settings.VIDEO_FPS) as renderer:
        for part in parts:
            scene = part.scene
            spec = SceneRenderSpec(
                scene_number=scene.scene_number,
                template_id=scene.template_id,
                props=dict(scene.template_props or {}),
                width=settings.VIDEO_WIDTH,
                height=settings.VIDEO_HEIGHT,
            )
            result = await renderer.render(spec, out_dir, duration=part.visual_duration)
            rendered[scene.scene_number] = result.frames

            # One asset row per scene rather than per frame: a 1000-row insert
            # per render would bury the genuinely interesting assets.
            await record_asset(
                session,
                project_id=project.id,
                scene_id=scene.id,
                asset_type=AssetType.SCENE_FRAME,
                path=result.frames[0],
                provider=renderer.name,
                duration_seconds=part.visual_duration,
                metadata={
                    "template": scene.template_id,
                    "scene": scene.scene_number,
                    "frame_count": len(result.frames),
                    "fps": settings.VIDEO_FPS,
                    "directory": str(result.frames[0].parent),
                },
            )
    await session.flush()
    return rendered


def build_sfx_cues(project: ContentProject, parts: list[SceneAudio]) -> list[dict]:
    """Place sound effects from each scene's declared cue.

    Declarative rather than hard-coded: a scene asks for a cue at a fraction of
    its own duration, so re-timing the narration moves the effect with it.
    """
    # Imported here, not at module scope: sfx needs numpy/soundfile, which only
    # the worker image carries. The API image imports this module too and must
    # not require the media stack (ARCH §15.2).
    from app.services import sfx

    library = sfx.build_library(project_dir(project.id) / "audio")
    cues: list[dict] = []
    for part in parts:
        spec = (part.scene.template_props or {}).get("sfx")
        if not spec:
            continue
        name = spec.get("cue")
        path = library.get(name)
        if path is None:
            continue
        at = part.start + part.visual_duration * float(spec.get("at", 0.5))
        cues.append({"path": path, "at_seconds": at, "gain": float(spec.get("gain", 0.45))})
    return cues


# ------------------------------------------------------------------ render ---
async def compose_video(
    session: AsyncSession,
    project: ContentProject,
    script: Script,
    parts: list[SceneAudio],
    frames: dict[int, list[Path]],
    narration: Path,
    subtitles: Path,
) -> VideoRender:
    render = VideoRender(
        project_id=project.id,
        script_id=script.id,
        status=RenderStatus.RUNNING.value,
        renderer="ffmpeg",
        started_at=utcnow(),
    )
    session.add(render)
    await session.flush()

    output = project_dir(project.id) / f"systemdecoded_{project.id.hex[:8]}.mp4"
    comp_scenes = [
        CompositionScene(
            scene_number=p.scene.scene_number,
            start=p.start,
            # The closing scene's picture outlasts its narration by the hold.
            end=p.start + p.visual_duration,
            frames=frames.get(p.scene.scene_number, []),
            transition_in=p.scene.transition_in,
        )
        for p in parts
    ]
    total_duration = parts[-1].start + parts[-1].visual_duration
    cues = build_sfx_cues(project, parts)

    spec = CompositionSpec(
        scenes=comp_scenes,
        narration_audio=narration,
        output_path=output,
        subtitle_path=subtitles,
        width=settings.VIDEO_WIDTH,
        height=settings.VIDEO_HEIGHT,
        fps=settings.VIDEO_FPS,
        target_lufs=settings.VIDEO_TARGET_LUFS,
        extra={
            "fonts_dir": str(settings.SCENE_TEMPLATES_DIR / "_base" / "fonts"),
            "sfx_cues": cues,
            "total_duration": total_duration,
        },
    )

    try:
        result = await FFmpegCompositor().compose(spec)
    except Exception as exc:
        render.status = RenderStatus.FAILED.value
        render.error_message = str(exc)[:4000]
        render.finished_at = utcnow()
        await session.flush()
        raise

    render.status = RenderStatus.SUCCEEDED.value
    render.output_path = str(result.path)
    render.width = result.width
    render.height = result.height
    render.fps = result.fps
    render.duration_seconds = Decimal(str(round(result.duration_seconds, 3)))
    render.bytes = result.bytes
    render.checksum = _checksum(result.path)
    render.loudness_lufs = (
        Decimal(str(round(result.loudness_lufs, 2))) if result.loudness_lufs is not None else None
    )
    render.peak_dbfs = (
        Decimal(str(round(result.peak_dbfs, 2))) if result.peak_dbfs is not None else None
    )
    render.spec = {
        "scenes": len(comp_scenes),
        "fps": spec.fps,
        "target_lufs": spec.target_lufs,
        "captions": subtitles.name,
        "renderer": "playwright-frames",
        "voice": settings.TTS_VOICE,
        "voice_speed": settings.TTS_SPEED,
        "sfx_cues": len(cues),
        "end_hold_seconds": parts[-1].hold_after,
        "crf": 16,
    }
    render.finished_at = utcnow()
    await session.flush()

    await record_asset(
        session,
        project_id=project.id,
        asset_type=AssetType.VIDEO,
        path=result.path,
        provider="ffmpeg",
        duration_seconds=result.duration_seconds,
        metadata={"render_id": str(render.id)},
    )
    for cue in cues:
        await record_asset(
            session,
            project_id=project.id,
            asset_type=AssetType.SFX,
            path=Path(cue["path"]),
            provider="internal-synthesis",
            metadata={"at_seconds": round(cue["at_seconds"], 3), "gain": cue["gain"]},
        )

    project.current_render_id = render.id
    await session.flush()
    return render


# --------------------------------------------------------------- full run ---
async def produce(session: AsyncSession, project: ContentProject) -> VideoRender:
    """Run the whole pipeline for a project's current script."""
    script = (
        await session.execute(
            select(Script).where(Script.project_id == project.id, Script.is_current.is_(True))
        )
    ).scalar_one_or_none()
    if script is None:
        raise TerminalError("Project has no current script")

    # A clean slate per run: stale frames from a previous attempt silently
    # ending up in a new video is a genuinely confusing failure.
    work = project_dir(project.id)
    if work.exists():
        shutil.rmtree(work / "_work", ignore_errors=True)

    parts = await synthesize_narration(session, project, script)
    narration = await concat_narration(session, project, parts)
    words = await align_narration(session, project, parts)
    subtitles = await build_captions(session, project, words)
    frames = await render_scenes(session, project, parts)
    return await compose_video(session, project, script, parts, frames, narration, subtitles)
