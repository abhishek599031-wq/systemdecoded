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
from app.providers.renderer.scene_contract import payload_digest, validate_template_payload

log = get_logger("renderer.frames")


def _validate_browser_layout_report(report: object, expected: object) -> None:
    """Fail closed when the browser's actual layout plan leaves the safe canvas."""
    if not isinstance(report, dict):
        raise TerminalError("Generic renderer did not expose a layout report")
    safe = report.get("safe_region")
    nodes = report.get("nodes")
    heading = report.get("heading")
    if not isinstance(safe, dict) or not isinstance(nodes, list) or not isinstance(heading, dict):
        raise TerminalError("Generic renderer exposed an invalid layout report")
    boxes = [("heading", heading), *[(str(n.get("id")), n) for n in nodes if isinstance(n, dict)]]
    for name, box in boxes:
        if not isinstance(box, dict) or any(
            not isinstance(box.get(key), (int, float)) for key in ("left", "right", "top", "bottom")
        ):
            raise TerminalError(f"Generic renderer has invalid bounds for {name}")
        if (
            box["left"] < safe["left"]
            or box["right"] > safe["right"]
            or box["top"] < safe["top"]
            or box["bottom"] > safe["bottom"]
        ):
            raise TerminalError(f"Generic renderer clipped {name} outside its visual safe region")
        if box["bottom"] > report.get("caption_region_top", 500):
            raise TerminalError(f"Generic renderer placed {name} inside the caption region")

    connector_labels = report.get("connector_labels")
    if not isinstance(connector_labels, list):
        raise TerminalError("Generic renderer did not expose connector-label geometry")
    visible_labels: list[tuple[str, dict]] = []
    for item in connector_labels:
        if not isinstance(item, dict) or not isinstance(item.get("visible"), bool):
            raise TerminalError("Generic renderer exposed an invalid connector label")
        if not item["visible"]:
            if not isinstance(item.get("omitted_reason"), str):
                raise TerminalError("Omitted connector label has no geometric reason")
            continue
        label_name = f"{item.get('from')}->{item.get('to')} label"
        bounds = item.get("bounds")
        if not isinstance(bounds, dict) or any(
            not isinstance(bounds.get(key), (int, float))
            for key in ("left", "right", "top", "bottom")
        ):
            raise TerminalError(f"Generic renderer has invalid bounds for {label_name}")
        if item.get("measurement_source") != "browser-canvas-font-metrics":
            raise TerminalError(f"Generic renderer did not measure {label_name} in Chromium")
        if not isinstance(item.get("measured_text_width"), (int, float)):
            raise TerminalError(f"Generic renderer did not measure text for {label_name}")
        if item.get("font_size", 0) < 26:
            raise TerminalError(f"Generic renderer made {label_name} unreadably small")
        if any(
            item.get(flag) is not False
            for flag in (
                "line_collision",
                "arrowhead_collision",
                "node_collision",
                "label_collision",
            )
        ):
            raise TerminalError(f"Generic renderer collision detected for {label_name}")
        if (
            bounds["left"] < safe["left"]
            or bounds["right"] > safe["right"]
            or bounds["top"] < safe["top"]
            or bounds["bottom"] > safe["bottom"]
        ):
            raise TerminalError(f"Generic renderer clipped {label_name}")
        for node in nodes:
            if (
                bounds["left"] < node["right"] + 8
                and bounds["right"] > node["left"] - 8
                and bounds["top"] < node["bottom"] + 8
                and bounds["bottom"] > node["top"] - 8
            ):
                raise TerminalError(f"Generic renderer placed {label_name} on a node")
        for other_name, other in visible_labels:
            if (
                bounds["left"] < other["right"] + 8
                and bounds["right"] > other["left"] - 8
                and bounds["top"] < other["bottom"] + 8
                and bounds["bottom"] > other["top"] - 8
            ):
                raise TerminalError(f"Generic renderer overlapped {label_name} with {other_name}")
        visible_labels.append((label_name, bounds))

    if report.get("has_end_card"):
        if report.get("end_card_brand") != {
            "name": "SYSTEMDECODED",
            "tagline": "Complex Technology. Decoded.",
        }:
            raise TerminalError("Generic renderer has inconsistent final branding")
    if isinstance(expected, dict):
        expected_ids = [node.get("id") for node in expected.get("nodes", [])]
        actual_ids = [node.get("id") for node in nodes]
        if actual_ids != expected_ids or report.get("layout") != expected.get("layout"):
            raise TerminalError("Browser layout diverged from the validated immutable payload")
        if "connectors" in expected and report.get("connectors") != expected["connectors"]:
            raise TerminalError("Browser connector directions diverged from the payload")


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
                # Vite's production bundles use ES-module chunks. Chromium
                # blocks module imports between file:// URLs unless local file
                # access is explicitly enabled; templates are trusted build
                # artifacts mounted read-only into this local worker.
                "--allow-file-access-from-files",
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
        validate_template_payload(template.parent, spec.props)
        # Scope by immutable ownership, not just ordinal.  A new script may also
        # have a scene 02; it must never see the old script's frame directory.
        scene_dir = out_dir / spec.script_id / f"scene_{spec.scene_number:02d}_{spec.scene_id}"
        scene_dir.mkdir(parents=True, exist_ok=True)
        for stale in scene_dir.glob("*.png"):
            stale.unlink()

        frame_count = max(1, round(duration * self.fps))
        page = await self._browser.new_page(
            viewport={"width": spec.width, "height": spec.height}, device_scale_factor=1
        )
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "console",
            lambda message: page_errors.append(message.text) if message.type == "error" else None,
        )
        frames: list[Path] = []
        try:
            await page.add_init_script(
                f"window.__PROPS__ = {__import__('json').dumps(spec.props)};"
                f"window.__RENDER_CONTEXT__ = {
                    __import__('json').dumps(
                        {
                            'project_id': spec.project_id,
                            'script_id': spec.script_id,
                            'scene_id': spec.scene_id,
                            'payload_sha256': payload_digest(spec),
                        }
                    )
                };"
            )
            await page.goto(template.as_uri(), wait_until="load")
            # Fonts must be settled before the first capture, or text reflows
            # part-way through the sequence.
            await page.wait_for_function("window.ready === true", timeout=15_000)

            if spec.template_id == "generic_scene_spec":
                layout_report = await page.evaluate("() => window.__LAYOUT_REPORT__")
                _validate_browser_layout_report(layout_report, spec.props.get("layout_report"))

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
            browser_detail = (
                f" Browser errors: {' | '.join(page_errors[-5:])}" if page_errors else ""
            )
            raise RetryableError(
                f"Rendering scene {spec.scene_number} ({spec.template_id}) failed: "
                f"{exc}.{browser_detail}"
            ) from exc
        finally:
            await page.close()

        log.info(
            "renderer.scene_frames",
            scene=spec.scene_number,
            template=spec.template_id,
            project_id=spec.project_id,
            script_id=spec.script_id,
            scene_id=spec.scene_id,
            payload_sha256=payload_digest(spec),
            frames=len(frames),
            duration=round(duration, 2),
        )
        return SceneRenderResult(
            scene_number=spec.scene_number, frames=frames, keyframes=spec.keyframes
        )
