"""Persist the human-requested Video #3 quality revision for fresh approval."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.core.state_machine import transition
from app.db.session import session_scope
from app.models.content import ContentProject, ResearchNote, ResearchSource, Script
from app.models.enums import ProjectStatus
from app.models.planner import ContentProposal
from app.schemas.planner import SceneSpecSet, ScriptDraft
from app.services.planner import validate_script_grounding, word_count
from app.services.script_revisions import create_review_revision

PROJECT_ID = uuid.UUID("01a133b5-5a42-789f-a301-f26c1e869149")
REVIEW_MARKER = "Phase 6A final directed-edge and connector-label quality revision"
RFC_URLS = {
    "https://www.rfc-editor.org/rfc/rfc4033.html",
    "https://www.rfc-editor.org/rfc/rfc4035.html",
}

NARRATION = [
    (
        "When you open a website, DNS helps your browser find its address. "
        "But what if that answer is fake?"
    ),
    (
        "An attacker can trick a DNS resolver into remembering the wrong address, "
        "potentially sending visitors to a fake website."
    ),
    (
        "DNSSEC lets domain owners digitally sign DNS information. A validating "
        "resolver uses trusted keys to check those signatures."
    ),
    (
        "If a signed answer fails that check, the resolver normally returns an error "
        "instead of accepting the forged information."
    ),
    (
        "But DNSSEC is not privacy protection. It verifies signed DNS information; "
        "it does not encrypt lookups or hide requested websites."
    ),
]


def draft(title: str, description: str) -> ScriptDraft:
    return ScriptDraft.model_validate(
        {
            "title": title,
            "description": description,
            "scenes": [
                {"scene_number": number, "narration": narration}
                for number, narration in enumerate(NARRATION, 1)
            ],
        }
    )


def specs() -> SceneSpecSet:
    rows = [
        {
            "visual_type": "NETWORK_FLOW",
            "visual_purpose": (
                "Teach the basic name-to-address DNS flow before introducing a "
                "visibly questionable answer."
            ),
            "labels": ["HOW DNS FINDS A WEBSITE"],
            "nodes": [
                {"id": "name", "label": "WEBSITE NAME", "emphasis": "normal"},
                {"id": "dns", "label": "DNS", "emphasis": "accent"},
                {"id": "address", "label": "SERVER ADDRESS", "emphasis": "success"},
                {"id": "question", "label": "WHAT IF IT'S FAKE?", "emphasis": "danger"},
            ],
            "connectors": [
                {"from": "name", "to": "dns", "label": "look up", "kind": "normal"},
                {"from": "dns", "to": "address", "label": "answer", "kind": "normal"},
                {"from": "address", "to": "question", "label": "trust?", "kind": "normal"},
            ],
            "timing_anchors": [
                {
                    "id": "dns",
                    "phrase": "DNS helps your browser",
                    "intent": "Reveal DNS between the name and address.",
                },
                {
                    "id": "fake",
                    "phrase": "what if that answer is fake",
                    "intent": "Transform the trusted address into a red question.",
                },
            ],
            "animation_intent": (
                "Move the website name through DNS into a server address, then "
                "challenge that address as potentially fake."
            ),
        },
        {
            "visual_type": "PROCESS",
            "visual_purpose": (
                "Show a cache changing from the legitimate destination to an "
                "attacker-supplied destination and redirecting the request."
            ),
            "labels": ["CACHE POISONING"],
            "nodes": [
                {"id": "real", "label": "REAL ADDRESS", "emphasis": "success"},
                {"id": "cache", "label": "DNS CACHE", "emphasis": "accent"},
                {"id": "attacker", "label": "ATTACKER", "emphasis": "danger"},
                {"id": "fake", "label": "FORGED ANSWER", "emphasis": "danger"},
                {"id": "destination", "label": "FAKE WEBSITE", "emphasis": "danger"},
            ],
            "connectors": [
                {
                    "from": "real",
                    "to": "cache",
                    "label": "legitimate",
                    "kind": "normal",
                },
                {
                    "from": "attacker",
                    "to": "fake",
                    "label": "injects",
                    "kind": "injection",
                },
                {
                    "from": "fake",
                    "to": "cache",
                    "label": "replaces",
                    "kind": "replacement",
                },
                {
                    "from": "cache",
                    "to": "destination",
                    "label": "later lookup",
                    "kind": "normal",
                },
            ],
            "timing_anchors": [
                {
                    "id": "replace",
                    "phrase": "remembering the wrong address",
                    "intent": "Replace the green address with the red address.",
                },
                {
                    "id": "redirect",
                    "phrase": "fake website",
                    "intent": "Route the request only to the malicious destination.",
                },
            ],
            "animation_intent": (
                "Store the legitimate address, animate the attacker introducing a forged "
                "answer into the resolver cache, then trace a later lookup to the fake website."
            ),
        },
        {
            "visual_type": "FLOW",
            "visual_purpose": (
                "Explain signature verification with plain-language stages and a "
                "visible successful result."
            ),
            "labels": ["CHECKING A SIGNED ANSWER"],
            "nodes": [
                {"id": "signed", "label": "SIGNED DNS INFO", "emphasis": "normal"},
                {"id": "verify", "label": "VERIFY SIGNATURE", "emphasis": "accent"},
                {"id": "keys", "label": "TRUSTED KEYS", "emphasis": "accent"},
                {"id": "valid", "label": "VALID", "emphasis": "success"},
            ],
            "connectors": [
                {"from": "signed", "to": "verify", "label": "check", "kind": "verification"},
                {"from": "verify", "to": "keys", "label": "trust", "kind": "verification"},
                {"from": "keys", "to": "valid", "label": "matches", "kind": "verification"},
            ],
            "timing_anchors": [
                {
                    "id": "signed",
                    "phrase": "digitally sign DNS information",
                    "intent": "Reveal the signature badge on the DNS answer.",
                },
                {
                    "id": "verify",
                    "phrase": "trusted keys to check those signatures",
                    "intent": "Trace verification to a green valid state.",
                },
            ],
            "animation_intent": (
                "Trace the signed information through signature verification and "
                "trusted keys, ending in a prominent valid state."
            ),
        },
        {
            "visual_type": "PROCESS",
            "visual_purpose": (
                "Make it unmistakable that failed signed data is rejected and never "
                "reaches the browser as accepted information."
            ),
            "labels": ["FORGED DATA IS BLOCKED"],
            "nodes": [
                {"id": "forged", "label": "FORGED SIGNED ANSWER", "emphasis": "danger"},
                {"id": "check", "label": "SIGNATURE CHECK", "emphasis": "accent"},
                {"id": "failed", "label": "FAILED", "emphasis": "danger"},
                {"id": "rejected", "label": "REJECTED", "emphasis": "danger"},
            ],
            "connectors": [
                {"from": "forged", "to": "check", "label": "validate", "kind": "verification"},
                {"from": "check", "to": "failed", "label": "no match", "kind": "rejection"},
                {"from": "failed", "to": "rejected", "label": "block", "kind": "rejection"},
            ],
            "timing_anchors": [
                {
                    "id": "failed",
                    "phrase": "fails that check",
                    "intent": "Break the path at the signature check.",
                },
                {
                    "id": "rejected",
                    "phrase": "instead of accepting",
                    "intent": "Land on rejected without a browser-success branch.",
                },
            ],
            "animation_intent": (
                "Drive the forged answer into the signature check, turn the route "
                "red on failure, and terminate it at rejected."
            ),
        },
        {
            "visual_type": "COMPARISON",
            "visual_purpose": (
                "Contrast authentication with privacy, then resolve into a dedicated "
                "SystemDecoded end card."
            ),
            "labels": ["WHAT DNSSEC DOES"],
            "nodes": [
                {"id": "dnssec", "label": "DNSSEC", "emphasis": "accent"},
                {"id": "auth", "label": "AUTHENTICITY", "emphasis": "success"},
                {"id": "privacy", "label": "LOOKUP PRIVACY", "emphasis": "danger"},
            ],
            "connectors": [
                {"from": "dnssec", "to": "auth", "label": "does", "kind": "verification"},
                {"from": "dnssec", "to": "privacy", "label": "doesn't", "kind": "normal"},
            ],
            "timing_anchors": [
                {
                    "id": "auth",
                    "phrase": "verifies signed DNS information",
                    "intent": "Highlight authenticity in green.",
                },
                {
                    "id": "privacy",
                    "phrase": "does not encrypt lookups",
                    "intent": "Cross out privacy in red before the end card.",
                },
            ],
            "animation_intent": (
                "Split DNSSEC into a green authenticity outcome and a red privacy "
                "limitation, then transition to the brand ending."
            ),
            "end_card": True,
        },
    ]
    return SceneSpecSet.model_validate(
        {"scenes": [{"scene_number": number, **row} for number, row in enumerate(rows, 1)]}
    )


async def verify_research() -> None:
    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(ResearchSource.url, ResearchNote.claim)
                    .join(ResearchNote, ResearchNote.source_id == ResearchSource.id)
                    .where(
                        ResearchNote.project_id == PROJECT_ID,
                        ResearchNote.verification_status == "SOURCE_RETRIEVED",
                        ResearchSource.url.in_(RFC_URLS),
                    )
                )
            ).all()
        )
    if {url for url, _ in rows} != RFC_URLS:
        raise RuntimeError("Persisted RFC 4033/4035 evidence is incomplete")
    claims = " ".join(claim.lower() for _, claim in rows)
    for phrase in (
        "data-origin authentication",
        "does not provide confidentiality",
        "trust anchor",
        "returns an error",
    ):
        if phrase not in claims:
            raise RuntimeError(f"Persisted research does not support required concept: {phrase}")


async def main() -> None:
    await verify_research()
    async with session_scope() as session:
        project = await session.get(ContentProject, PROJECT_ID, with_for_update=True)
        if project is None:
            raise RuntimeError("Video #3 project does not exist")
        current = await session.get(Script, project.current_script_id)
        if current and current.review_notes == REVIEW_MARKER:
            print(
                f"already applied script={current.id} version={current.version} "
                f"words={current.word_count} status={project.status}"
            )
            return
        if project.status == ProjectStatus.VIDEO_REVIEW.value:
            await transition(
                session,
                project,
                ProjectStatus.NEEDS_REVISION,
                actor="HUMAN",
                reason=(
                    "Video #3 visual and beginner-readability review: not approved for publication"
                ),
            )
        if project.status == ProjectStatus.NEEDS_REVISION.value:
            await transition(
                session,
                project,
                ProjectStatus.SCRIPT_REVIEW,
                reason="Phase 6A revised narration and SceneSpecs ready for fresh human approval",
            )
        proposal = (
            await session.execute(
                select(ContentProposal).where(ContentProposal.project_id == project.id)
            )
        ).scalar_one()
        proposed = draft(current.selected_title or project.working_title, current.description)
        scene_specs = specs()
        validate_script_grounding(proposal, proposed)
        exact = word_count(" ".join(scene.narration for scene in proposed.scenes))
        if exact != 95:
            raise RuntimeError(f"Expected 95 words, got {exact}")
        script = await create_review_revision(
            session,
            project=project,
            draft=proposed,
            scene_specs=scene_specs,
            review_notes=REVIEW_MARKER,
            words_per_minute=185.0,
        )
        print(
            f"applied script={script.id} version={script.version} "
            f"words={script.word_count} status={project.status}"
        )


if __name__ == "__main__":
    asyncio.run(main())
