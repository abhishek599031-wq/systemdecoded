"""Production pipeline: script -> narration -> timing -> scenes -> video.

The ordering here is the important part, and it is not arbitrary:

    1. Synthesize narration one *semantic block* at a time.
    2. MEASURE the real audio durations.
    3. Derive scene timings from those measurements.
    4. Align each block against its own audio, mapping timings onto the
       approved script (app.services.canonical_alignment).
    5. Render scene visuals to the measured durations.
    6. Composite.

Steps 2-3 are the ones people skip. Estimating scene timing from word counts
produces drift that compounds across a video and is miserable to debug, so
timings here are always measured, never guessed (ARCH §14.1).

Step 4 has one rule that overrides everything else: **the approved script is
what the captions say.** Speech recognition supplies timing and nothing else.

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
from app.core.errors import RetryableError, TerminalError
from app.core.logging import get_logger
from app.core.narration import SYSTEMDECODED_DIRECTION
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
from app.providers.tts.resolver import ResilientTTS, build_provider, voice_for
from app.services.canonical_alignment import AlignmentQuality, align_to_canonical
from app.services.prosody import segment_narration

log = get_logger("production")

# Small pause between scenes so narration does not run together. Kept short —
# dead air is the fastest way to lose a Shorts viewer.
INTER_SCENE_GAP = 0.12

# Silence quieter than this at a block's edges is trimmed before assembly. A
# generative TTS model decides its own lead-in and tail, and those vary per
# request; leaving them in means the gap between two scenes is "whatever the
# model emitted, plus ours", which is neither controllable nor consistent.
# -45 dB is well below speech but above the noise floor of clean synthesis.
EDGE_SILENCE_THRESHOLD_DB = -45

# Per-block loudness target, applied before the blocks are joined. Without it,
# block-to-block level differences survive into the master, because the final
# loudnorm measures the whole track and cannot fix variation inside it. The
# master pass then takes the assembled track to VIDEO_TARGET_LUFS.
BLOCK_LUFS = -16.0


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
    # What actually produced this block's audio. Carried on the block rather
    # than read back from settings at render time, because settings say what is
    # configured now and this has to say what was used then.
    provenance: dict | None = None

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


async def _measure(path: Path) -> float:
    """Duration of an audio file, read from the file itself."""
    from app.providers.compositor.ffmpeg import _run

    code, out, err = await _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)]
    )
    if code != 0:
        raise TerminalError(f"Could not measure {path.name}: {err.strip()[-300:]}")
    return float(out.strip())


async def _prepare_block(path: Path) -> float:
    """Trim edge silence and set a common level, in place.

    Both halves matter for a multi-block read. Trimming makes the pause between
    two scenes exactly the pause the pipeline chose, instead of that plus
    whatever lead-in the model produced that time. Levelling makes the blocks
    sound like one take — the master loudness pass measures the assembled track
    and so cannot correct differences *within* it.
    """
    from app.providers.compositor.ffmpeg import _run

    trimmed = path.with_name(f"{path.stem}_prepared.wav")
    silence = (
        f"silenceremove=start_periods=1:start_duration=0:"
        f"start_threshold={EDGE_SILENCE_THRESHOLD_DB}dB:detection=peak"
    )
    graph = f"{silence},areverse,{silence},areverse,loudnorm=I={BLOCK_LUFS}:TP=-2.0:LRA=11"

    code, _, err = await _run(
        ["ffmpeg", "-y", "-hide_banner", "-nostats", "-i", str(path),
         "-af", graph, "-ar", "24000", "-ac", "1", str(trimmed)]
    )
    if code != 0:
        raise TerminalError(f"Block preparation failed for {path.name}: {err.strip()[-500:]}")

    duration = await _measure(trimmed)
    if duration <= 0.05:
        # Trimming removed everything, which means the threshold ate real
        # speech or the block is silent. Either way, do not ship it.
        raise TerminalError(
            f"{path.name} is empty after silence trimming ({duration:.3f}s) — "
            "narration audio may be silent"
        )
    trimmed.replace(path)
    return duration


async def synthesize_narration(
    session: AsyncSession, project: ContentProject, script: Script, voice: VoiceSpec | None = None
) -> list[SceneAudio]:
    """Synthesize narration one semantic block per scene, and measure it.

    A "block" is one scene's narration: a complete beat of the story. How it is
    submitted depends on what the provider does well, declared by the provider
    itself as `prefers_whole_block`:

    * Gemini is *directed* to vary its pacing and to slow around reveals, so it
      receives the whole block and produces the internal rhythm itself. Five
      scenes means five API calls rather than the fourteen that clause-level
      synthesis needed — which matters against a preview model whose free tier
      allows a couple of requests per minute.
    * Kokoro gives every sentence an identical contour, so the block is split
      into clauses and the pause after each is chosen by intent
      (app.services.prosody).

    Either way the caller gets the same thing: one measured audio file per
    scene.
    """
    scenes = sorted(script.scenes, key=lambda s: s.scene_number)
    if not scenes:
        raise TerminalError("Script has no scenes")

    # Fall back per *video*, never per block.
    #
    # The obvious design — let each block retry and fall back on its own — was
    # what ran first, and it produced a video narrated by Gemini for scene 1 and
    # by Kokoro for scenes 2 to 5, because the daily quota ran out mid-render.
    # Every individual decision was correct and the result was unusable: a
    # narrator that changes voice a quarter of the way in is worse than either
    # voice used throughout.
    #
    # So the primary is given no fallback of its own. If it cannot carry the
    # whole script, the whole script is re-read by the fallback, and the video
    # has one narrator either way. `TerminalError` still propagates untouched:
    # a configuration mistake must not be answered by switching provider.
    primary = ResilientTTS(fallback=None)
    voice = voice or voice_for(settings.TTS_PROVIDER)
    try:
        return await _synthesize_blocks(session, project, scenes, primary, voice)
    except RetryableError as exc:
        if settings.TTS_FALLBACK_PROVIDER == "none":
            raise
        fallback = build_provider(settings.TTS_FALLBACK_PROVIDER)
        log.warning(
            "production.narration_fallback",
            primary=settings.TTS_PROVIDER,
            fallback=settings.TTS_FALLBACK_PROVIDER,
            reason=str(exc)[:300],
            note="whole narration re-read so the video has a single voice",
        )
        return await _synthesize_blocks(
            session, project, scenes, fallback,
            voice_for(settings.TTS_FALLBACK_PROVIDER),
            fallback_from=settings.TTS_PROVIDER,
            fallback_reason=str(exc)[:300],
        )


async def _synthesize_blocks(
    session: AsyncSession,
    project: ContentProject,
    scenes: list[Scene],
    tts,
    voice: VoiceSpec,
    fallback_from: str | None = None,
    fallback_reason: str | None = None,
) -> list[SceneAudio]:
    """Read every block with one provider, and measure what comes back."""
    whole_block = getattr(tts, "prefers_whole_block", False)
    out_dir = project_dir(project.id) / "audio"
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[SceneAudio] = []
    used_fallback = False
    calls = 0
    cursor = 0.0
    for scene in scenes:
        props = scene.template_props or {}
        scene_path = out_dir / f"scene_{scene.scene_number:02d}.wav"

        if whole_block:
            if not scene.narration.strip():
                raise TerminalError(f"Scene {scene.scene_number} has no speakable narration")
            result = await tts.synthesize(scene.narration, voice, scene_path)
            calls += 1
            used_fallback = used_fallback or result.fallback_used
            last_result = result
            duration = await _prepare_block(scene_path)
            parts = [SegmentAudio(path=scene_path, duration=duration, pause_after=0.0)]
        else:
            reveals = frozenset(props.get("reveal_segments") or [])
            segments = segment_narration(scene.narration, reveal_indexes=reveals)
            if not segments:
                raise TerminalError(f"Scene {scene.scene_number} has no speakable narration")

            parts = []
            for index, segment in enumerate(segments):
                seg_path = out_dir / f"scene_{scene.scene_number:02d}_s{index:02d}.wav"
                result = await tts.synthesize(segment.text, voice, seg_path)
                calls += 1
                used_fallback = used_fallback or result.fallback_used
                last_result = result
                parts.append(
                    SegmentAudio(
                        path=seg_path,
                        duration=result.duration_seconds,
                        pause_after=segment.pause_seconds,
                    )
                )
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
                **last_result.as_asset_metadata(),
                "scene": scene.scene_number,
                "segments": len(parts),
                "whole_block": whole_block,
                "speed": voice.speed,
                # Set when the whole narration was re-read by the fallback, so
                # a stored asset always says what actually produced it.
                **(
                    {"fallback_used": True, "fallback_from": fallback_from,
                     "fallback_reason": fallback_reason}
                    if fallback_from
                    else {}
                ),
            },
        )
        results.append(
            SceneAudio(
                scene=scene, path=scene_path, duration=duration,
                start=start, end=end, segments=parts,
                gap_after=gap, hold_after=hold,
                provenance={
                    "provider": last_result.provider,
                    "model": last_result.model,
                    "voice": last_result.voice,
                    "fallback_used": bool(fallback_from) or last_result.fallback_used,
                    "fallback_from": fallback_from,
                },
            )
        )

    await session.flush()
    log.info(
        "production.narration_done",
        scenes=len(results),
        blocks=len(results) if whole_block else None,
        tts_calls=calls,
        segments=sum(len(r.segments) for r in results),
        narration_seconds=round(results[-1].end, 2),
        total_seconds=round(results[-1].end + results[-1].hold_after, 2),
        provider=last_result.provider,
        model=last_result.model,
        voice=last_result.voice,
        fallback_used=used_fallback,
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
@dataclass(slots=True)
class AlignedNarration:
    """Approved words with measured timings, plus how well that went."""

    words: list[WordTiming]
    quality: list[AlignmentQuality]

    @property
    def warnings(self) -> list[str]:
        return [w for q in self.quality for w in q.warnings]

    @property
    def worst_coverage(self) -> float:
        return min((q.coverage for q in self.quality), default=0.0)

    def as_metadata(self) -> dict:
        return {
            "blocks": len(self.quality),
            "words": len(self.words),
            "worst_coverage": round(self.worst_coverage, 4),
            "warnings": self.warnings,
            "per_block": [q.as_dict() for q in self.quality],
        }


async def align_narration(
    session: AsyncSession, project: ContentProject, parts: list[SceneAudio]
) -> AlignedNarration:
    """Time the approved script against the audio, block by block.

    Two decisions here, both load-bearing.

    **Per block, not per track.** Aligning a 38-second read in one pass let
    recognition merge and split words differently along the way, which lost the
    script's clause structure. Per-block keeps each comparison short, isolates a
    bad block instead of corrupting everything after it, and makes scene
    boundaries hard caption breaks for free.

    **The script wins.** Recognition output is never used as caption text. Each
    block's approved narration is aligned against the words that were heard,
    takes timings where they match, and is interpolated where they do not.
    Recognised words with no counterpart in the script — the hallucinations —
    are discarded. Without this, a transcription loop becomes a duplicated
    caption, which is exactly what the Gemini auditions produced.
    """
    aligner = FasterWhisperAligner()
    words: list[WordTiming] = []
    quality: list[AlignmentQuality] = []

    for part in parts:
        heard = await aligner.align(part.path, part.scene.narration)
        aligned = align_to_canonical(
            part.scene.narration,
            heard.words,
            block_start=0.0,
            block_end=part.duration,
            label=f"scene_{part.scene.scene_number}",
        )
        quality.append(aligned.quality)
        for w in aligned.words:
            words.append(
                WordTiming(
                    word=w.word,
                    start=part.start + w.start,
                    end=min(part.start + w.end, part.end),
                )
            )

    if not words:
        raise TerminalError("Alignment produced no word timings; captions would be empty")

    result = AlignedNarration(words=words, quality=quality)
    log.info(
        "production.alignment_done",
        words=len(words),
        blocks=len(parts),
        worst_coverage=round(result.worst_coverage, 3),
        warnings=len(result.warnings),
    )
    return result


async def build_captions(
    session: AsyncSession, project: ContentProject, aligned: AlignedNarration
) -> Path:
    """Write captions from the approved script's own words.

    `aligned.words` is canonical by construction — the alignment stage never
    substitutes recognised text — so nothing here needs to sanitise it. The
    alignment quality is stored alongside so a caption file can be traced back
    to how much of it was measured rather than interpolated.
    """
    ass_path = project_dir(project.id) / "captions.ass"
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(
        caption_builder.build_ass(
            aligned.words, width=settings.VIDEO_WIDTH, height=settings.VIDEO_HEIGHT
        ),
        encoding="utf-8",
    )
    await record_asset(
        session,
        project_id=project.id,
        asset_type=AssetType.CAPTION_FILE,
        path=ass_path,
        provider="ass-builder",
        metadata={"words": len(aligned.words), "alignment": aligned.as_metadata()},
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
    aligned: AlignedNarration | None = None,
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
        # What actually narrated this render, taken from the blocks themselves.
        # Reading it back from settings would have reported "gemini / Algieba"
        # for a video the fallback had narrated — the first run did exactly
        # that, and the render record disagreed with the audio.
        "narration": {
            **(parts[0].provenance or {}),
            "direction": SYSTEMDECODED_DIRECTION.fingerprint,
            "blocks": len(parts),
            "single_voice": len({(p.provenance or {}).get("voice") for p in parts}) == 1,
        },
        "alignment": aligned.as_metadata() if aligned else None,
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
    aligned = await align_narration(session, project, parts)
    subtitles = await build_captions(session, project, aligned)
    frames = await render_scenes(session, project, parts)
    return await compose_video(
        session, project, script, parts, frames, narration, subtitles, aligned
    )
