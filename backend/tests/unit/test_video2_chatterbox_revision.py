from __future__ import annotations

# ruff: noqa: RUF001 -- test asserts the approved narration verbatim.
from app.services.revise_video2_chatterbox import SCENES


def test_approved_video2_adaptation_is_exactly_five_scenes_and_102_words() -> None:
    narration = " ".join(scene["narration"] for scene in SCENES)

    assert [scene["n"] for scene in SCENES] == [1, 2, 3, 4, 5]
    assert len(narration.split()) == 102
    assert narration.startswith("Good websites don’t store your password")
    assert narration.endswith("database waiting to be stolen.")


def test_video2_adaptation_has_only_motion_canvas_password_templates() -> None:
    serialized = str(SCENES).lower()

    assert all(scene["template"].startswith("motion_canvas_") for scene in SCENES)
    for forbidden in (
        "totp",
        "418902",
        "six-digit",
        "countdown",
        "they never communicate",
        "authenticator",
    ):
        assert forbidden not in serialized
