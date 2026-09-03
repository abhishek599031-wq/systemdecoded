"""Frame-sequence scene rendering (LOCAL provider).

The V2 renderer anticipated in ARCH §2.2. Where `PlaywrightStillsRenderer`
captures a few keyframes and lets FFmpeg fake motion with a Ken Burns push,
this drives each template's deterministic `seek(t)` once per output frame and
captures the real thing.

That difference is what lets a scene *demonstrate* its mechanism — a signal
travelling and then being cut, two columns resolving in step, a timer actually
running down — rather than gesturing at it with a slow zoom.

Templates needed no changes to support this: they already exposed `seek(t)`,
which was the whole point of defining it up front.
"""

from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.core.errors import RetryableError, TerminalError
from app.core.logging import get_logger
from app.providers.base import SceneRenderResult, SceneRenderSpec

log = get_logger("renderer.frames")


class PlaywrightFrameRenderer:
    name = "playwright-frames"

    def __init__(self, templates_dir: Path | None = None, fps: int | None = None) -> None:
        self.templates_dir = templates_dir or settings.SCENE_TEMPLATES_DIR
        self.fps = fps or settings.VIDEO_FPS
        self._playwright = None
        self._browser = None

    async def __aenter__(self) -> PlaywrightFrameRenderer:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    async def start(self) -> None:
        if self._browser is not None:
            return
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                # Deterministic rasterisation: identical props must yield
                # identical pixels across runs.
                "--force-color-profile=srgb",
                "--disable-lcd-text",
                "--hide-scrollbars",
            ]
        )
        log.info("renderer.browser_started", version=self._browser.version, fps=self.fps)

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    def template_path(self, template_id: str) -> Path:
        path = self.templates_dir / template_id / "index.html"
        if not path.exists():
            available = sorted(
                p.name for p in self.templates_dir.iterdir() if (p / "index.html").exists()
            )
            raise TerminalError(f"Scene template {template_id!r} not found. Available: {available}")
        return path

    async def render(
        self, spec: SceneRenderSpec, out_dir: Path, duration: float | None = None
    ) -> SceneRenderResult:
        """Capture one PNG per output frame for `duration` seconds."""
        if self._browser is None:
            raise TerminalError("Renderer not started; use `async with` or call start()")
        if duration is None or duration <= 0:
            raise TerminalError(f"Scene {spec.scene_number} needs a measured duration to render")

        template = self.template_path(spec.template_id)
        scene_dir = out_dir / f"scene_{spec.scene_number:02d}"
        scene_dir.mkdir(parents=True, exist_ok=True)
        for stale in scene_dir.glob("*.png"):
            stale.unlink()

        frame_count = max(1, round(duration * self.fps))
        page = await self._browser.new_page(
            viewport={"width": spec.width, "height": spec.height}, device_scale_factor=1
        )
        frames: list[Path] = []
        try:
            await page.add_init_script(
                f"window.__PROPS__ = {__import__('json').dumps(spec.props)};"
            )
            await page.goto(template.as_uri(), wait_until="load")
            # Fonts must be settled before the first capture, or text reflows
            # part-way through the sequence.
            await page.wait_for_function("window.ready === true", timeout=15_000)

            # scene.js boots at t=1 so a template is previewable in its final
            # state. Rewind before capturing: a `draw()` that mutates state
            # one-way (say, swapping digit text) would otherwise render the
            # whole scene in its end state. Templates are required to be
            # deterministic, and this makes a violation show up immediately.
            await page.evaluate("() => window.seek(0)")

            for index in range(frame_count):
                t = index / max(1, frame_count - 1) if frame_count > 1 else 1.0
                await page.evaluate("(t) => window.seek(t)", t)
                frame_path = scene_dir / f"f_{index:05d}.png"
                await page.screenshot(path=str(frame_path), type="png")
                frames.append(frame_path)

        except TerminalError:
            raise
        except Exception as exc:
            raise RetryableError(
                f"Rendering scene {spec.scene_number} ({spec.template_id}) failed: {exc}"
            ) from exc
        finally:
            await page.close()

        log.info(
            "renderer.scene_frames",
            scene=spec.scene_number,
            template=spec.template_id,
            frames=len(frames),
            duration=round(duration, 2),
        )
        return SceneRenderResult(
            scene_number=spec.scene_number, frames=frames, keyframes=spec.keyframes
        )
