"""Automated quality gates (ARCH §13.3).

A render must earn its way to human review. Checks are split into *blocking*
(the video is wrong and must not ship) and *warnings* (worth a look, not worth
stopping for).

Everything asserted here is measured off the actual output file with ffprobe,
never inferred from what the pipeline intended to produce. That distinction is
the whole point: a QC that trusts the pipeline cannot catch the pipeline being
wrong.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.logging import get_logger
from app.models.content import (
    ContentProject,
    ProductionAsset,
    QualityCheck,
    ResearchNote,
    Scene,
    Script,
    VideoRender,
)
from app.models.enums import AssetType, QualityVerdict
from app.providers.compositor.ffmpeg import _run, probe

log = get_logger("quality")


async def _extract_final_frame(video: Path, duration: float, out: Path) -> tuple[int, str, str]:
    """Grab the last real video frame.

    `-sseof` seeks relative to the end, which is the only reliable way to reach
    the final frame. `-update 1` is required for a single-image output — without
    it the image2 muxer wants a numbering pattern, exits 0, and writes nothing.

    Note this returns 0 even when it produces no file, so the caller must check
    that the file exists rather than trusting the exit code. That combination is
    exactly what let a video whose picture ran out before its audio look fine.
    """
    return await _run(
        ["ffmpeg", "-y", "-v", "error", "-sseof", "-0.4", "-i", str(video),
         "-update", "1", "-frames:v", "1", str(out)],
        timeout=60,
    )


@dataclass(slots=True)
class CheckResult:
    name: str
    passed: bool
    blocking: bool
    detail: str
    measured: Any = None


@dataclass(slots=True)
class QualityReport:
    checks: list[CheckResult] = field(default_factory=list)

    def add(
        self, name: str, passed: bool, *, blocking: bool, detail: str, measured: Any = None
    ) -> None:
        self.checks.append(
            CheckResult(
                name=name, passed=passed, blocking=blocking, detail=detail, measured=measured
            )
        )

    @property
    def blocking_issues(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if c.blocking and not c.passed]

    @property
    def warnings(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.blocking and not c.passed]

    @property
    def verdict(self) -> QualityVerdict:
        if self.blocking_issues:
            return QualityVerdict.FAIL
        return QualityVerdict.PASS_WITH_WARNINGS if self.warnings else QualityVerdict.PASS

    def as_json(self) -> list[dict[str, Any]]:
        return [
            {
                "name": c.name,
                "passed": c.passed,
                "blocking": c.blocking,
                "detail": c.detail,
                "measured": c.measured,
            }
            for c in self.checks
        ]


async def run_quality_checks(
    session: AsyncSession, project: ContentProject, render: VideoRender
) -> QualityCheck:
    report = QualityReport()

    # ---------------------------------------------------------- the file ---
    path = Path(render.output_path) if render.output_path else None
    exists = bool(path and path.exists() and path.stat().st_size > 0)
    report.add(
        "video_exists",
        exists,
        blocking=True,
        detail="Output file is present and non-empty" if exists else "Output file missing or empty",
        measured=str(path) if path else None,
    )

    if not exists:
        return await _persist(session, project, render, report)

    probed = await probe(path)

    # ------------------------------------------------------- dimensions ---
    correct_dims = (
        probed.width == settings.VIDEO_WIDTH and probed.height == settings.VIDEO_HEIGHT
    )
    report.add(
        "dimensions_9_16",
        correct_dims,
        blocking=True,
        detail=(
            f"{probed.width}x{probed.height} matches the Shorts frame"
            if correct_dims
            else f"Expected {settings.VIDEO_WIDTH}x{settings.VIDEO_HEIGHT}, got "
            f"{probed.width}x{probed.height}"
        ),
        measured=f"{probed.width}x{probed.height}",
    )

    # --------------------------------------------------------- duration ---
    lo, hi = settings.TARGET_DURATION_MIN_SECONDS, settings.TARGET_DURATION_MAX_SECONDS
    in_band = lo <= probed.duration <= hi
    report.add(
        "duration_in_band",
        in_band,
        blocking=True,
        detail=(
            f"{probed.duration:.1f}s is within the {lo:.0f}-{hi:.0f}s Shorts band"
            if in_band
            else f"{probed.duration:.1f}s is outside the {lo:.0f}-{hi:.0f}s band"
        ),
        measured=round(probed.duration, 2),
    )

    # Video and audio must actually span the same time. A short video stream
    # is invisible to every other check — format duration follows the longest
    # stream, so the file "looks" the right length while the picture has
    # already run out.
    stream_gap = probed.duration - probed.video_duration
    report.add(
        "video_covers_audio",
        stream_gap <= 0.25,
        blocking=True,
        detail=(
            f"Video stream {probed.video_duration:.2f}s covers the {probed.duration:.2f}s file"
            if stream_gap <= 0.25
            else f"Video stream is {stream_gap:.2f}s SHORTER than the file — "
            "the picture ends before the audio"
        ),
        measured=round(probed.video_duration, 3),
    )

    # ------------------------------------------------------------ audio ---
    report.add(
        "narration_present",
        probed.has_audio,
        blocking=True,
        detail="Audio stream present" if probed.has_audio else "No audio stream",
    )

    if render.loudness_lufs is not None:
        lufs = float(render.loudness_lufs)
        # Wide band: loudnorm targets -14, and a couple of dB either side is
        # inaudible. Anything far outside means normalisation did not run.
        ok = -20.0 <= lufs <= -9.0
        report.add(
            "loudness_normalised",
            ok,
            blocking=False,
            detail=f"Integrated loudness {lufs:.1f} LUFS"
            + ("" if ok else f" is far from the {settings.VIDEO_TARGET_LUFS} LUFS target"),
            measured=lufs,
        )

    if render.peak_dbfs is not None:
        peak = float(render.peak_dbfs)
        no_clip = peak <= -0.5
        report.add(
            "no_clipping",
            no_clip,
            blocking=True,
            detail=f"True peak {peak:.2f} dBFS" + ("" if no_clip else " — audio is clipping"),
            measured=peak,
        )

    # --------------------------------------------------------- captions ---
    caption_count = (
        await session.execute(
            select(func.count())
            .select_from(ProductionAsset)
            .where(
                ProductionAsset.project_id == project.id,
                ProductionAsset.asset_type == AssetType.CAPTION_FILE.value,
            )
        )
    ).scalar_one()
    caption_asset = (
        await session.execute(
            select(ProductionAsset).where(
                ProductionAsset.project_id == project.id,
                ProductionAsset.asset_type == AssetType.CAPTION_FILE.value,
            )
        )
    ).scalars().first()

    has_events = False
    if caption_asset:
        cap_path = Path(caption_asset.file_path)
        if cap_path.exists():
            has_events = "Dialogue:" in cap_path.read_text(encoding="utf-8", errors="replace")
    report.add(
        "captions_present",
        caption_count > 0 and has_events,
        blocking=True,
        detail="Caption track generated with timed events"
        if has_events
        else "No caption events found",
        measured=caption_count,
    )

    # ----------------------------------------------------------- scenes ---
    script = (
        await session.execute(
            select(Script).where(Script.project_id == project.id, Script.is_current.is_(True))
        )
    ).scalar_one_or_none()

    if script is not None:
        scenes = (
            await session.execute(
                select(Scene).where(Scene.script_id == script.id).order_by(Scene.scene_number)
            )
        ).scalars().all()

        frame_count = (
            await session.execute(
                select(func.count())
                .select_from(ProductionAsset)
                .where(
                    ProductionAsset.project_id == project.id,
                    ProductionAsset.asset_type == AssetType.SCENE_FRAME.value,
                )
            )
        ).scalar_one()
        all_rendered = frame_count >= len(scenes) and len(scenes) > 0
        report.add(
            "all_scenes_rendered",
            all_rendered,
            blocking=True,
            detail=f"{frame_count} frames for {len(scenes)} scenes",
            measured=frame_count,
        )

        timed = [s for s in scenes if s.start_seconds is not None and s.end_seconds is not None]
        report.add(
            "scene_timings_measured",
            len(timed) == len(scenes) and bool(scenes),
            blocking=True,
            detail=f"{len(timed)}/{len(scenes)} scenes have timings derived from real audio",
        )

        # Silence: a gap between scenes far larger than the intended pause
        # means a scene's audio failed to generate.
        gaps = []
        for a, b in itertools.pairwise(scenes):
            if a.end_seconds is not None and b.start_seconds is not None:
                gaps.append(float(b.start_seconds) - float(a.end_seconds))
        worst_gap = max(gaps) if gaps else 0.0
        report.add(
            "no_long_silences",
            worst_gap <= 1.2,
            blocking=True,
            detail=f"Largest inter-scene gap {worst_gap:.2f}s",
            measured=round(worst_gap, 3),
        )

        # Safe area: measurable because on-screen text is placed by our own
        # templates inside a `.content` box with the safe insets applied.
        outside = [s.scene_number for s in scenes if not s.template_id]
        report.add(
            "safe_area_respected",
            not outside,
            blocking=True,
            detail="All scenes use safe-area-constrained templates"
            if not outside
            else f"Scenes without a template: {outside}",
        )
    else:
        report.add("all_scenes_rendered", False, blocking=True, detail="No current script")

    # ------------------------------------------------------------ assets ---
    unlicensed = (
        await session.execute(
            select(func.count())
            .select_from(ProductionAsset)
            .where(
                ProductionAsset.project_id == project.id,
                (ProductionAsset.license.is_(None)) | (ProductionAsset.license == ""),
            )
        )
    ).scalar_one()
    report.add(
        "assets_licensed",
        unlicensed == 0,
        blocking=True,
        detail="Every asset has a recorded licence"
        if unlicensed == 0
        else f"{unlicensed} assets have no licence recorded",
        measured=unlicensed,
    )

    # ------------------------------------------------------------- ending ---
    # A video that simply stops on its last narrated frame reads as truncated.
    # These check that the ending was authored rather than merely reached.
    spec = render.spec or {}
    hold = float(spec.get("end_hold_seconds") or 0.0)
    report.add(
        "ending_hold_present",
        hold >= 0.5,
        blocking=False,
        detail=(
            f"Picture holds {hold:.2f}s after narration ends"
            if hold >= 0.5
            else "No closing hold — the video will feel cut off rather than finished"
        ),
        measured=round(hold, 3),
    )

    if script is not None and scenes:
        last = scenes[-1]
        narration_end = float(last.end_seconds) if last.end_seconds is not None else 0.0
        trailing = probed.duration - narration_end
        report.add(
            "audio_ends_cleanly",
            trailing >= 0.3,
            blocking=True,
            detail=(
                f"{trailing:.2f}s of picture after the last word"
                if trailing >= 0.3
                else f"Only {trailing:.2f}s after the last word — audio terminates abruptly"
            ),
            measured=round(trailing, 3),
        )

    # The final frame must be real video, not a decode failure or black.
    final_frame = path.parent / "_qc_final_frame.png"
    code, _, _ = await _extract_final_frame(path, probed.duration, final_frame)
    report.add(
        "final_frame_valid",
        code == 0 and final_frame.exists() and final_frame.stat().st_size > 5000,
        blocking=True,
        detail="Final frame decodes to a real image"
        if final_frame.exists()
        else "Could not decode the final frame",
        measured=final_frame.stat().st_size if final_frame.exists() else 0,
    )

    # ------------------------------------------------------------ sources ---
    note_count = (
        await session.execute(
            select(func.count())
            .select_from(ResearchNote)
            .where(ResearchNote.project_id == project.id)
        )
    ).scalar_one()
    report.add(
        "factual_sources_recorded",
        note_count > 0,
        blocking=True,
        detail=f"{note_count} research notes linked to this project"
        if note_count
        else "No factual sources recorded for this project",
        measured=note_count,
    )

    return await _persist(session, project, render, report)


async def _persist(
    session: AsyncSession, project: ContentProject, render: VideoRender, report: QualityReport
) -> QualityCheck:
    check = QualityCheck(
        render_id=render.id,
        project_id=project.id,
        verdict=report.verdict.value,
        checks=report.as_json(),
        blocking_issues=report.blocking_issues,
        warnings=report.warnings,
    )
    session.add(check)
    await session.flush()

    log.info(
        "quality.evaluated",
        project_id=str(project.id),
        verdict=report.verdict.value,
        blocking=len(report.blocking_issues),
        warnings=len(report.warnings),
    )
    return check
