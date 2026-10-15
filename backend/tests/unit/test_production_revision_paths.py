"""Final production artifacts are immutable across script revisions."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.services.production import render_output_path, revision_dir


def test_final_render_path_is_unique_to_script_and_render(monkeypatch, tmp_path) -> None:
    from app.services import production

    monkeypatch.setattr(production, "project_dir", lambda _project_id: tmp_path)
    project = SimpleNamespace(id=uuid.uuid4())
    first_script = SimpleNamespace(id=uuid.uuid4(), version=3)
    revised_script = SimpleNamespace(id=uuid.uuid4(), version=7)
    first_render = SimpleNamespace(id=uuid.uuid4())
    revised_render = SimpleNamespace(id=uuid.uuid4())

    old_path = render_output_path(project, first_script, first_render)
    revised_path = render_output_path(project, revised_script, revised_render)

    assert old_path != revised_path
    assert f"script_{first_script.id}" in str(old_path)
    assert f"script_{revised_script.id}" in str(revised_path)
    assert "_v3_" in old_path.name
    assert "_v7_" in revised_path.name
    assert revision_dir(project.id, first_script.id) != revision_dir(
        project.id, revised_script.id
    )
