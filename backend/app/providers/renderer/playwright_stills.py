"""Scene rendering via headless Chromium (LOCAL provider).

ADR: ARCH §2.2 — scene templates are HTML/CSS/SVG rendered by Playwright, not
FFmpeg filter graphs. The templates stay openable in a browser, the `Scene`
row maps 1:1 onto `template_id + props`, and the visual identity lives in CSS
where it can be iterated in seconds.

V1 captures stills at declared keyframes and lets FFmpeg animate between them.
Templates already expose a deterministic `seek(t)`, so V2 can capture real
frame sequences without changing a single template.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.core.errors import RetryableError, TerminalError
from app.core.logging import get_logger
from app.providers.base import SceneRenderResult, SceneRenderSpec

log = get_logger("renderer.playwright")


class PlaywrightStillsRenderer:
    name = "playwright-stills"

    def __init__(self, templates_dir: Path | None = None) -> None:
        self.templates_dir = templates_dir or settings.SCENE_TEMPLATES_DIR
        self._playwright = None
        self._browser = None

    async def __aenter__(self) -> PlaywrightStillsRenderer:
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
                # Deterministic rasterisation: without this, identical props can
                # produce byte-different screenshots across runs.
                "--force-color-profile=srgb",
                "--disable-lcd-text",
                "--hide-scrollbars",
            ]
        )
        log.info("renderer.browser_started", version=self._browser.version)

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
            raise TerminalError(
                f"Scene template {template_id!r} not found. Available: {available}"
            )
        return path

    async def render(self, spec: SceneRenderSpec, out_dir: Path) -> SceneRenderResult:
        if self._browser is None:
            raise TerminalError("Renderer not started; use `async with` or call start()")

        template = self.template_path(spec.template_id)
        out_dir.mkdir(parents=True, exist_ok=True)

        page = await self._browser.new_page(
            viewport={"width": spec.width, "height": spec.height},
            device_scale_factor=1,
        )
        frames: list[Path] = []
        try:
            # Props are injected before any script runs, so the template never
            # renders a flash of demo content.
            await page.add_init_script(f"window.__PROPS__ = {json.dumps(spec.props)};")
            await page.goto(template.as_uri(), wait_until="load")

            # Wait for fonts. Screenshotting early captures fallback metrics and
            # the text reflows afterwards — a subtle, maddening class of bug.
            await page.wait_for_function("window.ready === true", timeout=15_000)

            for index, t in enumerate(spec.keyframes):
                await page.evaluate("(t) => window.seek(t)", t)
                frame_path = out_dir / f"scene_{spec.scene_number:02d}_k{index}.png"
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
            "renderer.scene_rendered",
            scene=spec.scene_number,
            template=spec.template_id,
            frames=len(frames),
        )
        return SceneRenderResult(
            scene_number=spec.scene_number, frames=frames, keyframes=spec.keyframes
        )
