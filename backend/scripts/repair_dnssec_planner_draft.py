"""Repair the reviewed DNSSEC planner draft without erasing generated history."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.core.clock import utcnow
from app.db.session import session_scope
from app.models.content import ContentProject, ResearchNote, ResearchSource, Script
from app.models.experiments import ContentExperiment
from app.models.planner import ContentProposal, PlannerRun
from app.schemas.planner import ResearchPlan, SceneSpecSet, ScriptDraft
from app.services.planner import _grounded_sources, topic_key, validate_script_grounding
from app.services.script_revisions import create_review_revision

RUN_ID = uuid.UUID("01a1307f-0754-7245-86ce-a7f877ea74ae")
REPAIR_MARKER = "DNSSEC factual review: unsigned-insecure and absolute claims corrected"
REVISED_TOPIC = "How DNSSEC Protects Against Cache Poisoning"
REVISED_HYPOTHESIS = (
    "A visual chain-of-trust walkthrough may help beginners distinguish DNSSEC "
    "authentication from DNS encryption and understand how bogus answers are rejected."
)
REVISED_HOOK = (
    "A forged DNS answer can redirect you—unless a validating resolver can prove it is fake."
)

SCENES = [
    (
        "An attacker injects a forged DNS answer, hoping a resolver will cache the "
        "wrong address and redirect later requests."
    ),
    (
        "DNSSEC lets a validating resolver authenticate signed DNS data and confirm "
        "it has not changed since the zone signed it."
    ),
    (
        "The resolver follows public keys and delegation records through a chain of "
        "trust anchored in a key it already trusts."
    ),
    (
        "If signature validation marks an answer bogus, the resolver returns an error "
        "instead of caching and serving that forged data."
    ),
    (
        "DNSSEC protects authenticity and integrity, not confidentiality: it does not "
        "encrypt DNS lookups or hide which names users request."
    ),
]


def _draft() -> ScriptDraft:
    return ScriptDraft.model_validate(
        {
            "title": "How DNSSEC Blocks Forged DNS Answers",
            "description": (
                "A beginner explanation of how validating resolvers authenticate signed DNS "
                "data and reject bogus answers without implying that DNSSEC encrypts lookups."
            ),
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
            "visual_purpose": "Show a forged answer attempting to poison a resolver cache.",
            "labels": ["FORGED ANSWER", "RESOLVER", "WRONG ADDRESS"],
            "nodes": [
                {"id": "forged", "label": "FORGED ANSWER", "emphasis": "danger"},
                {"id": "resolver", "label": "RESOLVER", "emphasis": "normal"},
                {"id": "wrong", "label": "WRONG ADDRESS", "emphasis": "danger"},
            ],
            "connectors": [
                {"from": "forged", "to": "resolver", "label": "inject"},
                {"from": "resolver", "to": "wrong", "label": "cache?"},
            ],
            "timing_anchors": [
                {"id": "attack", "phrase": "injects a forged DNS answer", "intent": "reveal"}
            ],
            "animation_intent": "A red forged answer races toward the resolver cache.",
        },
        {
            "visual_type": "PROCESS",
            "visual_purpose": "Show signed DNS data being authenticated and checked for change.",
            "labels": ["SIGNED DNS DATA", "VERIFY SIGNATURE", "AUTHENTIC"],
            "nodes": [
                {"id": "signed", "label": "SIGNED DNS DATA", "emphasis": "normal"},
                {"id": "verify", "label": "VERIFY SIGNATURE", "emphasis": "accent"},
                {"id": "authentic", "label": "AUTHENTIC", "emphasis": "success"},
            ],
            "connectors": [
                {"from": "signed", "to": "verify", "label": "check"},
                {"from": "verify", "to": "authentic", "label": "valid"},
            ],
            "timing_anchors": [
                {"id": "verify", "phrase": "authenticate signed DNS data", "intent": "highlight"}
            ],
            "animation_intent": "The signature check resolves from amber to green.",
        },
        {
            "visual_type": "FLOW",
            "visual_purpose": "Trace the DNSSEC chain from trust anchor to signed answer.",
            "labels": ["TRUST ANCHOR", "DS", "DNSKEY", "RRSIG"],
            "nodes": [
                {"id": "anchor", "label": "TRUST ANCHOR", "emphasis": "accent"},
                {"id": "ds", "label": "DS", "emphasis": "normal"},
                {"id": "dnskey", "label": "DNSKEY", "emphasis": "normal"},
                {"id": "rrsig", "label": "RRSIG", "emphasis": "success"},
            ],
            "connectors": [
                {"from": "anchor", "to": "ds", "label": "trust"},
                {"from": "ds", "to": "dnskey", "label": "delegation"},
                {"from": "dnskey", "to": "rrsig", "label": "verify"},
            ],
            "timing_anchors": [
                {"id": "chain", "phrase": "through a chain of trust", "intent": "trace"}
            ],
            "animation_intent": "Trace the chain one verified link at a time.",
        },
        {
            "visual_type": "FLOW",
            "visual_purpose": "Show a bogus signed answer failing validation before caching.",
            "labels": ["BOGUS ANSWER", "VALIDATION FAILS", "ERROR — NOT CACHED"],
            "nodes": [
                {"id": "bogus", "label": "BOGUS ANSWER", "emphasis": "danger"},
                {"id": "fails", "label": "VALIDATION FAILS", "emphasis": "danger"},
                {"id": "error", "label": "ERROR — NOT CACHED", "emphasis": "success"},
            ],
            "connectors": [
                {"from": "bogus", "to": "fails", "label": "check"},
                {"from": "fails", "to": "error", "label": "reject"},
            ],
            "timing_anchors": [
                {"id": "bogus", "phrase": "marks an answer bogus", "intent": "reject"}
            ],
            "animation_intent": "The bogus answer stops at validation and never reaches cache.",
        },
        {
            "visual_type": "COMPARISON",
            "visual_purpose": "Contrast DNSSEC authentication with encrypted DNS transport.",
            "labels": ["DNSSEC", "AUTHENTICITY + INTEGRITY", "NOT CONFIDENTIALITY"],
            "nodes": [
                {"id": "dnssec", "label": "DNSSEC", "emphasis": "accent"},
                {"id": "auth", "label": "AUTHENTICITY + INTEGRITY", "emphasis": "success"},
                {"id": "private", "label": "NOT CONFIDENTIALITY", "emphasis": "danger"},
            ],
            "connectors": [
                {"from": "dnssec", "to": "auth", "label": "provides"},
                {"from": "dnssec", "to": "private", "label": "not"},
            ],
            "timing_anchors": [
                {
                    "id": "not_encrypt",
                    "phrase": "does not encrypt DNS lookups",
                    "intent": "contrast",
                }
            ],
            "animation_intent": (
                "Authentication lights up while confidentiality remains crossed out."
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


async def main() -> None:
    async with session_scope() as session:
        run = await session.get(PlannerRun, RUN_ID)
        if run is None:
            raise RuntimeError(f"Planner run {RUN_ID} not found")
        proposal = (
            await session.execute(
                select(ContentProposal).where(
                    ContentProposal.planner_run_id == RUN_ID,
                    ContentProposal.selected.is_(True),
                )
            )
        ).scalar_one()
        if proposal.project_id is None:
            raise RuntimeError("Selected proposal has no project")
        project = await session.get(ContentProject, proposal.project_id)
        if project is None:
            raise RuntimeError("Selected project not found")
        project.topic = REVISED_TOPIC
        project.topic_key = topic_key(REVISED_TOPIC)
        if "99%" in proposal.hook:
            proposal.scoring = {
                **proposal.scoring,
                "original_ai_hook": proposal.hook,
                "hook_revision_reason": "Unsupported 99% statistic removed during research review.",
            }
            proposal.hook = REVISED_HOOK
            flag_modified(proposal, "scoring")
        experiment = (
            await session.execute(
                select(ContentExperiment).where(ContentExperiment.project_id == project.id)
            )
        ).scalar_one()
        experiment.planned_topic = REVISED_TOPIC
        experiment.hypothesis = REVISED_HYPOTHESIS
        current = await session.get(Script, project.current_script_id)
        if current is not None and current.review_notes == REPAIR_MARKER:
            current.hook_candidates = [REVISED_HOOK]
            current.selected_hook = REVISED_HOOK
            print(f"already repaired: script={current.id} version={current.version}")
            return

        draft, specs = _draft(), _specs()
        validate_script_grounding(proposal, draft)

        old_notes = list(
            (
                await session.execute(
                    select(ResearchNote).where(ResearchNote.project_id == project.id)
                )
            ).scalars()
        )
        for note in old_notes:
            note.verification_status = "SUPERSEDED"
            note.used_in_script = False

        empty_plan = ResearchPlan.model_construct(sources=[])
        for source in _grounded_sources(proposal, empty_plan):
            saved = ResearchSource(
                url=str(source.url),
                title=source.title,
                publisher=source.publisher,
                source_tier="PRIMARY",
                retrieved_at=utcnow(),
                notes=source.uncertainty,
            )
            session.add(saved)
            await session.flush()
            for claim in source.claims:
                session.add(
                    ResearchNote(
                        project_id=project.id,
                        source_id=saved.id,
                        claim=claim,
                        confidence="HIGH",
                        verification_status="SOURCE_RETRIEVED",
                        used_in_script=True,
                    )
                )

        script = await create_review_revision(
            session,
            project=project,
            draft=draft,
            scene_specs=specs,
            review_notes=REPAIR_MARKER,
            words_per_minute=float(run.context_snapshot["observed_wpm"]),
        )
        script.hook_candidates = [REVISED_HOOK]
        script.selected_hook = REVISED_HOOK
        print(f"repaired: project={project.id} script={script.id} version={script.version}")


if __name__ == "__main__":
    asyncio.run(main())
