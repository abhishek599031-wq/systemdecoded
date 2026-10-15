from __future__ import annotations

import struct
from copy import deepcopy
from importlib.util import find_spec
from pathlib import Path

import pytest

from app.config import settings
from app.providers.base import SceneRenderSpec
from app.providers.renderer.generic_scene import validate_layout_plan
from app.providers.renderer.playwright_frames import (
    PlaywrightFrameRenderer,
    _validate_browser_layout_report,
)

pytestmark = [
    pytest.mark.media,
    pytest.mark.skipif(
        find_spec("playwright") is None,
        reason="Playwright is installed only in the worker image",
    ),
]


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def _runtime_props(scene: dict) -> dict:
    props = deepcopy(scene)
    props.update(
        {
            "duration_seconds": 1.0,
            "speech_duration_seconds": 0.8,
            "caption_safe_bottom": 430,
            "layout_report": validate_layout_plan(props),
            "timing": {
                "schema_version": 1,
                "source": "canonical-caption-word-timing",
                "audio_duration_seconds": 0.8,
                "words": [],
                "events": [],
            },
        }
    )
    return props


SYNTHETIC_GEOMETRY_CASES = [
    {
        "schema_version": 1,
        "visual_type": "NETWORK_FLOW",
        "labels": ["TRAFFIC ROUTING"],
        "nodes": [
            {"id": "client", "label": "CLIENT", "emphasis": "normal"},
            {"id": "router", "label": "ROUTER", "emphasis": "accent"},
            {"id": "one", "label": "SERVER ONE", "emphasis": "success"},
            {"id": "two", "label": "SERVER TWO", "emphasis": "success"},
            {"id": "three", "label": "SERVER THREE", "emphasis": "success"},
            {"id": "health", "label": "HEALTH CHECK", "emphasis": "accent"},
        ],
        "connectors": [
            {"from": "client", "to": "router", "label": "REQUEST"},
            {"from": "router", "to": "one", "label": "ROUTE"},
            {"from": "router", "to": "two", "label": "ROUTE"},
            {"from": "router", "to": "three", "label": "ROUTE"},
            {"from": "health", "to": "router", "label": "HEALTH", "kind": "verification"},
        ],
        "timing_anchors": {"request": "request"},
        "animation_intent": "Move a request through a router to healthy servers.",
        "end_card": False,
    },
    {
        "schema_version": 1,
        "visual_type": "COMPARISON",
        "labels": ["DATABASE FAILOVER"],
        "nodes": [
            {"id": "primary", "label": "PRIMARY", "emphasis": "accent"},
            {"id": "replica", "label": "REPLICA", "emphasis": "success"},
            {"id": "failure", "label": "FAILED PRIMARY", "emphasis": "danger"},
            {"id": "promoted", "label": "NEW PRIMARY", "emphasis": "success"},
        ],
        "connectors": [
            {"from": "primary", "to": "replica", "label": "SYNC"},
            {"from": "primary", "to": "failure", "label": "OUTAGE"},
            {"from": "failure", "to": "promoted", "label": "PROMOTE", "kind": "replacement"},
        ],
        "timing_anchors": {"failure": "failed"},
        "animation_intent": "Show a replica replacing a failed database.",
        "end_card": True,
    },
]


@pytest.mark.parametrize("scene", SYNTHETIC_GEOMETRY_CASES)
async def test_chromium_connector_labels_have_measured_collision_free_geometry(
    scene: dict,
) -> None:
    props = _runtime_props(scene)
    async with PlaywrightFrameRenderer(
        templates_dir=settings.SCENE_TEMPLATES_DIR,
        fps=1,
    ) as renderer:
        assert renderer._browser is not None
        page = await renderer._browser.new_page(viewport={"width": 1080, "height": 1920})
        try:
            await page.add_init_script(f"window.__PROPS__ = {__import__('json').dumps(props)};")
            await page.goto(renderer.template_path("generic_scene_spec").as_uri())
            await page.wait_for_function("window.ready === true", timeout=15_000)
            report = await page.evaluate("() => window.__LAYOUT_REPORT__")
        finally:
            await page.close()

    _validate_browser_layout_report(report, props["layout_report"])
    visible = [item for item in report["connector_labels"] if item["visible"]]
    assert len(report["connector_labels"]) == len(scene["connectors"])
    assert all(
        item["measurement_source"] == "browser-canvas-font-metrics"
        for item in report["connector_labels"]
    )
    assert all(item["visible"] or item["omitted_reason"] for item in report["connector_labels"])
    assert all(item["measurement_source"] == "browser-canvas-font-metrics" for item in visible)
    assert all(not item["line_collision"] for item in visible)
    assert all(not item["arrowhead_collision"] for item in visible)
    assert all(not item["node_collision"] for item in visible)


