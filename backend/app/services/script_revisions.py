"""Version-preserving corrections for scripts still at the content-review gate."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ValidationError
from app.models.content import ContentProject, Scene, Script
from app.models.enums import ProjectStatus
from app.schemas.planner import SceneSpecSet, ScriptDraft
from app.services.planner import word_count


async def create_review_revision(
    session: AsyncSession,
    *,
    project: ContentProject,
    draft: ScriptDraft,
    scene_specs: SceneSpecSet,
    review_notes: str,
    words_per_minute: float,
) -> Script:
    """Create a new current script without erasing the generated source version."""
    if project.status != ProjectStatus.SCRIPT_REVIEW.value:
        raise ConflictError("Script revisions are only allowed during SCRIPT_REVIEW.")
    if len(draft.scenes) != len(scene_specs.scenes):
        raise ValidationError("Narration and SceneSpec counts must match.")

    scripts = list(
        (
            await session.execute(
                select(Script)
                .where(Script.project_id == project.id)
                .order_by(Script.version.desc())
            )
        ).scalars()
    )
    if not scripts:
        raise ConflictError("The project has no source script to revise.")
    previous = scripts[0]
    for row in scripts:
        row.is_current = False

    narration = " ".join(scene.narration for scene in draft.scenes)
    words = word_count(narration)
    script = Script(
        project_id=project.id,
        version=previous.version + 1,
        is_current=True,
        title_candidates=[draft.title],
        selected_title=draft.title,
        hook_candidates=list(previous.hook_candidates or []),
        selected_hook=previous.selected_hook,
        narration=narration,
        description=draft.description,
        hashtags=list(previous.hashtags or []),
        authoring_mode="system_review",
        word_count=words,
        estimated_duration_seconds=Decimal(str(round(words / words_per_minute * 60, 2))),
        review_notes=review_notes,
    )
    session.add(script)
    await session.flush()

    for narration_scene, spec in zip(draft.scenes, scene_specs.scenes, strict=True):
        session.add(
            Scene(
                script_id=script.id,
                project_id=project.id,
                scene_number=narration_scene.scene_number,
                narration=narration_scene.narration,
                on_screen_text=" · ".join(spec.labels),
                visual_instruction=spec.visual_purpose,
                template_id="generic_scene_spec",
                template_props={
                    "schema_version": 1,
                    "visual_type": spec.visual_type.value,
                    "labels": spec.labels,
                    "nodes": [node.model_dump() for node in spec.nodes],
                    "connectors": [
                        edge.model_dump(by_alias=True, mode="json") for edge in spec.connectors
                    ],
                    "timing_anchors": {anchor.id: anchor.phrase for anchor in spec.timing_anchors},
                    "animation_intent": spec.animation_intent,
                    "end_card": spec.end_card,
                },
            )
        )
    project.current_script_id = script.id
    project.working_title = draft.title
    await session.flush()
    return script
