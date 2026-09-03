"""The first SystemDecoded Short: "Nobody sent you that code."

Topic: TOTP — why a 6-digit authenticator code expires in 30 seconds, and why
the counter-intuitive answer is that nothing was ever sent.

Why this topic won the shortlist:
  - Universal: essentially every viewer has typed one of these codes.
  - The payoff is genuinely surprising, not merely informative — "your phone and
    the server never talk to each other" reframes something people see weekly.
  - It renders beautifully as two independent columns reaching the same answer,
    which is a visual our templates can own rather than a stock slideshow.
  - Factually airtight: RFC 6238 is a public specification, so nothing has to
    be estimated, dramatised or invented.
  - Zero copyright surface: no logos, no product screenshots, no footage.

Script structure targets Shorts retention: hook in the first ~2 seconds, the
viewer's existing assumption named and broken, the mechanism, then a payoff
that lands on the original question.

This module is a one-off seeder for Phase 2, not a general content generator.
Phase 4 replaces it with the LLM pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import utcnow
from app.core.logging import get_logger
from app.models.channel import Channel
from app.models.content import (
    ContentProject,
    ResearchNote,
    ResearchSource,
    Scene,
    Script,
)
from app.models.enums import ProjectStatus

log = get_logger("seed")

TOPIC = "Why a 6-digit authenticator code expires in 30 seconds"
TOPIC_KEY = "totp-authenticator-code-expiry"

TITLE_CANDIDATES = [
    "Nobody sent you that code",
    "Your phone and the server never talk",
    "Why does the code die in 30 seconds?",
    "The 6-digit code nobody sent you",
]

HOOK_CANDIDATES = [
    "That 6-digit code on your phone? Nobody sent it to you.",
    "Your authenticator app has no internet connection. It still knows the code.",
    "Turn on airplane mode. The code still works. Here's why.",
]

# Sources. Primary specification plus the standard it builds on — both public,
# stable, and precisely what the claims below rest on.
SOURCES = [
    {
        "title": "RFC 6238 — TOTP: Time-Based One-Time Password Algorithm",
        "url": "https://datatracker.ietf.org/doc/html/rfc6238",
        "publisher": "IETF",
        "tier": "PRIMARY",
        "published_at": datetime(2011, 5, 1, tzinfo=UTC),
    },
    {
        "title": "RFC 4226 — HOTP: An HMAC-Based One-Time Password Algorithm",
        "url": "https://datatracker.ietf.org/doc/html/rfc4226",
        "publisher": "IETF",
        "tier": "PRIMARY",
        "published_at": datetime(2005, 12, 1, tzinfo=UTC),
    },
]

# Facts, kept separate from narration. Each is checkable against a source; the
# narration is how we say them, not what we assert.
CLAIMS = [
    ("TOTP generates a one-time password from a shared secret and the current time.", 0),
    ("The default TOTP time step is 30 seconds.", 0),
    ("TOTP is an extension of the HOTP algorithm, replacing its counter with a time value.", 1),
    ("TOTP uses an HMAC, which is a one-way function — the output cannot be reversed.", 1),
    ("The shared secret is exchanged once during enrolment, commonly via a QR code.", 0),
    ("Both the client and server compute the code independently; the code is not transmitted.", 0),
]

# Scenes. Narration is written to be spoken, not read: short clauses, no
# subordinate stacking, and no jargon that a non-developer would stumble on.
SCENES = [
    {
        "n": 1,
        "narration": "That six digit code on your phone? Nobody sent it to you.",
        "on_screen": "Nobody sent you this code.",
        "visual": "Phone showing a 6-digit code with a 30-second countdown ring.",
        "template": "code_reveal",
        "props": {
            "eyebrow": "Authentication",
            "code": "418902",
            "headline": "Nobody *sent* you this code.",
            "seconds_start": 30,
            "seconds_end": 22,
        },
    },
    {
        "n": 2,
        "narration": "Most people assume a server generated it and pushed it to your phone. It didn't.",
        "on_screen": "The server sent it → wrong",
        "visual": "Server → phone arrow, struck through: the assumed path that never happens.",
        "template": "diagram_flow",
        "props": {
            "eyebrow": "The assumption",
            "title": "Most people think it gets *sent*.",
            "nodes": [
                {"label": "Server", "value": "generates a code"},
                {"label": "Your phone", "value": "receives it", "muted": True},
            ],
            "links": [{"note": "over the network", "blocked": True}],
        },
    },
    {
        "n": 3,
        "narration": "When you first scanned that setup code, your phone and the server agreed on one shared secret. That was the only time they spoke.",
        "on_screen": "One shared secret. Once.",
        "visual": "QR enrolment: a single secret handed to both sides, then the channel closes.",
        "template": "diagram_flow",
        "props": {
            "eyebrow": "Setup, once",
            "title": "They agreed on *one secret*.",
            "nodes": [
                {"label": "Setup QR code", "value": "shared secret", "active": True},
                {"label": "Stored on both sides", "value": "your phone + the server"},
            ],
            "links": [{"note": "one time only"}],
        },
    },
    {
        "n": 4,
        "narration": "After that, both sides do the same thing. Take the secret, mix in the current time, and run it through a one way function.",
        "on_screen": "secret + time → one-way function",
        "visual": "Two independent columns computing the same thing with no link between them.",
        "template": "parallel_compute",
        "props": {
            "eyebrow": "How it actually works",
            "title": "Same inputs. Same math.",
            "badge": "They never communicate",
            "left_head": "Your phone",
            "right_head": "The server",
            "rows": [
                {"label": "Shared secret", "value": "K7F2…9A", "shared": True},
                {"label": "Current time", "value": "30-second step", "shared": True},
            ],
            "op": "one-way function",
            "result": "418902",
        },
    },
    {
        "n": 5,
        "narration": "Same secret, same clock, same math. So they both land on the same six digits, without ever talking.",
        "on_screen": "Same answer. No connection.",
        "visual": "Both columns resolve to an identical code; the gap between them stays empty.",
        "template": "parallel_compute",
        "props": {
            "eyebrow": "The result",
            "title": "*Same answer.* Independently.",
            "badge": "Still no connection between them",
            "left_head": "Your phone",
            "right_head": "The server",
            "rows": [
                {"label": "Computed", "value": "418902", "shared": True},
                {"label": "Matches", "value": "yes", "shared": True},
            ],
            "op": "compared locally",
            "result": "418902",
        },
    },
    {
        "n": 6,
        "narration": "The server isn't checking a code it sent you. It's checking whether you can produce the one it just worked out itself. And that's why it expires. The clock moved on.",
        "on_screen": "The clock moved on.",
        "visual": "Countdown hits zero, code rolls over to a new value.",
        "template": "code_reveal",
        "props": {
            "eyebrow": "The payoff",
            "code": "730154",
            "headline": "The clock *moved on*.",
            "sub": "New window. New code.",
            "seconds_start": 8,
            "seconds_end": 0,
        },
    },
]

DESCRIPTION = (
    "Your authenticator app works in airplane mode — because the code was never "
    "sent to you. Your phone and the server both derive it from one shared secret "
    "and the current time, using the same one-way function. Same inputs, same "
    "answer, no connection required. That's also why it expires: the clock moved on."
)

HASHTAGS = ["shorts", "technology", "cybersecurity", "2fa", "howitworks"]


async def seed_first_video(session: AsyncSession, *, force: bool = False) -> ContentProject:
    """Create the first content project with its research, script and scenes."""
    existing = (
        await session.execute(select(ContentProject).where(ContentProject.topic_key == TOPIC_KEY))
    ).scalar_one_or_none()
    if existing is not None and not force:
        log.info("seed.already_exists", project_id=str(existing.id))
        return existing

    channel = (
        await session.execute(select(Channel).order_by(Channel.created_at).limit(1))
    ).scalar_one()

    project = ContentProject(
        channel_id=channel.id,
        topic=TOPIC,
        topic_key=TOPIC_KEY,
        working_title=TITLE_CANDIDATES[0],
        content_pillar="Cybersecurity",
        content_format="How It Works",
        target_viewer="Anyone who has typed a 2FA code — developers and non-developers alike.",
        curiosity_gap="Why does a code nobody sent you stop working after 30 seconds?",
        status=ProjectStatus.IDEA.value,
        target_duration_seconds=34,
        created_by="HUMAN",
    )
    session.add(project)
    await session.flush()

    sources: list[ResearchSource] = []
    for spec in SOURCES:
        source = ResearchSource(
            title=spec["title"],
            url=spec["url"],
            publisher=spec["publisher"],
            source_tier=spec["tier"],
            published_at=spec["published_at"],
            retrieved_at=utcnow(),
        )
        session.add(source)
        sources.append(source)
    await session.flush()

    for claim, source_index in CLAIMS:
        session.add(
            ResearchNote(
                project_id=project.id,
                source_id=sources[source_index].id,
                claim=claim,
                claim_type="FACT",
                confidence="HIGH",
                # Primary specifications, read directly — not second-hand summaries.
                verification_status="CORROBORATED",
                used_in_script=True,
            )
        )

    narration = " ".join(s["narration"] for s in SCENES)
    script = Script(
        project_id=project.id,
        version=1,
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
                transition_in="fade",
            )
        )

    project.current_script_id = script.id
    await session.flush()

    log.info(
        "seed.created",
        project_id=str(project.id),
        scenes=len(SCENES),
        words=script.word_count,
    )
    return project