async def test_built_generic_scene_spec_renders_real_motion_canvas_frames(
    tmp_path: Path,
) -> None:
    props = {
        "schema_version": 1,
        "visual_type": "NETWORK_FLOW",
        "labels": ["SIGNED DNS ANSWER", "VERIFY", "TRUSTED KEYS"],
        "nodes": [
            {"id": "answer", "label": "SIGNED ANSWER", "emphasis": "accent"},
            {"id": "resolver", "label": "RESOLVER CHECKS", "emphasis": "normal"},
            {"id": "trust", "label": "TRUSTED KEY", "emphasis": "success"},
        ],
        "connectors": [
            {"from": "answer", "to": "resolver", "label": "signature"},
            {"from": "trust", "to": "resolver", "label": "chain"},
        ],
        "timing_anchors": {"answer": "signed answer", "verify": "resolver checks"},
        "animation_intent": "Reveal the signed answer, then its verification path.",
        "duration_seconds": 1.0,
        "speech_duration_seconds": 0.88,
        "caption_safe_bottom": 430,
        "layout_report": {
            "schema_version": 1,
            "canvas": {"width": 1080, "height": 1920},
            "safe_region": {"left": -468, "right": 468, "top": -760, "bottom": 500},
            "heading": {"left": -440, "right": 440, "top": -700, "bottom": -580},
            "layout": "vertical",
            "nodes": [
                {
                    "id": "answer",
                    "left": -380,
                    "right": 380,
                    "top": -305,
                    "bottom": -155,
                    "width": 760,
                    "height": 150,
                },
                {
                    "id": "resolver",
                    "left": -380,
                    "right": 380,
                    "top": -75,
                    "bottom": 75,
                    "width": 760,
                    "height": 150,
                },
                {
                    "id": "trust",
                    "left": -380,
                    "right": 380,
                    "top": 155,
                    "bottom": 305,
                    "width": 760,
                    "height": 150,
                },
            ],
            "caption_region_top": 500,
            "has_end_card": False,
        },
        "timing": {
            "schema_version": 1,
            "source": "canonical-caption-word-timing",
            "audio_duration_seconds": 0.88,
            "words": [
                {"word": "signed", "start": 0.08, "end": 0.2},
                {"word": "answer", "start": 0.2, "end": 0.32},
                {"word": "resolver", "start": 0.44, "end": 0.58},
                {"word": "checks", "start": 0.58, "end": 0.72},
            ],
            "events": [
                {"id": "answer", "phrase": "signed answer", "start": 0.08, "end": 0.32},
                {"id": "verify", "phrase": "resolver checks", "start": 0.44, "end": 0.72},
            ],
        },
    }
    spec = SceneRenderSpec(
        project_id="project-video-3",
        script_id="script-version-3",
        scene_id="scene-3",
        scene_number=3,
        template_id="generic_scene_spec",
        props=props,
    )

    async with PlaywrightFrameRenderer(
        templates_dir=settings.SCENE_TEMPLATES_DIR,
        fps=5,
    ) as renderer:
        result = await renderer.render(spec, tmp_path, duration=1.0)

    assert len(result.frames) == 5
    assert all(_png_size(frame) == (1080, 1920) for frame in result.frames)
    assert result.frames[0].read_bytes() != result.frames[-1].read_bytes()
