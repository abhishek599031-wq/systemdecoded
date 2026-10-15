"""Apply the human-reviewed Video #3 copy as a new immutable script version."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.db.session import session_scope
from app.models.content import ContentProject, ResearchNote, ResearchSource, Script
from app.models.planner import ContentProposal, PlannerRun
from app.schemas.planner import SceneSpecSet, ScriptDraft
from app.services.planner import validate_script_grounding, word_count
from app.services.script_revisions import create_review_revision

PROJECT_ID = uuid.UUID("01a133b5-5a42-789f-a301-f26c1e869149")
REVIEW_MARKER = "Video #3 beginner narration approved in human content review"
REQUIRED_RESEARCH_URLS = {
    "https://www.rfc-editor.org/rfc/rfc4033.html",
    "https://www.rfc-editor.org/rfc/rfc4035.html",
}

SCENES = [
    "What if your browser got a fake answer to one simple question: where is this website?",
    (
        "Attackers can poison a DNS cache, replacing a real server address with a false one "
        "that points somewhere else."
    ),
    (
        "DNSSEC makes signed DNS answers verifiable. A resolver checks digital signatures "
        "and follows a chain of trusted keys."
    ),
    (
        "When a signed answer fails validation, the resolver treats it as bogus and normally "
        "returns an error instead of trusting it."
    ),
    (
        "But DNSSEC doesn't encrypt your lookups or hide the websites you request. "
        "It checks authenticity, not privacy."
    ),
]


def _draft(*, title: str, description: str) -> ScriptDraft:
    return ScriptDraft.model_validate(
        {
            "title": title,
            "description": description,
            "scenes": [
                {"scene_number": number, "narration": narration}
                for number, narration in enumerate(SCENES, start=1)
            ],
        }
    )


def _specs() -> SceneSpecSet:
    payloads = [
        {
            "visual_type": "NETWORK_FLOW",
            "visual_purpose": (
                "Open on a browser asking DNS for a destination, then reveal that the returned "
                "answer could be real or fake."
            ),
            "labels": ["WHERE IS THIS WEBSITE?", "DNS ANSWER", "REAL OR FAKE?"],
            "nodes": [
                {"id": "browser", "label": "BROWSER", "emphasis": "normal"},
                {"id": "dns", "label": "WHERE IS THIS WEBSITE?", "emphasis": "accent"},
                {"id": "answer", "label": "REAL OR FAKE?", "emphasis": "danger"},
            ],
            "connectors": [
                {"from": "browser", "to": "dns", "label": "asks DNS"},
                {"from": "dns", "to": "answer", "label": "answer"},
            ],
            "timing_anchors": [
                {
                    "id": "question",
                    "phrase": "where is this website",
                    "intent": "Reveal the destination question and split the answer state.",
                }
            ],
            "animation_intent": (
                "Send a question pulse from browser to DNS, then flip the answer card between "
                "green real and red fake states."
            ),
        },
        {
            "visual_type": "NETWORK_FLOW",
            "visual_purpose": (
                "Show an attacker replacing the legitimate cached server address with a false "
                "destination."
            ),
            "labels": ["REAL ADDRESS", "POISONED CACHE", "FALSE ADDRESS"],
            "nodes": [
                {"id": "real", "label": "REAL ADDRESS", "emphasis": "success"},
                {"id": "cache", "label": "DNS CACHE", "emphasis": "normal"},
                {"id": "attacker", "label": "ATTACKER", "emphasis": "danger"},
                {"id": "false", "label": "FALSE ADDRESS", "emphasis": "danger"},
            ],
            "connectors": [
                {"from": "real", "to": "cache", "label": "stored"},
                {"from": "attacker", "to": "cache", "label": "poison"},
                {"from": "cache", "to": "false", "label": "redirect"},
            ],
            "timing_anchors": [
                {
                    "id": "poison",
                    "phrase": "poison a DNS cache",
                    "intent": "Replace the green cached address with the red false address.",
                }
            ],
            "animation_intent": (
                "Animate the real address into cache, then let a red attacker packet overwrite "
                "it and redirect the outgoing path."
            ),
        },
        {
            "visual_type": "FLOW",
            "visual_purpose": (
                "Trace signature verification from a signed answer through trusted keys to a "
                "verified result."
            ),
            "labels": ["SIGNED ANSWER", "CHECK SIGNATURE", "TRUSTED KEY CHAIN", "VERIFIED"],
            "nodes": [
                {"id": "signed", "label": "SIGNED ANSWER", "emphasis": "normal"},
                {"id": "signature", "label": "CHECK SIGNATURE", "emphasis": "accent"},
                {"id": "keys", "label": "TRUSTED KEY CHAIN", "emphasis": "accent"},
                {"id": "verified", "label": "VERIFIED", "emphasis": "success"},
            ],
            "connectors": [
                {"from": "signed", "to": "signature", "label": "verify"},
                {"from": "keys", "to": "signature", "label": "trust"},
                {"from": "signature", "to": "verified", "label": "valid"},
            ],
            "timing_anchors": [
                {
                    "id": "signatures",
                    "phrase": "checks digital signatures",
                    "intent": "Light the signature check as the answer enters it.",
                },
                {
                    "id": "chain",
                    "phrase": "chain of trusted keys",
                    "intent": "Trace each trust link before confirming the answer.",
                },
            ],
            "animation_intent": (
                "Draw the trusted-key chain link by link, route it into the signature check, "
                "and finish with a green verified stamp."
            ),
        },
        {
            "visual_type": "PROCESS",
            "visual_purpose": (
                "Show a signed answer failing validation, becoming bogus, and ending in an "
                "error rather than a trusted response."
            ),
            "labels": ["VALIDATION FAILS", "BOGUS", "RETURN ERROR"],
            "nodes": [
                {"id": "answer", "label": "SIGNED ANSWER", "emphasis": "normal"},
                {"id": "validation", "label": "VALIDATION FAILS", "emphasis": "danger"},
                {"id": "bogus", "label": "BOGUS", "emphasis": "danger"},
                {"id": "error", "label": "RETURN ERROR", "emphasis": "success"},
            ],
            "connectors": [
                {"from": "answer", "to": "validation", "label": "check"},
                {"from": "validation", "to": "bogus", "label": "fails"},
                {"from": "bogus", "to": "error", "label": "reject"},
            ],
            "timing_anchors": [
                {
                    "id": "fails",
                    "phrase": "fails validation",
                    "intent": "Stop the answer at a red validation barrier.",
                },
                {
                    "id": "error",
                    "phrase": "normally returns an error",
                    "intent": "Route the blocked answer to an error card.",
                },
            ],
            "animation_intent": (
                "Move the answer into validation, break the path on failure, mark it bogus, "
                "and send only an error toward the browser."
            ),
        },
        {
            "visual_type": "COMPARISON",
            "visual_purpose": (
                "Contrast what DNSSEC verifies with the privacy protections it does not provide."
            ),
            "labels": ["DNSSEC CHECKS", "AUTHENTICITY", "NOT PRIVACY"],
            "nodes": [
                {"id": "dnssec", "label": "DNSSEC CHECKS", "emphasis": "accent"},
                {"id": "authenticity", "label": "AUTHENTICITY", "emphasis": "success"},
                {"id": "privacy", "label": "NOT PRIVACY", "emphasis": "danger"},
            ],
            "connectors": [
                {"from": "dnssec", "to": "authenticity", "label": "verifies"},
                {"from": "dnssec", "to": "privacy", "label": "doesn't hide"},
            ],
            "timing_anchors": [
                {
                    "id": "no_encryption",
                    "phrase": "doesn't encrypt your lookups",
                    "intent": "Cross out a privacy shield without changing the verified state.",
                },
                {
                    "id": "contrast",
                    "phrase": "authenticity, not privacy",
                    "intent": "Land on the final two-column contrast.",
                },
            ],
            "animation_intent": (
                "Illuminate authenticity in green while a separate privacy shield remains "
                "crossed out, ending on the concise contrast."
            ),
        },
    ]
    return SceneSpecSet.model_validate(
        {
            "scenes": [
                {"scene_number": number, **payload}
                for number, payload in enumerate(payloads, start=1)
            ]
        }
    )


async def _verify_persisted_research() -> None:
    async with session_scope() as session:
        rows = list(
            (
                await session.execute(
                    select(ResearchSource.url, ResearchNote.claim)
                    .join(ResearchNote, ResearchNote.source_id == ResearchSource.id)
                    .where(
                        ResearchNote.project_id == PROJECT_ID,
                        ResearchNote.verification_status == "SOURCE_RETRIEVED",
                        ResearchNote.used_in_script.is_(True),
                        ResearchSource.url.in_(REQUIRED_RESEARCH_URLS),
                    )
                )
            ).all()
        )
        found_urls = {url for url, _claim in rows}
        if found_urls != REQUIRED_RESEARCH_URLS:
            raise RuntimeError("The persisted RFC 4033/4035 evidence set is incomplete.")
        claims = " ".join(claim.lower() for _url, claim in rows)
        required_claim_fragments = (
            "data-origin authentication",
            "does not provide confidentiality",
            "trust anchor",
            "bogus",
            "returns an error",
        )
        if any(fragment not in claims for fragment in required_claim_fragments):
            raise RuntimeError("The persisted RFC evidence does not cover the revised narration.")


async def main() -> None:
    await _verify_persisted_research()
    async with session_scope() as session:
        project = await session.get(ContentProject, PROJECT_ID)
        if project is None:
            raise RuntimeError(f"Project {PROJECT_ID} not found")
        current = await session.get(Script, project.current_script_id)
        if current is None:
            raise RuntimeError("The project has no current script")
        if current.review_notes == REVIEW_MARKER:
            print(
                f"already applied: project={project.id} script={current.id} "
                f"version={current.version} words={current.word_count} status={project.status}"
            )
            return

        proposal = (
            await session.execute(
                select(ContentProposal).where(ContentProposal.project_id == project.id)
            )
        ).scalar_one()
        run = await session.get(PlannerRun, proposal.planner_run_id)
        if run is None:
            raise RuntimeError("The source planner run is missing")

        draft = _draft(
            title=current.selected_title or project.working_title,
            description=current.description,
        )
        specs = _specs()
        validate_script_grounding(proposal, draft)
        exact_words = word_count(" ".join(scene.narration for scene in draft.scenes))
        if exact_words != 91:
            raise RuntimeError(f"Unexpected revised narration count: {exact_words}")

        script = await create_review_revision(
            session,
            project=project,
            draft=draft,
            scene_specs=specs,
            review_notes=REVIEW_MARKER,
            words_per_minute=float(run.context_snapshot["observed_wpm"]),
        )
        print(
            f"applied: project={project.id} script={script.id} version={script.version} "
            f"words={script.word_count} status={project.status}"
        )


if __name__ == "__main__":
    asyncio.run(main())
