"""Revision 2 of the first SystemDecoded Short.

What changed from v1 and why:

**Hook ambiguity.** v1 opened with "That six-digit code on your phone" — which
most viewers read as an SMS or email OTP. Those genuinely *are* sent, so the
hook was accurate only for a case the viewer had not been told we meant. v2
says "in your authenticator app" inside the first two seconds, which makes the
claim true as heard rather than true only on a technicality.

**Expiry precision.** v1 said the code expires because "the clock moved on",
implying a hard cutoff at exactly 30 seconds. Real TOTP uses time *steps*, and
servers routinely accept an adjacent window for clock drift. v2 says the time
window changes and both sides derive a new code — accurate without turning the
Short into a lecture on clock skew.

**Pacing.** v1 spent three scenes circling one idea (shared secret → same
inputs → same math → same result). v2 folds that into a single derivation
scene, freeing ~4 seconds for a real ending.

**Ending.** v1 simply stopped on its last frame. v2 rotates the code to a new
value, names the mechanism, and holds the resolved frame.

Scene 1 keeps v1's `code_reveal` template; scenes 2 and 3 use `diagram_flow`
(one blocked path, one open); scene 4 is the single derivation; scene 5 is the
resolution. No new templates were needed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.content import ContentProject, Scene, Script

log = get_logger("revise")

TITLE_CANDIDATES = [
    "Nobody sent you that code",
    "Your authenticator app has no internet. It still knows the code.",
    "The 6-digit code nobody sent you",
    "How your authenticator app works offline",
]

HOOK_CANDIDATES = [
    "That six-digit code in your authenticator app? Nobody sent it to you.",
    "Your authenticator app works in airplane mode. Here's why.",
    "Nobody sends you that six-digit code. Your phone works it out.",
]

# Scenes. `reveal_segments` marks which spoken segment lands a payoff and earns
# a longer beat after it; `sfx` places an effect at a fraction of the scene.
SCENES = [
    {
        "n": 1,
        "narration": "That six-digit code in your authenticator app? Nobody sent it to you.",
        "on_screen": "Nobody sent you this code.",
        "visual": "Authenticator code with a live countdown ring; digits land one by one.",
        "template": "code_reveal",
        "props": {
            "eyebrow": "Authenticator app",
            "code": "418902",
            "headline": "Nobody *sent* you this code.",
            "seconds_start": 30,
            "seconds_end": 21,
            # The hook line itself is the payoff of this scene.
            "reveal_segments": [0],
        },
    },
    {
        "n": 2,
        "narration": "There's no message. No network call. Your phone works it out on its own.",
        "on_screen": "No message. No network call.",
        "visual": "A signal sets off from the server, travels a short way, and is cut off.",
        "template": "diagram_flow",
        "props": {
            "eyebrow": "What isn't happening",
            "title": "Nothing is *delivered*.",
            "nodes": [
                {"label": "Server", "value": "has a code"},
                {"label": "Your phone", "value": "never receives it", "muted": True},
            ],
            "links": [{"note": "no message, no call", "blocked": True}],
        },
    },
    {
        "n": 3,
        "narration": (
            "When you set the app up, it and the server agreed on one shared secret. "
            "That was the only time they talked."
        ),
        "on_screen": "One shared secret. Once.",
        "visual": "Setup handshake: the secret is provisioned once and retained by both sides.",
        "template": "diagram_flow",
        "props": {
            "eyebrow": "Setup, once",
            "title": "They agreed on *one secret*.",
            "nodes": [
                {"label": "Setup QR code", "value": "shared secret", "active": True},
                {"label": "Kept by both", "value": "your app + the server"},
            ],
            "links": [{"note": "one time only"}],
        },
    },
    {
        "n": 4,
        "narration": (
            "After that, both sides take that secret, add the current time, "
            "and run the same calculation. Same inputs. Same answer."
        ),
        "on_screen": "secret + time → same answer",
        "visual": "Both columns derive the code step by step, then confirm the match together.",
        "template": "parallel_compute",
        "props": {
            "eyebrow": "How it actually works",
            "title": "Same inputs. Same math.",
            "badge": "They never communicate",
            "left_head": "Your app",
            "right_head": "The server",
            "rows": [
                {"label": "Shared secret", "value": "K7F2…9A", "shared": True},
                {"label": "Current time", "value": "time window", "shared": True},
            ],
            "op": "same calculation",
            "result": "418902",
            "match_at": 0.62,
            "reveal_segments": [1],
            "sfx": {"cue": "confirm", "at": 0.70, "gain": 0.32},
        },
    },
    {
        "n": 5,
        "narration": (
            "Nothing is transmitted. When the time window changes, "
            "both sides work out a new code. That's TOTP. Decoded."
        ),
        "on_screen": "New window. New code.",
        "visual": "The window rolls over, digits flip to a new code, then the term resolves.",
        "template": "code_reveal",
        "props": {
            "eyebrow": "The payoff",
            "code": "418902",
            "code_next": "730154",
            "headline": "New window. *New code.*",
            "rotate_at": 0.42,
            "outro_label": "TOTP",
            "outro_expand": "Time-Based One-Time Password",
            "outro_at": 0.70,
            "seconds_start": 6,
            "seconds_end": 0,
            "reveal_segments": [1],
            "sfx": {"cue": "resolve", "at": 0.58, "gain": 0.30},
        },
    },
]

DESCRIPTION = (
    "Your authenticator app works in airplane mode — because the code was never sent "
    "to you. During setup, your app and the server agree on one shared secret. After "
    "that, each one combines that secret with the current time and runs the same "
    "calculation, so both arrive at the same six digits without exchanging anything. "
    "When the time window changes, both sides work out a new code."
)

HASHTAGS = ["shorts", "technology", "cybersecurity", "2fa", "howitworks"]


async def revise_first_video(session: AsyncSession, project: ContentProject) -> Script:
    """Create script v2 for an existing project, retaining v1.

    Versions are never mutated in place: v1 and its render stay queryable, which
    is what makes "what changed between revisions?" answerable later.
    """
    existing = (
        await session.execute(
            select(Script)
            .where(Script.project_id == project.id)
            .order_by(Script.version.desc())
        )
    ).scalars().all()

    latest = existing[0] if existing else None
    if latest is not None and latest.version >= 2:
        log.info("revise.already_at_v2", script_id=str(latest.id))
        return latest

    for old in existing:
        old.is_current = False

    narration = " ".join(s["narration"] for s in SCENES)
    script = Script(
        project_id=project.id,
        version=(latest.version + 1) if latest else 1,
        is_current=True,
        title_candidates=TITLE_CANDIDATES,
        selected_title=TITLE_CANDIDATES[0],
        hook_candidates=HOOK_CANDIDATES,
        selected_hook=HOOK_CANDIDATES[0],
        narration=narration,
        description=DESCRIPTION,
        hashtags=HASHTAGS,
        authoring_mode="manual",
        word_count=len(narration.split()),
        review_notes=(
            "v2: hook names the authenticator app; expiry reframed as a changing time "
            "window rather than a hard 30-second cutoff; middle tightened from three "
            "scenes to one derivation; ending resolves with a code rotation and the "
            "term named."
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
    project.working_title = TITLE_CANDIDATES[0]
    await session.flush()

    log.info(
        "revise.created",
        project_id=str(project.id),
        version=script.version,
        scenes=len(SCENES),
        words=script.word_count,
    )
    return script
