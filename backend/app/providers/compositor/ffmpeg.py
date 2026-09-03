"""FFmpeg composition (LOCAL provider).

FFmpeg's job here is composition, timing, audio and encoding — not visual
design. Design happens in the scene templates; this module assembles what they
produced (ARCH §2.2).

Two decisions worth stating, both learned from reviewing actual output:

1. **Scenes are one continuous frame sequence, not clips joined by crossfades.**
   The earlier crossfade approach made the payoff visually muddy: two scenes'
   typography overlapped mid-dissolve and neither was readable. Since the
   renderer now produces real per-frame animation, a clean cut is both sharper
   and far simpler — the whole xfade filter graph is gone.

2. **Audio gets a light, fixed chain**, not per-render tuning: high-pass,
   gentle compression, then loudness normalisation. Enough to sound produced,
   restrained enough to stay natural.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import RetryableError, TerminalError
from app.core.logging import get_logger
from app.providers.base import CompositionSpec, VideoResult

log = get_logger("compositor.ffmpeg")

# Fade the very end of the audio so the file never stops mid-waveform.
END_FADE_SECONDS = 0.45


def _concat_quote(path: Path) -> str:
    """Quote a path for FFmpeg's concat demuxer.

    The demuxer takes single-quoted paths, and the only way to include a literal
    single quote is to close the quoting, emit an escaped quote, and reopen it.
    """
    text = str(path).replace("\\", "/")
    escaped = text.replace("'", "'\\''")
    return f"'{escaped}'"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    width: int
    height: int
    duration: float
    fps: float
    has_audio: bool
    bytes: int
    video_codec: str
    audio_codec: str | None
    # Per-stream, not container. The container reports the longest stream, so
    # only this reveals a video track that ends early.
    video_duration: float


async def _run(cmd: list[str], *, timeout: float = 1800.0) -> tuple[int, str, str]:
    log.debug("ffmpeg.exec", cmd=" ".join(shlex.quote(c) for c in cmd[:14]) + " …")
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        raise RetryableError(f"FFmpeg timed out after {timeout}s") from None
    return (
        process.returncode or 0,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


async def probe(path: Path) -> ProbeResult:
    """Read real properties back off a file. QC asserts against this, not intent."""
    code, out, err = await _run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        timeout=60,
    )
    if code != 0:
        raise TerminalError(f"ffprobe failed for {path.name}: {err.strip()[:400]}")

    data = json.loads(out)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if video is None:
        raise TerminalError(f"{path.name} has no video stream")

    fps = 0.0
    try:
        num, _, den = (video.get("avg_frame_rate") or "0/1").partition("/")
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    def _duration(stream: dict) -> float:
        raw = stream.get("duration")
        if raw:
            return float(raw)
        # MP4 streams sometimes report duration only via tags.
        tags = stream.get("tags") or {}
        for key in ("DURATION", "duration"):
            if key in tags:
                try:
                    h, m, sec = str(tags[key]).split(":")
                    return int(h) * 3600 + int(m) * 60 + float(sec)
                except ValueError:
                    pass
        return 0.0

    return ProbeResult(
        video_duration=_duration(video),
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        duration=float(data.get("format", {}).get("duration", 0.0)),
        fps=fps,
        has_audio=audio is not None,
        bytes=int(data.get("format", {}).get("size", 0)),
        video_codec=video.get("codec_name", ""),
        audio_codec=audio.get("codec_name") if audio else None,
    )


async def measure_loudness(path: Path) -> tuple[float | None, float | None]:
    """Return (integrated LUFS, true peak dBFS) using the EBU R128 filter."""
    code, _, err = await _run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        timeout=300,
    )
    if code != 0:
        return None, None
    start, end = err.rfind("{"), err.rfind("}")
    if start == -1 or end == -1:
        return None, None
    try:
        data = json.loads(err[start : end + 1])
        return float(data["input_i"]), float(data["input_tp"])
    except (ValueError, KeyError):
        return None, None


class FFmpegCompositor:
    name = "ffmpeg"

    async def compose(self, spec: CompositionSpec) -> VideoResult:
        if not spec.scenes:
            raise TerminalError("Cannot compose a video with no scenes")
        spec.output_path.parent.mkdir(parents=True, exist_ok=True)

        work_dir = spec.output_path.parent / "_work"
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)

        list_path = work_dir / "frames.txt"
        total_frames = self._write_concat_list(spec, list_path)

        silent = work_dir / "video_track.mp4"
        await self._encode_sequence(list_path, silent, spec, total_frames)
        await self._mux(silent, spec)

        result = await probe(spec.output_path)
        lufs, peak = await measure_loudness(spec.output_path)

        log.info(
            "compositor.done",
            output=spec.output_path.name,
            duration=round(result.duration, 2),
            size=f"{result.width}x{result.height}",
            codecs=f"{result.video_codec}/{result.audio_codec}",
            lufs=lufs,
            mb=round(result.bytes / 1_048_576, 2),
        )
        return VideoResult(
            path=spec.output_path,
            width=result.width,
            height=result.height,
            fps=round(result.fps),
            duration_seconds=result.duration,
            bytes=result.bytes,
            loudness_lufs=lufs,
            peak_dbfs=peak,
        )

    # --------------------------------------------------------------- video ---
    def _write_concat_list(self, spec: CompositionSpec, list_path: Path) -> int:
        """Write an FFmpeg concat list naming every scene frame in order.

        Deliberately a list file rather than a renumbered copy of the frames.
        The earlier approach hard-linked ~1000 PNGs into one sequence directory
        and fed image2 a `%06d` pattern; on a Docker bind mount those writes
        were not all visible by the time FFmpeg opened the sequence, so it
        silently encoded only the prefix it could see — producing a 3-second
        video against 42 seconds of audio, with a zero exit code.

        Referencing the frames where they already are removes the duplication,
        the race, and ~1000 filesystem operations at once.
        """
        frame_duration = 1.0 / spec.fps
        lines: list[str] = []
        total = 0
        last: Path | None = None

        for scene in spec.scenes:
            if not scene.frames:
                raise TerminalError(f"Scene {scene.scene_number} has no rendered frames")
            for frame in scene.frames:
                if not frame.exists():
                    raise TerminalError(f"Frame missing at compose time: {frame}")
                lines.append(f"file {_concat_quote(frame)}")
                lines.append(f"duration {frame_duration:.6f}")
                last = frame
                total += 1

        if total == 0 or last is None:
            raise TerminalError("Frame sequence is empty")
        # The concat demuxer drops the final entry's duration unless the last
        # file is repeated, which would clip the closing hold by one frame.
        lines.append(f"file {_concat_quote(last)}")

        list_path.parent.mkdir(parents=True, exist_ok=True)
        list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return total

    async def _encode_sequence(
        self, list_path: Path, out_path: Path, spec: CompositionSpec, frames: int
    ) -> None:
        code, _, err = await _run(
            ["ffmpeg", "-y", "-hide_banner", "-nostats",
             "-f", "concat", "-safe", "0", "-i", str(list_path),
             "-vf", f"format=yuv420p,scale={spec.width}:{spec.height}",
             "-fps_mode", "cfr", "-r", str(spec.fps),
             # Near-lossless intermediate: the final encode is the only place
             # quality should be traded, not here.
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
             str(out_path)]
        )
        if code != 0:
            raise TerminalError(f"Sequence encode failed ({frames} frames): {err.strip()[-600:]}")

        # Verify the encoder actually consumed every frame. It exits 0 when it
        # reads a short sequence, so silence here is not success.
        produced = await probe(out_path)
        expected = frames / spec.fps
        if produced.duration < expected - 0.2:
            raise TerminalError(
                f"Encoded video is {produced.duration:.2f}s but {frames} frames at "
                f"{spec.fps}fps should be {expected:.2f}s — the frame sequence was "
                "incomplete when FFmpeg read it"
            )

    # ---------------------------------------------------------------- mux ---
    def _audio_chain(self, spec: CompositionSpec, narration_label: str) -> str:
        """Light, fixed post-processing. Produced, not processed-sounding.

        high-pass  — removes rumble below the voice
        compressor — gentle 2.5:1 to even out delivery, slow enough not to pump
        loudnorm   — EBU R128 to the platform target
        """
        duration = spec.extra.get("total_duration")
        fade = ""
        if isinstance(duration, int | float) and duration > END_FADE_SECONDS:
            start = max(0.0, float(duration) - END_FADE_SECONDS)
            fade = f",afade=t=out:st={start:.3f}:d={END_FADE_SECONDS}"
        return (
            f"[{narration_label}]highpass=f=75,"
            "acompressor=threshold=-18dB:ratio=2.5:attack=12:release=220:makeup=1.6,"
            f"loudnorm=I={spec.target_lufs}:TP=-1.5:LRA=11{fade}[a]"
        )

    async def _mux(self, video_track: Path, spec: CompositionSpec) -> None:
        """Burn captions, mix audio and effects, normalise, encode for YouTube."""
        inputs = ["-i", str(video_track), "-i", str(spec.narration_audio)]
        cues = spec.extra.get("sfx_cues") or []
        for cue in cues:
            inputs += ["-i", str(cue["path"])]

        # ---- video: captions ----
        if spec.subtitle_path is not None:
            ass = str(spec.subtitle_path).replace("\\", "/").replace(":", "\\:")
            fonts_dir = str(spec.extra.get("fonts_dir", "")).replace("\\", "/").replace(":", "\\:")
            fonts_arg = f":fontsdir='{fonts_dir}'" if fonts_dir else ""
            video_filter = f"[0:v]ass='{ass}'{fonts_arg}[v];"
        else:
            video_filter = "[0:v]null[v];"

        # ---- audio: narration + placed effects ----
        parts: list[str] = ["[1:a]aresample=48000[nar]"]
        if cues:
            labels = ["nar"]
            for i, cue in enumerate(cues):
                stream = 2 + i
                label = f"s{i}"
                delay_ms = max(0, int(float(cue["at_seconds"]) * 1000))
                gain = float(cue.get("gain", 0.5))
                parts.append(
                    f"[{stream}:a]aresample=48000,volume={gain:.3f},"
                    f"adelay={delay_ms}|{delay_ms}[{label}]"
                )
                labels.append(label)
            joined = "".join(f"[{n}]" for n in labels)
            # `longest` would let a late cue extend the file past the narration;
            # `first` keeps narration authoritative over the runtime.
            parts.append(f"{joined}amix=inputs={len(labels)}:duration=first:normalize=0[mixed]")
            narration_label = "mixed"
        else:
            narration_label = "nar"

        parts.append(self._audio_chain(spec, narration_label))
        audio_filter = ";".join(parts)

        code, _, err = await _run(
            ["ffmpeg", "-y", "-hide_banner", "-nostats", *inputs,
             "-filter_complex", video_filter + audio_filter,
             "-map", "[v]", "-map", "[a]",
             # Publishing master: CRF 16 with a slow preset. YouTube will
             # re-encode, so the master must protect thin cyan lines and
             # typography on near-black rather than chase a small file.
             "-c:v", "libx264", "-preset", "slow", "-crf", "16",
             "-profile:v", "high", "-level", "4.2", "-pix_fmt", "yuv420p",
             "-x264-params", "ref=4:bframes=3:aq-mode=3:aq-strength=1.1",
             "-r", str(spec.fps),
             "-movflags", "+faststart",
             "-c:a", "aac", "-b:a", "256k", "-ar", "48000", "-ac", "2",
             str(spec.output_path)],
            timeout=2400,
        )
        if code != 0:
            raise TerminalError(f"Final mux failed: {err.strip()[-800:]}")
