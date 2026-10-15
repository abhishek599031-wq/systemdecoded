from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.errors import TerminalError
from app.providers.base import WordTiming
from app.providers.renderer.generic_scene import build_generic_runtime_props, validate_layout_plan


def _props(
    *,
    visual_type: str,
    labels: list[str],
    nodes: list[dict],
    connectors: list[dict],
    end_card: bool = False,
):
    return {
        "schema_version": 1,
        "visual_type": visual_type,
        "labels": labels,
        "nodes": nodes,
        "connectors": connectors,
        "timing_anchors": {"change": "system changes"},
        "animation_intent": "Animate the system state through each declared connection.",
        "end_card": end_card,
    }


def test_six_node_load_balancer_uses_safe_wrapped_layout() -> None:
    props = _props(
        visual_type="NETWORK_FLOW",
        labels=["TRAFFIC DISTRIBUTION"],
        nodes=[
            {"id": "client", "label": "CLIENT", "emphasis": "normal"},
            {"id": "balancer", "label": "LOAD BALANCER", "emphasis": "accent"},
            {"id": "a", "label": "SERVER A", "emphasis": "success"},
            {"id": "b", "label": "SERVER B", "emphasis": "success"},
            {"id": "c", "label": "SERVER C", "emphasis": "success"},
            {"id": "health", "label": "HEALTH CHECK", "emphasis": "accent"},
        ],
        connectors=[
            {"from": "client", "to": "balancer"},
            {"from": "balancer", "to": "a"},
            {"from": "balancer", "to": "b"},
            {"from": "balancer", "to": "c"},
            {"from": "health", "to": "balancer"},
        ],
    )
    report = validate_layout_plan(props)
    assert report["layout"] == "wrapped"
    assert len(report["nodes"]) == 6
    assert all(node["bottom"] <= report["caption_region_top"] for node in report["nodes"])


def test_database_failover_uses_centered_comparison_layout_with_end_card() -> None:
    props = _props(
        visual_type="COMPARISON",
        labels=["DATABASE FAILOVER"],
        nodes=[
            {"id": "primary", "label": "PRIMARY DATABASE", "emphasis": "accent"},
            {"id": "replica", "label": "SYNCED REPLICA", "emphasis": "success"},
            {"id": "failure", "label": "PRIMARY FAILED", "emphasis": "danger"},
            {"id": "promoted", "label": "REPLICA PROMOTED", "emphasis": "success"},
        ],
        connectors=[
            {"from": "primary", "to": "replica"},
            {"from": "primary", "to": "failure"},
            {"from": "failure", "to": "promoted"},
        ],
        end_card=True,
    )
    report = validate_layout_plan(props)
    assert report["layout"] == "comparison"
    assert report["has_end_card"] is True
    assert min(node["left"] for node in report["nodes"]) >= report["safe_region"]["left"]
    assert max(node["right"] for node in report["nodes"]) <= report["safe_region"]["right"]


def test_clipped_heading_regression_is_rejected_instead_of_shrunk() -> None:
    props = _props(
        visual_type="FLOW",
        labels=["SIGNED ANSWER", "CHECK SIGNATURE", "TRUSTED KEY CHAIN", "VERIFIED"],
        nodes=[{"id": "one", "label": "ONE", "emphasis": "normal"}],
        connectors=[],
    )
    with pytest.raises(TerminalError, match="heading exceeds"):
        validate_layout_plan(props)


def test_unreadable_node_label_is_rejected_instead_of_tiny_text() -> None:
    props = _props(
        visual_type="FLOW",
        labels=["SAFE HEADING"],
        nodes=[
            {
                "id": "overflow",
                "label": "THIS LABEL CANNOT REMAIN MOBILE READABLE",
                "emphasis": "danger",
            }
        ],
        connectors=[],
    )
    with pytest.raises(TerminalError, match="readable characters"):
        validate_layout_plan(props)


def test_comparison_branch_label_must_fit_without_tiny_text() -> None:
    props = _props(
        visual_type="COMPARISON",
        labels=["CLEAR COMPARISON"],
        nodes=[
            {"id": "root", "label": "ROOT", "emphasis": "accent"},
            {"id": "good", "label": "THIS BRANCH LABEL IS TOO LONG", "emphasis": "success"},
            {"id": "bad", "label": "SHORT BRANCH", "emphasis": "danger"},
        ],
        connectors=[
            {"from": "root", "to": "good"},
            {"from": "root", "to": "bad"},
        ],
    )
    with pytest.raises(TerminalError, match="comparison/wrapped card"):
        validate_layout_plan(props)


def test_tight_connector_label_is_left_for_browser_placement_or_omission() -> None:
    props = _props(
        visual_type="COMPARISON",
        labels=["BRANCH"],
        nodes=[
            {"id": "root", "label": "ROOT", "emphasis": "accent"},
            {"id": "left", "label": "LEFT", "emphasis": "success"},
            {"id": "right", "label": "RIGHT", "emphasis": "danger"},
        ],
        connectors=[{"from": "left", "to": "right", "label": "redirect"}],
    )
    report = validate_layout_plan(props)
    assert report["connectors"] == [{"from": "left", "to": "right", "kind": "normal"}]


def test_injection_direction_must_originate_at_danger_node() -> None:
    props = _props(
        visual_type="FLOW",
        labels=["INJECTION"],
        nodes=[
            {"id": "client", "label": "CLIENT", "emphasis": "normal"},
            {"id": "cache", "label": "CACHE", "emphasis": "accent"},
        ],
        connectors=[{"from": "client", "to": "cache", "kind": "injection"}],
    )
    with pytest.raises(TerminalError, match="injection must originate at a danger node"):
        validate_layout_plan(props)


def test_directed_connector_cycle_is_rejected() -> None:
    props = _props(
        visual_type="FLOW",
        labels=["NO CYCLE"],
        nodes=[
            {"id": "one", "label": "ONE", "emphasis": "normal"},
            {"id": "two", "label": "TWO", "emphasis": "normal"},
        ],
        connectors=[{"from": "one", "to": "two"}, {"from": "two", "to": "one"}],
    )
    with pytest.raises(TerminalError, match="directed connectors contain a cycle"):
        validate_layout_plan(props)


def test_runtime_carries_immutable_layout_evidence_for_chromium_qc() -> None:
    props = _props(
        visual_type="PROCESS",
        labels=["STATE CHANGE"],
        nodes=[
            {"id": "before", "label": "BEFORE", "emphasis": "normal"},
            {"id": "after", "label": "AFTER", "emphasis": "success"},
        ],
        connectors=[{"from": "before", "to": "after"}],
    )
    scene = SimpleNamespace(scene_number=1, template_props=props)
    runtime = build_generic_runtime_props(
        scene,
        words=[
            WordTiming(word="system", start=0.1, end=0.4),
            WordTiming(word="changes", start=0.4, end=0.8),
        ],
        scene_start=0,
        speech_duration=1,
        visual_duration=1.2,
    )
    assert runtime["layout_report"]["layout"] == "vertical"
    assert runtime["layout_report"]["caption_region_top"] == 500
