"""Reusable adapter from persisted declarative SceneSpecs to Motion Canvas."""

from __future__ import annotations

from typing import Any

from app.core.errors import TerminalError
from app.models.content import Scene
from app.providers.base import WordTiming
from app.providers.renderer.motion_timing import build_motion_timing_payload

SUPPORTED_VISUAL_TYPES = frozenset(
    {
        "TITLE_REVEAL",
        "FLOW",
        "PARALLEL_FLOW",
        "PROCESS",
        "COMPARISON",
        "DATABASE_FLOW",
        "NETWORK_FLOW",
        "TIMELINE",
        "CODE_REVEAL",
        "CALLOUT",
    }
)
SUPPORTED_EMPHASIS = frozenset({"normal", "accent", "danger", "success"})
SUPPORTED_CONNECTOR_KINDS = frozenset(
    {"normal", "injection", "replacement", "verification", "rejection"}
)

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
SAFE_X = 468
SAFE_TOP = -760
SAFE_BOTTOM = 500


def _layout_plan(props: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic portrait layout used by Motion Canvas.

    Keeping this calculation data-only lets the backend reject overcrowded
    SceneSpecs before Chromium is started. The TypeScript renderer mirrors the
    same constants and exposes its own report for a render-time cross-check.
    """
    nodes = props["nodes"]
    visual_type = props["visual_type"]
    comparison = visual_type in {"COMPARISON", "PARALLEL_FLOW"} and len(nodes) >= 3
    wrapped = len(nodes) >= 5
    if comparison:
        positions = [(0, -340)] + [
            (-220 if index % 2 == 0 else 220, -70 + (index // 2) * 235)
            for index in range(len(nodes) - 1)
        ]
        sizes = [(760, 150)] + [(400, 160)] * (len(nodes) - 1)
        layout = "comparison"
    elif wrapped:
        positions = [
            (-220 if index % 2 == 0 else 220, -330 + (index // 2) * 250)
            for index in range(len(nodes))
        ]
        sizes = [(400, 160)] * len(nodes)
        layout = "wrapped"
    else:
        gap = 230
        first = -((len(nodes) - 1) * gap) / 2
        positions = [(0, first + index * gap) for index in range(len(nodes))]
        sizes = [(760, 150)] * len(nodes)
        layout = "vertical"

    boxes = []
    for node, (x, y), (width, height) in zip(nodes, positions, sizes, strict=True):
        boxes.append(
            {
                "id": node["id"],
                "left": x - width / 2,
                "right": x + width / 2,
                "top": y - height / 2,
                "bottom": y + height / 2,
                "width": width,
                "height": height,
            }
        )
    return {
        "schema_version": 1,
        "canvas": {"width": CANVAS_WIDTH, "height": CANVAS_HEIGHT},
        "safe_region": {
            "left": -SAFE_X,
            "right": SAFE_X,
            "top": SAFE_TOP,
            "bottom": SAFE_BOTTOM,
        },
        "heading": {"left": -440, "right": 440, "top": -700, "bottom": -580},
        "layout": layout,
        "nodes": boxes,
        "connectors": [
            {
                "from": edge["from"],
                "to": edge["to"],
                "kind": edge.get("kind", "normal"),
            }
            for edge in props["connectors"]
        ],
        "caption_region_top": SAFE_BOTTOM,
        "has_end_card": bool(props.get("end_card")),
    }


def validate_layout_plan(props: dict[str, Any]) -> dict[str, Any]:
    """Reject clipped, colliding, unreadable, or overcrowded portrait layouts."""
    _validate_directed_graph(
        scene_number=int(props.get("scene_number", 0)),
        nodes=props["nodes"],
        connectors=props["connectors"],
    )
    report = _layout_plan(props)
    safe = report["safe_region"]
    errors: list[str] = []
    heading_text = " · ".join(props["labels"])
    if len(heading_text) > 36:
        errors.append("combined heading exceeds 36 characters")
    for node, box in zip(props["nodes"], report["nodes"], strict=True):
        if len(node["label"]) > 30:
            errors.append(f"node {node['id']!r} exceeds 30 readable characters")
        if box["width"] <= 400 and len(node["label"]) > 18:
            errors.append(f"node {node['id']!r} is too long for a comparison/wrapped card")
        if (
            box["left"] < safe["left"]
            or box["right"] > safe["right"]
            or box["top"] < safe["top"]
            or box["bottom"] > safe["bottom"]
        ):
            errors.append(f"node {node['id']!r} leaves the visual safe region")
    for index, first in enumerate(report["nodes"]):
        for second in report["nodes"][index + 1 :]:
            overlap_x = min(first["right"], second["right"]) - max(first["left"], second["left"])
            overlap_y = min(first["bottom"], second["bottom"]) - max(first["top"], second["top"])
            if overlap_x > 0 and overlap_y > 0:
                errors.append(f"nodes {first['id']!r} and {second['id']!r} overlap")
    if len(props["connectors"]) > max(0, len(props["nodes"]) + 1):
        errors.append("connector density is too high for a mobile diagram")
    if errors:
        raise TerminalError("Generic SceneSpec does not fit: " + "; ".join(errors))
    return report


def _validate_directed_graph(
    *, scene_number: int, nodes: list[dict[str, Any]], connectors: list[dict[str, Any]]
) -> None:
    """Validate typed directed edges without assuming a topic-specific graph."""
    by_id = {node["id"]: node for node in nodes}
    pairs: set[tuple[str, str]] = set()
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    for edge in connectors:
        source = edge["from"]
        target = edge["to"]
        kind = edge.get("kind", "normal")
        if source == target:
            raise TerminalError(f"Generic scene {scene_number} connector cannot point to itself")
        pair = (source, target)
        if pair in pairs:
            raise TerminalError(f"Generic scene {scene_number} has a duplicate directed connector")
        pairs.add(pair)
        adjacency[source].append(target)
        if kind in {"injection", "replacement"} and by_id[source]["emphasis"] != "danger":
            raise TerminalError(
                f"Generic scene {scene_number} {kind} must originate at a danger node"
            )
        if kind == "rejection" and by_id[target]["emphasis"] not in {
            "danger",
            "success",
        }:
            raise TerminalError(
                f"Generic scene {scene_number} rejection must end at a terminal state"
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise TerminalError(f"Generic scene {scene_number} directed connectors contain a cycle")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in adjacency[node_id]:
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in adjacency:
        visit(node_id)


def _validated_persisted_props(scene: Scene) -> dict[str, Any]:
    props = scene.template_props
    if not isinstance(props, dict) or props.get("schema_version") != 1:
        raise TerminalError(f"Generic scene {scene.scene_number} needs SceneSpec schema version 1")
    visual_type = props.get("visual_type")
    if visual_type not in SUPPORTED_VISUAL_TYPES:
        raise TerminalError(
            f"Generic scene {scene.scene_number} has unsupported visual type {visual_type!r}"
        )

    labels = props.get("labels")
    nodes = props.get("nodes")
    connectors = props.get("connectors")
    anchors = props.get("timing_anchors")
    if not isinstance(labels, list) or not 1 <= len(labels) <= 6:
        raise TerminalError(f"Generic scene {scene.scene_number} needs 1-6 labels")
    if any(not isinstance(label, str) or not label.strip() or len(label) > 42 for label in labels):
        raise TerminalError(f"Generic scene {scene.scene_number} has an invalid visual label")
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 6:
        raise TerminalError(f"Generic scene {scene.scene_number} needs 1-6 nodes")
    if not isinstance(connectors, list) or len(connectors) > 8:
        raise TerminalError(f"Generic scene {scene.scene_number} has invalid connectors")
    if not isinstance(anchors, dict) or not 1 <= len(anchors) <= 5:
        raise TerminalError(f"Generic scene {scene.scene_number} needs 1-5 timing anchors")

    node_ids: set[str] = set()
    for node in nodes:
        if (
            not isinstance(node, dict)
            or not isinstance(node.get("id"), str)
            or not node["id"].strip()
            or not isinstance(node.get("label"), str)
            or not node["label"].strip()
            or len(node["label"]) > 42
            or node.get("emphasis") not in SUPPORTED_EMPHASIS
        ):
            raise TerminalError(f"Generic scene {scene.scene_number} has an invalid node")
        node_ids.add(node["id"])
    if len(node_ids) != len(nodes):
        raise TerminalError(f"Generic scene {scene.scene_number} has duplicate or invalid nodes")
    for edge in connectors:
        if (
            not isinstance(edge, dict)
            or edge.get("from") not in node_ids
            or edge.get("to") not in node_ids
            or (
                "label" in edge and edge["label"] is not None and not isinstance(edge["label"], str)
            )
            or edge.get("kind", "normal") not in SUPPORTED_CONNECTOR_KINDS
        ):
            raise TerminalError(
                f"Generic scene {scene.scene_number} connector references an unknown node"
            )
    _validate_directed_graph(
        scene_number=scene.scene_number,
        nodes=nodes,
        connectors=connectors,
    )
    if any(
        not isinstance(key, str)
        or not key.strip()
        or not isinstance(value, str)
        or not value.strip()
        for key, value in anchors.items()
    ):
        raise TerminalError(f"Generic scene {scene.scene_number} has invalid timing anchors")
    if not isinstance(props.get("animation_intent"), str) or not props["animation_intent"].strip():
        raise TerminalError(f"Generic scene {scene.scene_number} needs animation intent")
    if not isinstance(props.get("end_card", False), bool):
        raise TerminalError(f"Generic scene {scene.scene_number} has invalid end_card")
    validate_layout_plan(props)
    return props


def build_generic_runtime_props(
    scene: Scene,
    *,
    words: list[WordTiming],
    scene_start: float,
    speech_duration: float,
    visual_duration: float,
) -> dict[str, Any]:
    """Attach the one canonical timing system to a persisted SceneSpec."""
    props = _validated_persisted_props(scene)
    local_words = [
        WordTiming(
            word=word.word,
            start=max(0.0, word.start - scene_start),
            end=min(speech_duration, word.end - scene_start),
        )
        for word in words
        if word.start >= scene_start - 0.001 and word.end <= scene_start + speech_duration + 0.001
    ]
    timing = build_motion_timing_payload(
        local_words,
        speech_duration,
        dict(props["timing_anchors"]),
    )
    return {
        "duration_seconds": round(visual_duration, 3),
        "speech_duration_seconds": round(speech_duration, 3),
        "timing": timing,
        "caption_safe_bottom": 430,
        "layout_report": validate_layout_plan(props),
    }
