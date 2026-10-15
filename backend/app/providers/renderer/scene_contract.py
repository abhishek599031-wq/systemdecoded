"""Project-isolated scene payload construction and validation.

Every renderer consumes an explicit immutable payload.  Missing visual data is
an error; it must never be filled from preview/demo copy or a previous render.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.errors import TerminalError
from app.models.content import ContentProject, Scene, Script
from app.providers.base import SceneRenderSpec


def build_scene_spec(
    project: ContentProject,
    script: Script,
    scene: Scene,
    *,
    width: int,
    height: int,
    runtime_props: dict[str, Any] | None = None,
) -> SceneRenderSpec:
    """Create a render spec only when the full ownership chain is consistent."""
    if script.project_id != project.id:
        raise TerminalError("Render script does not belong to the requested project")
    if scene.script_id != script.id:
        raise TerminalError("Render scene does not belong to the current script")
    if project.current_script_id and project.current_script_id != script.id:
        raise TerminalError("Render script is not the project's current script")
    if not scene.template_id:
        raise TerminalError(f"Scene {scene.id} has no explicit renderer/template")
    if not isinstance(scene.template_props, dict) or not scene.template_props:
        raise TerminalError(f"Scene {scene.id} has no explicit immutable visual payload")

    # Round-trip through JSON to detach the renderer payload from SQLAlchemy's
    # mutable JSON value.  Providers receive a snapshot, not a live model dict.
    props = json.loads(json.dumps(scene.template_props, ensure_ascii=False))
    if runtime_props:
        # Runtime values are derived from measured narration/canonical caption
        # timing. They enrich the immutable creative payload but are never
        # written back into the persisted SceneSpec.
        props.update(json.loads(json.dumps(runtime_props, ensure_ascii=False)))
    return SceneRenderSpec(
        project_id=str(project.id),
        script_id=str(script.id),
        scene_id=str(scene.id),
        scene_number=scene.scene_number,
        template_id=scene.template_id,
        props=props,
        width=width,
        height=height,
    )


def payload_digest(spec: SceneRenderSpec) -> str:
    payload = {
        "project_id": spec.project_id,
        "script_id": spec.script_id,
        "scene_id": spec.scene_id,
        "scene_number": spec.scene_number,
        "template_id": spec.template_id,
        "props": spec.props,
        "width": spec.width,
        "height": spec.height,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_template_payload(template_dir: Path, props: dict[str, Any]) -> None:
    """Validate an HTML template manifest; never fall back to demo values."""
    manifest_path = template_dir / "manifest.json"
    if not manifest_path.exists():
        raise TerminalError(f"Scene template {template_dir.name!r} has no manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TerminalError(
            f"Invalid manifest for scene template {template_dir.name!r}: {exc}"
        ) from exc

    required = manifest.get("required_props")
    if not isinstance(required, list) or not all(isinstance(key, str) for key in required):
        raise TerminalError(f"Template {template_dir.name!r} manifest has invalid required_props")
    missing = [key for key in required if key not in props or props[key] in (None, "", [], {})]
    if missing:
        raise TerminalError(
            f"Template {template_dir.name!r} requires explicit props: {', '.join(missing)}"
        )
