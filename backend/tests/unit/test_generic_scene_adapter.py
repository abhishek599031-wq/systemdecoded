from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.errors import TerminalError
from app.providers.base import WordTiming
from app.providers.renderer.generic_scene import build_generic_runtime_props


def _scene(**overrides):
    props = {
        "schema_version": 1,
        "visual_type": "NETWORK_FLOW",
        "labels": ["SIGNED ANSWER", "VERIFY", "TRUSTED KEY"],
        "nodes": [
            {"id": "answer", "label": "SIGNED ANSWER", "emphasis": "accent"},
            {"id": "resolver", "label": "VERIFY", "emphasis": "normal"},
            {"id": "key", "label": "TRUSTED KEY", "emphasis": "success"},
        ],
        "connectors": [
            {"from": "answer", "to": "resolver"},
            {"from": "key", "to": "resolver"},
        ],
        "timing_anchors": {"answer_arrives": "signed answer", "verify": "resolver checks"},
        "animation_intent": "Reveal the answer, then verification.",
    }
    props.update(overrides)
    return SimpleNamespace(scene_number=3, template_props=props)


def test_runtime_timing_uses_canonical_scene_local_word_events() -> None:
    words = [
        WordTiming(word="A", start=4.9, end=5.0),
        WordTiming(word="signed", start=5.2, end=5.5),
        WordTiming(word="answer", start=5.5, end=5.9),
        WordTiming(word="arrives", start=5.9, end=6.2),
        WordTiming(word="and", start=6.2, end=6.35),
        WordTiming(word="the", start=6.35, end=6.45),
        WordTiming(word="resolver", start=6.45, end=6.8),
        WordTiming(word="checks", start=6.8, end=7.1),
        WordTiming(word="it", start=7.1, end=7.3),
        WordTiming(word="next", start=7.4, end=7.7),
    ]

    runtime = build_generic_runtime_props(
        _scene(),
        words=words,
        scene_start=5.0,
        speech_duration=2.3,
        visual_duration=2.42,
    )

    assert runtime["duration_seconds"] == 2.42
    assert runtime["speech_duration_seconds"] == 2.3
    assert runtime["caption_safe_bottom"] == 430
    assert runtime["layout_report"]["layout"] == "vertical"
    assert runtime["timing"]["source"] == "canonical-caption-word-timing"
    events = {event["id"]: event for event in runtime["timing"]["events"]}
    assert events["answer_arrives"]["start"] == pytest.approx(0.2)
    assert events["verify"]["start"] == pytest.approx(1.45)
    assert all(word["word"] != "next" for word in runtime["timing"]["words"])


def test_unknown_connector_node_is_rejected() -> None:
    with pytest.raises(TerminalError, match="unknown node"):
        build_generic_runtime_props(
            _scene(connectors=[{"from": "answer", "to": "missing"}]),
            words=[],
            scene_start=0,
            speech_duration=1,
            visual_duration=1,
        )


def test_nullable_connector_label_from_persisted_scene_is_accepted() -> None:
    runtime = build_generic_runtime_props(
        _scene(
            connectors=[
                {"from": "answer", "to": "resolver", "label": None},
                {"from": "key", "to": "resolver", "label": None},
            ]
        ),
        words=[
            WordTiming(word="signed", start=0.0, end=0.2),
            WordTiming(word="answer", start=0.2, end=0.4),
            WordTiming(word="resolver", start=0.4, end=0.7),
            WordTiming(word="checks", start=0.7, end=0.9),
        ],
        scene_start=0,
        speech_duration=1,
        visual_duration=1,
    )

    assert runtime["layout_report"]["layout"] == "vertical"


def test_unknown_visual_type_is_rejected() -> None:
    with pytest.raises(TerminalError, match="unsupported visual type"):
        build_generic_runtime_props(
            _scene(visual_type="DNSSEC_SPECIAL_CASE"),
            words=[],
            scene_start=0,
            speech_duration=1,
            visual_duration=1,
        )


def test_invalid_node_emphasis_is_rejected() -> None:
    scene = _scene()
    scene.template_props["nodes"][0]["emphasis"] = "dnssec-blue"
    with pytest.raises(TerminalError, match="invalid node"):
        build_generic_runtime_props(
            scene,
            words=[],
            scene_start=0,
            speech_duration=1,
            visual_duration=1,
        )
