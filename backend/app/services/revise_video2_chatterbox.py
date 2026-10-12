"""Create the approved Video #2 production adaptation for Original Chatterbox."""

# ruff: noqa: RUF001 -- approved narration intentionally preserves curly apostrophes.

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.errors import ConflictError
from app.models.content import ContentProject, Scene, Script

SCENES = [
    {
        "n": 1,
        "narration": (
            "Good websites don’t store your password as readable text. "
            "They store a verifier."
        ),
        "on_screen": "PASSWORD ≠ STORED TEXT",
        "visual": (
            "Login password flows toward a database; readable text is rejected "
            "and replaced by a verifier."
        ),
        "template": "motion_canvas_password_misconception",
        "props": {"concepts": ["PASSWORD", "DATABASE", "VERIFIER"]},
    },
    {
        "n": 2,
        "narration": (
            "First, your password is combined with a unique random salt, then "
            "passed through a slow password-hashing algorithm."
        ),
        "on_screen": "PASSWORD + UNIQUE SALT → HASH → VERIFIER",
        "visual": "Password and unique salt enter a slow hash and produce a verifier.",
        "template": "motion_canvas_salt_hash",
        "props": {"concepts": ["PASSWORD", "UNIQUE SALT", "SLOW HASH", "VERIFIER"]},
    },
    {
        "n": 3,
        "narration": (
            "Even if two people choose the same password, different salts produce "
            "different stored results. When you log in, the site repeats that process "
            "and compares the new result with the stored verifier."
        ),
        "on_screen": "DIFFERENT SALTS → DIFFERENT RESULTS · COMPARE → MATCH",
        "visual": (
            "Two accounts share an example password but have different salts and "
            "outputs; login repeats the hash and compares for a match."
        ),
        "template": "motion_canvas_compare_accounts",
        "props": {
            "concepts": [
                "ACCOUNT A",
                "ACCOUNT B",
                "DIFFERENT SALTS",
                "DIFFERENT RESULTS",
                "COMPARE",
                "MATCH",
            ]
        },
    },
    {
        "n": 4,
        "narration": (
            "If attackers steal the database, they still can’t reverse the hash—but "
            "they can guess passwords offline. That’s why algorithms like Argon2id "
            "make every guess deliberately expensive."
        ),
        "on_screen": "NOT REVERSIBLE · STILL GUESSABLE · EACH GUESS COSTS",
        "visual": (
            "A breached verifier database feeds repeated offline guesses while a "
            "cost and memory indicator rises; Argon2id appears as an example."
        ),
        "template": "motion_canvas_offline_guessing",
        "props": {
            "concepts": [
                "NOT REVERSIBLE",
                "STILL GUESSABLE",
                "EACH GUESS COSTS",
                "ARGON2ID EXAMPLE",
            ]
        },
    },
    {
        "n": 5,
        "narration": (
            "The password itself never needs to sit in the database waiting to be stolen."
        ),
        "on_screen": "VERIFIER, NOT PLAINTEXT",
        "visual": (
            "Plaintext password disappears while verifier metadata remains in the "
            "database, followed by a SystemDecoded brand hold."
        ),
        "template": "motion_canvas_verifier_takeaway",
        "props": {"concepts": ["VERIFIER", "NOT PLAINTEXT", "SYSTEMDECODED"]},
    },
]


async def revise_video2_for_chatterbox(
    session: AsyncSession, project: ContentProject
) -> Script:
    """Version, never mutate, the approved provider-specific production script."""
    existing = list(
        (
            await session.execute(
                select(Script)
                .where(Script.project_id == project.id)
                .order_by(Script.version.desc())
            )
        ).scalars()
    )
    latest = existing[0] if existing else None
    if latest is not None and latest.version >= 3:
        if latest.narration == " ".join(scene["narration"] for scene in SCENES):
            return latest
        raise ConflictError("Video #2 already has a different script version 3 or later")

    for previous in existing:
        previous.is_current = False

    narration = " ".join(scene["narration"] for scene in SCENES)
    script = Script(
        project_id=project.id,
        version=(latest.version + 1) if latest else 1,
        is_current=True,
        title_candidates=(latest.title_candidates if latest else []),
        selected_title="What Websites Store Instead of Your Password",
        hook_candidates=(latest.hook_candidates if latest else []),
        selected_hook=SCENES[0]["narration"],
        narration=narration,
        description=(latest.description if latest else None),
        hashtags=(latest.hashtags if latest else []),
        authoring_mode="manual",
        word_count=len(narration.split()),
        approved_at=utcnow(),
        review_notes=(
            "Production adaptation for the human-selected Original Chatterbox narrator. "
            "The locked experiment hypothesis and planned variables are unchanged."
        ),
    )
    session.add(script)
    await session.flush()

    for spec in SCENES:
        session.add(
            Scene(
                script_id=script.id,
                project_id=project.id,
                scene_number=spec["n"],
                narration=spec["narration"],
                on_screen_text=spec["on_screen"],
                visual_instruction=spec["visual"],
                template_id=spec["template"],
                template_props=spec["props"],
                transition_in="cut",
            )
        )

    project.current_script_id = script.id
    project.working_title = script.selected_title
    await session.flush()
    return script
