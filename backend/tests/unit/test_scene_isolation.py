from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.errors import TerminalError
from app.providers.renderer.scene_contract import (
    build_scene_spec,
    payload_digest,
    validate_template_payload,
)


def _ids() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def _spec(topic: str, props: dict):
    project_id, script_id, scene_id = _ids()
    project = SimpleNamespace(id=project_id, current_script_id=script_id)
    script = SimpleNamespace(id=script_id, project_id=project_id)
    scene = SimpleNamespace(
        id=scene_id,
        script_id=script_id,
        scene_number=1,
        template_id=topic,
        template_props=props,
    )
    return build_scene_spec(project, script, scene, width=1080, height=1920)


def test_project_b_payload_never_inherits_project_a_content() -> None:
    project_a = _spec(
        "code_reveal",
        {"code": "418902", "seconds_start": 30, "headline": "Nobody sent this code"},
    )
    project_b = _spec(
        "salted_hash",
        {
            "password": "correct horse",
            "accounts": [
                {"name": "Account A", "salt": "A7C2", "verifier": "$argon2id$A9…"},
                {"name": "Account B", "salt": "F4E1", "verifier": "$argon2id$7D…"},
            ],
        },
    )

    assert project_a.project_id != project_b.project_id
    assert project_a.script_id != project_b.script_id
    assert project_a.scene_id != project_b.scene_id
    assert payload_digest(project_a) != payload_digest(project_b)
    serialized_b = json.dumps(project_b.props).lower()
    for forbidden in ("418902", "30-second", "nobody sent", "never communicate", "totp"):
        assert forbidden not in serialized_b


def test_scene_ownership_mismatch_fails_instead_of_rendering() -> None:
    project_id, script_id, scene_id = _ids()
    project = SimpleNamespace(id=project_id, current_script_id=script_id)
    wrong_script = SimpleNamespace(id=script_id, project_id=uuid.uuid4())
    scene = SimpleNamespace(
        id=scene_id,
        script_id=script_id,
        scene_number=2,
        template_id="salted_hash",
        template_props={"password": "example"},
    )
    with pytest.raises(TerminalError, match="does not belong"):
        build_scene_spec(project, wrong_script, scene, width=1080, height=1920)


def test_template_missing_explicit_props_fails_without_demo_fallback(tmp_path: Path) -> None:
    template = tmp_path / "parallel_compute"
    template.mkdir()
    (template / "manifest.json").write_text(
        json.dumps({"required_props": ["title", "badge", "rows"]}), encoding="utf-8"
    )
    with pytest.raises(TerminalError, match="badge, rows"):
        validate_template_payload(template, {"title": "Password hashing"})


def test_render_payload_is_a_detached_snapshot() -> None:
    source = {"title": "hash", "nodes": [{"value": "salt A"}]}
    spec = _spec("flow", source)
    source["nodes"][0]["value"] = "mutated after construction"
    assert spec.props["nodes"][0]["value"] == "salt A"
