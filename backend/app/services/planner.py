"""Phase 6A autonomous draft preparation.

The planner is deliberately a finite, review-first workflow.  It may create a
DRAFT project only after deterministic context, duplicate checks, source URL
verification, Pydantic validation, and script/SceneSpec validation all pass.
It neither calls media providers nor changes publishing/OAuth state.
"""
# ruff: noqa: E501

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.clock import utcnow
from app.core.errors import ConflictError, TerminalError
from app.core.state_machine import transition
from app.models.analytics import YouTubeVideoWindowMetric
from app.models.channel import Channel
from app.models.content import ContentProject, ResearchNote, ResearchSource, Scene, Script
from app.models.content_intelligence import ContentFeatureProfile
from app.models.enums import AnalyticsWindowStatus, ExperimentStatus, ProjectStatus
from app.models.experiments import ContentExperiment
from app.models.planner import ContentProposal, PlannerRun
from app.providers.llm.resolver import get_planner_provider
from app.schemas.experiments import ExperimentCreate
from app.schemas.planner import (
    CandidateIdea,
    CandidateIdeas,
    ResearchPlan,
    SceneSpecSet,
    ScriptDraft,
    SourceInput,
)
from app.services import experiments
from app.services.editorial_rules import prompt_rules

PLANNER_VERSION = "6A.0"
PILLARS = (
    "cybersecurity",
    "internet infrastructure",
    "software engineering",
    "AI systems",
    "consumer technology",
    "data/privacy",
)
_WORDS = re.compile(r"\b[\w'-]+\b")


def topic_key(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))[:300]


def _tokens(value: str) -> set[str]:
    return {token for token in topic_key(value).split() if len(token) > 2}


def duplicate_reason(candidate: str, existing: list[str]) -> str | None:
    """Conservative deterministic duplicate gate independent of LLM judgment."""
    candidate_key = topic_key(candidate)
    candidate_tokens = _tokens(candidate)
    for current in existing:
        current_key = topic_key(current)
        current_tokens = _tokens(current)
        if candidate_key == current_key:
            return f"Exact duplicate of existing topic: {current}"
        union = candidate_tokens | current_tokens
        overlap = len(candidate_tokens & current_tokens) / len(union) if union else 0
        # Two very similar short noun phrases are too risky to autonomously reuse.
        if overlap >= 0.60 and len(candidate_tokens) >= 3:
            return f"High deterministic topic overlap with: {current}"
        sensitive = {"password", "totp", "authenticator", "hash", "dns"}
        if candidate_tokens & sensitive and (candidate_tokens & sensitive) == (
            current_tokens & sensitive
        ):
            return f"Potentially overlapping core concept with: {current}"
    return None


def unsupported_hook_reason(hook: str) -> str | None:
    if re.search(r"\b\d+(?:\.\d+)?%", hook):
        return "Unsupported percentage in hook; research is required before selection."
    return None


def word_count(text: str) -> int:
    return len(_WORDS.findall(text))


def observed_wpm(profiles: list[ContentFeatureProfile]) -> float:
    measured = [
        float(p.narration_words_per_minute) for p in profiles if p.narration_words_per_minute
    ]
    return sum(measured) / len(measured) if measured else 180.0


async def build_context(session: AsyncSession) -> dict[str, Any]:
    projects = list(
        (
            await session.execute(select(ContentProject).order_by(ContentProject.created_at))
        ).scalars()
    )
    profiles = list((await session.execute(select(ContentFeatureProfile))).scalars())
    windows = list((await session.execute(select(YouTubeVideoWindowMetric))).scalars())
    proposal_rows = list((await session.execute(select(ContentProposal))).scalars())
    published = [
        p
        for p in projects
        if p.status
        in {
            ProjectStatus.PUBLISHED.value,
            ProjectStatus.ANALYTICS_COLLECTING.value,
            ProjectStatus.COMPLETED.value,
        }
    ]
    available = sum(1 for w in windows if w.status == AnalyticsWindowStatus.AVAILABLE.value)
    pending = sum(1 for w in windows if w.status == AnalyticsWindowStatus.PENDING.value)
    return {
        "brand": "SystemDecoded: Complex Technology. Decoded. Beginner-friendly, accurate technical Shorts.",
        "pillars": list(PILLARS),
        "published_count": len(published),
        "published_topics": [{"topic": p.topic, "pillar": p.content_pillar} for p in published],
        "existing_topics": [p.topic for p in projects],
        "rejected_topics": [p.topic for p in proposal_rows if p.state == "REJECTED"],
        "analytics": {
            "available_window_rows": available,
            "pending_window_rows": pending,
            "evidence_status": "LIMITED" if pending or len(published) < 3 else "AVAILABLE",
            "rule": "Pending analytics are not zero and cannot establish a winning format.",
        },
        "media": "Original Chatterbox narration; Motion Canvas generic declarative diagrams; FFmpeg; 1080x1920; 30-40 seconds; no production in planning.",
        "research": "Use authoritative primary documentation. Source URLs must be verifiable; unverified research blocks draft creation.",
        "observed_wpm": round(observed_wpm(profiles), 1),
        "context_limit_note": "Compact summary only; never infer conclusions beyond the stored evidence.",
    }


def _candidate_prompt(context: dict[str, Any]) -> str:
    return (
        "Generate exactly 5 distinct candidate ideas for the next SystemDecoded Short. "
        "Use this factual application context exactly; do not claim performance conclusions not present. "
        f"CONTEXT={context!r}\n"
        "Avoid existing/rejected topics. Prefer a researchable, mobile-explainable mechanism. "
        "Keep every text field concise (one or two short sentences; no more than 60 words). "
        "Each candidate must state a cautious experiment hypothesis and limitation."
    )


def _rank(
    candidate: CandidateIdea, *, duplicate: str | None, recent_pillars: Counter[str]
) -> dict[str, int]:
    pillar = candidate.content_pillar.lower()
    diversity = 2 if recent_pillars[pillar] == 0 else max(0, 2 - recent_pillars[pillar])
    return {
        "duplicate_guard": 0 if duplicate else 3,
        "duration": 2 if 30 <= candidate.target_duration_seconds <= 40 else 0,
        "diversity": diversity,
        "research": 2 if len(candidate.research_feasibility) >= 20 else 0,
        "visual": 2 if len(candidate.visual_feasibility) >= 20 else 0,
    }


async def _verify_sources(plan: ResearchPlan) -> list[SourceInput]:
    """Verify retrievability; fetched text is untrusted data, never instructions."""
    verified = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(20.0)) as client:
        for source in plan.sources:
            parsed = urlparse(str(source.url))
            if parsed.scheme != "https" or not parsed.netloc:
                continue
            try:
                response = await client.get(
                    str(source.url), headers={"User-Agent": "SystemDecodedResearch/1.0"}
                )
            except httpx.HTTPError:
                continue
            if 200 <= response.status_code < 300 and len(response.content) >= 200:
                verified.append(source)
    return verified


async def start_run(session: AsyncSession, *, trigger: str = "MANUAL") -> PlannerRun:
    active = (
        await session.execute(
            select(PlannerRun).where(PlannerRun.status.in_(["PENDING", "RUNNING"]))
        )
    ).scalar_one_or_none()
    if active:
        raise ConflictError("A planner run is already active; wait for it to finish.")
    context = await build_context(session)
    run = PlannerRun(
        trigger=trigger,
        status="PENDING",
        provider="ollama",
        model=None,
        planner_version=PLANNER_VERSION,
        context_snapshot=context,
        started_at=None,
    )
    session.add(run)
    await session.flush()
    return run


async def generate_candidates(session: AsyncSession, run: PlannerRun) -> list[ContentProposal]:
    provider = get_planner_provider()
    result = await provider.generate_structured(
        _candidate_prompt(run.context_snapshot), CandidateIdeas
    )
    run.provider, run.model = result.raw.provider, result.raw.model
    existing = list(run.context_snapshot["existing_topics"]) + list(
        run.context_snapshot["rejected_topics"]
    )
    pillars = Counter(
        str(item.get("pillar") or "").lower() for item in run.context_snapshot["published_topics"]
    )
    rows: list[ContentProposal] = []
    for index, idea in enumerate(result.value.candidates, start=1):
        duplicate = duplicate_reason(idea.topic, existing)
        unsupported_stat = unsupported_hook_reason(idea.hook)
        rejection = duplicate or unsupported_stat
        scoring = _rank(idea, duplicate=rejection, recent_pillars=pillars)
        row = ContentProposal(
            planner_run_id=run.id,
            rank=index,
            state="REJECTED" if rejection else "CANDIDATE",
            topic=idea.topic,
            topic_key=topic_key(idea.topic),
            content_pillar=idea.content_pillar,
            core_question=idea.core_question,
            hook=idea.hook,
            hook_type=idea.hook_type,
            target_duration_seconds=idea.target_duration_seconds,
            rationale=idea.why_fits,
            why_now=idea.why_now,
            novelty_assessment=rejection or "No deterministic duplicate found.",
            research_feasibility=idea.research_feasibility,
            visual_feasibility=idea.visual_feasibility,
            experiment_hypothesis=idea.experiment_hypothesis,
            primary_variable=idea.primary_experimental_variable,
            evidence_limitations=idea.evidence_limitation,
            rejection_reason=rejection,
            scoring=scoring,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    viable = [row for row in rows if row.state == "CANDIDATE"]
    if not viable:
        raise TerminalError("All generated candidates failed deterministic duplicate checks.")
    selected = max(viable, key=lambda row: (sum(row.scoring.values()), -row.rank))
    selected.selected, selected.state = True, "SELECTED"
    run.decision_summary = (
        f"Selected {selected.topic!r} after deterministic duplicate and feasibility checks. "
        f"Evidence remains {run.context_snapshot['analytics']['evidence_status']}."
    )
    await session.flush()
    return rows


def _research_prompt(proposal: ContentProposal) -> str:
    if "dnssec" in proposal.topic.lower():
        return (
            "Build a beginner DNSSEC research plan using the core standards RFC 4033 and "
            "RFC 4035 from rfc-editor.org. Focus only on DNS data-origin authentication, "
            "integrity, authenticated denial of existence, the chain of trust, and how a "
            "validating resolver handles bogus data. DNSSEC does not encrypt DNS traffic. "
            "Do not include adoption rates, deployment prevalence, or claims about why sites "
            "do not deploy it. Return 2-4 authoritative HTTPS primary/standards sources, and "
            "state only claims directly supported by each source."
        )
    return (
        f"Find 2-4 authoritative HTTPS primary/standards documentation sources for a beginner Short about {proposal.topic!r}. "
        "Return only sources whose URLs you are confident are stable. Summaries must state only what the source supports; flag simplifications. "
        "The local model is not a source."
    )


def _grounded_sources(proposal: ContentProposal, plan: ResearchPlan) -> list[SourceInput]:
    """Replace model-authored DNSSEC claims with a reviewed standards claim set."""
    if "dnssec" not in proposal.topic.lower():
        return plan.sources
    return [
        SourceInput(
            url="https://www.rfc-editor.org/rfc/rfc4033.html",
            title="DNS Security Introduction and Requirements",
            publisher="IETF",
            summary=(
                "Defines DNSSEC's data-origin authentication, integrity, authenticated "
                "denial of existence, resolver security states, and confidentiality limits."
            ),
            claims=[
                "DNSSEC provides data-origin authentication and integrity protection for DNS data, plus authenticated denial of existence.",
                "DNSSEC does not provide confidentiality and does not protect DNS queries or responses from disclosure.",
                "A security-aware resolver distinguishes secure, insecure, bogus, and indeterminate responses; an unsigned insecure delegation is not automatically bogus.",
            ],
            uncertainty=(
                "These guarantees depend on a configured trust anchor and a valid chain of trust."
            ),
        ),
        SourceInput(
            url="https://www.rfc-editor.org/rfc/rfc4035.html",
            title="Protocol Modifications for the DNS Security Extensions",
            publisher="IETF",
            summary=(
                "Specifies how security-aware resolvers build authentication chains and "
                "validate DNSKEY, DS, and RRSIG records."
            ),
            claims=[
                "A validating resolver follows DS and DNSKEY records from a configured trust anchor and verifies RRSIG signatures over signed RRsets.",
                "RRSIG validation includes checking signature inception and expiration times.",
                "When validation determines that signed data is bogus, a security-aware resolver returns an error rather than treating that data as authentic.",
            ],
            uncertainty=(
                "Resolver behavior can be changed by explicit checking-disabled requests; the Short describes normal validation."
            ),
        ),
    ]


def _script_prompt(proposal: ContentProposal, sources: list[SourceInput]) -> str:
    research = [
        {"title": s.title, "url": str(s.url), "summary": s.summary, "claims": s.claims}
        for s in sources
    ]
    return (
        f"Draft a factual, beginner-friendly 5-scene narration about how DNSSEC protects against "
        f"cache poisoning, based on the selected proposal {proposal.topic!r}. "
        "Write exactly 90 narration words total. Every one of the exactly 5 scenes must contain "
        "exactly 18 spoken words. Count each scene before responding. The schema permits 15-22 "
        "words per scene and only 90-108 total words. "
        "Use short spoken sentences and "
        "no unsupported facts or markdown formatting. DNSSEC authenticates DNS data and origin; "
        "it does not encrypt DNS traffic or hide lookup names. Describe cache-poisoning protection "
        "only as validation rejecting forged data when the signed chain of trust is configured and "
        "validated. Do not call DNSSEC an ultimate safeguard. Do not state adoption percentages, "
        "claim that most sites skip it, or make other deployment claims unless the supplied source "
        "explicitly supports them. "
        f"Research evidence: {research!r}. Do not cite URLs in narration. "
        f"{prompt_rules()}"
    )


def _scene_prompt(script: ScriptDraft) -> str:
    return (
        "Create one declarative SceneSpec per narration scene. Use short visual labels, one of the allowed types, "
        "valid directed node/connectors, and timing anchor phrases copied exactly from that scene "
        "narration. Classify each connector as normal, injection, replacement, verification, or "
        "rejection. Arrow direction must match the real information flow; injection and replacement "
        "must originate at a danger node. Omit redundant connector labels when the visual is clear. "
        "Reject or simplify a scene that cannot fit without tiny text. "
        f"{prompt_rules()} NARRATION={script.model_dump()!r}"
    )


def validate_script_grounding(proposal: ContentProposal, script: ScriptDraft) -> None:
    """Apply deterministic topic-specific factual gates after schema validation."""
    if "dnssec" not in proposal.topic.lower():
        return
    narration = " ".join(scene.narration for scene in script.scenes).lower()
    forbidden = {
        "fake entries are impossible": "DNSSEC mitigates forged data; it cannot make attacks impossible",
        "most sites": "deployment prevalence needs separate authoritative evidence",
        "rarely used": "deployment prevalence needs separate authoritative evidence",
        "ultimate safeguard": "DNSSEC is not a complete DNS or endpoint security safeguard",
        "no signature found": "unsigned insecure delegations are not automatically bogus",
    }
    violations = [reason for phrase, reason in forbidden.items() if phrase in narration]
    if "%" in narration:
        violations.append("adoption percentages need separate authoritative evidence")
    if not any(phrase in narration for phrase in ("does not encrypt", "doesn't encrypt")):
        violations.append("the beginner script must distinguish DNSSEC from DNS encryption")
    if not any(
        phrase in narration
        for phrase in (
            "chain of trust",
            "chain of trusted keys",
            "uses trusted keys to check",
        )
    ):
        violations.append("the script must qualify validation with the chain of trust")
    if violations:
        raise TerminalError("DNSSEC factual-grounding blocker: " + "; ".join(violations))


async def prepare_draft(session: AsyncSession, run: PlannerRun) -> ContentProposal:
    selected = (
        await session.execute(
            select(ContentProposal).where(
                ContentProposal.planner_run_id == run.id, ContentProposal.selected.is_(True)
            )
        )
    ).scalar_one()
    if selected.project_id is not None:
        raise ConflictError("The selected proposal already owns a draft project.")
    provider = get_planner_provider()
    research_result = await provider.generate_structured(_research_prompt(selected), ResearchPlan)
    grounded_plan = ResearchPlan(sources=_grounded_sources(selected, research_result.value))
    sources = await _verify_sources(grounded_plan)
    if len(sources) < 2:
        raise TerminalError(
            "Research blocker: fewer than two authoritative source URLs were retrievable."
        )
    script_result = await provider.generate_structured(
        _script_prompt(selected, sources), ScriptDraft
    )
    validate_script_grounding(selected, script_result.value)
    narration = " ".join(scene.narration for scene in script_result.value.scenes)
    words = word_count(narration)
    if not 90 <= words <= 108:
        raise TerminalError(
            f"Script blocker: validated narration has {words} words; expected 90-108."
        )
    scenes_result = await provider.generate_structured(
        _scene_prompt(script_result.value), SceneSpecSet
    )
    if len(scenes_result.value.scenes) != len(script_result.value.scenes):
        raise TerminalError("SceneSpec blocker: scene count does not match narration.")

    channel = (
        await session.execute(select(Channel).order_by(Channel.created_at).limit(1))
    ).scalar_one_or_none()
    if channel is None:
        raise TerminalError("No configured channel exists for a draft project.")
    project = ContentProject(
        channel_id=channel.id,
        topic=selected.topic,
        topic_key=selected.topic_key,
        working_title=script_result.value.title,
        content_pillar=selected.content_pillar,
        content_format="How It Works",
        target_viewer="Curious general technology audience",
        curiosity_gap=selected.core_question,
        target_duration_seconds=selected.target_duration_seconds,
        status=ProjectStatus.IDEA.value,
        created_by="SYSTEM",
    )
    session.add(project)
    await session.flush()
    await experiments.create_experiment(
        session,
        ExperimentCreate(
            project_id=project.id,
            hypothesis=selected.experiment_hypothesis,
            primary_variable=selected.primary_variable,
            planned_topic_category=selected.content_pillar,
            planned_topic=selected.topic,
            planned_duration_min_seconds=Decimal("30"),
            planned_duration_max_seconds=Decimal("40"),
            planned_explanation_pattern="mechanism walkthrough",
            planned_audience_level="beginner_general",
            planned_technical_depth="introductory",
            planned_title_style="how_to",
        ),
    )
    for source in sources:
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
                    verification_status="SOURCE_RETRIEVED",
                    used_in_script=True,
                )
            )
    script = Script(
        project_id=project.id,
        version=1,
        is_current=True,
        title_candidates=[script_result.value.title],
        selected_title=script_result.value.title,
        hook_candidates=[selected.hook],
        selected_hook=selected.hook,
        narration=narration,
        description=script_result.value.description,
        authoring_mode="local_llm",
        word_count=words,
        estimated_duration_seconds=Decimal(
            str(round(words / run.context_snapshot["observed_wpm"] * 60, 2))
        ),
    )
    session.add(script)
    await session.flush()
    for narration_scene, spec in zip(
        script_result.value.scenes, scenes_result.value.scenes, strict=True
    ):
        anchors = {anchor.id: anchor.phrase for anchor in spec.timing_anchors}
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
                    "timing_anchors": anchors,
                    "animation_intent": spec.animation_intent,
                    "end_card": spec.end_card,
                },
            )
        )
    await session.flush()
    project.current_script_id = script.id
    selected.project_id = project.id
    await transition(
        session,
        project,
        ProjectStatus.IDEA_APPROVED,
        reason="Autonomous draft prepared; awaiting content review",
    )
    await transition(
        session, project, ProjectStatus.RESEARCHING, reason="Verified source research persisted"
    )
    await transition(
        session,
        project,
        ProjectStatus.RESEARCH_READY,
        reason="Research source and claims review ready",
    )
    await transition(
        session, project, ProjectStatus.SCRIPT_GENERATING, reason="Validated local draft generated"
    )
    await transition(
        session, project, ProjectStatus.SCRIPT_REVIEW, reason="READY FOR CONTENT REVIEW"
    )
    run.status, run.completed_at = "READY_FOR_REVIEW", utcnow()
    run.provider, run.model = script_result.raw.provider, script_result.raw.model
    await session.flush()
    return selected


async def execute(session: AsyncSession, *, run_id: Any) -> PlannerRun:
    """Execute the durable run selected by the API, never a second run."""
    run = await session.get(PlannerRun, run_id)
    if run is None:
        raise TerminalError(f"Planner run {run_id} no longer exists.")
    if run.status not in {"PENDING", "RUNNING"}:
        return run
    run.status, run.started_at = "RUNNING", utcnow()
    await session.flush()
    try:
        proposals_exist = (
            await session.execute(
                select(ContentProposal.id).where(ContentProposal.planner_run_id == run.id).limit(1)
            )
        ).scalar_one_or_none()
        if proposals_exist is None:
            await generate_candidates(session, run)
        await prepare_draft(session, run)
    except Exception as exc:  # noqa: BLE001 - durable run records every planner failure
        run.status, run.error_message, run.completed_at = "FAILED", str(exc)[:4000], utcnow()
        await session.flush()
    return run


async def select_proposal(
    session: AsyncSession, *, run_id: Any, proposal_id: Any
) -> tuple[PlannerRun, ContentProposal]:
    """Persist an explicit human override without creating downstream work."""
    run = await session.get(PlannerRun, run_id)
    if run is None:
        raise TerminalError(f"Planner run {run_id} no longer exists.")
    proposals = list(
        (
            await session.execute(
                select(ContentProposal).where(ContentProposal.planner_run_id == run.id)
            )
        ).scalars()
    )
    chosen = next((row for row in proposals if row.id == proposal_id), None)
    if chosen is None:
        raise TerminalError("The proposal does not belong to this planner run.")
    if chosen.state == "REJECTED":
        raise ConflictError("A deterministically rejected proposal cannot be selected.")
    if chosen.project_id is not None:
        raise ConflictError(
            "This candidate already owns a draft project; review that project instead."
        )
    original = next((row for row in proposals if row.selected), None)
    ai_original = next((row for row in proposals if row.scoring.get("ai_original_selected")), None)
    if original is not None and ai_original is None:
        original.scoring = {**original.scoring, "ai_original_selected": True}
        flag_modified(original, "scoring")
        ai_original = original

    for row in proposals:
        if row.project_id is None:
            continue
        downstream = await session.get(ContentProject, row.project_id)
        if downstream is None:
            raise ConflictError("A prior proposal references a missing draft project.")
        if downstream.status not in {
            ProjectStatus.SCRIPT_REVIEW.value,
            ProjectStatus.NEEDS_REVISION.value,
        }:
            raise ConflictError(f"Cannot supersede project {downstream.id} in {downstream.status}.")
        await transition(
            session,
            downstream,
            ProjectStatus.REJECTED,
            actor="HUMAN",
            reason=f"Superseded by planner proposal {chosen.id}",
        )
        experiment = (
            await session.execute(
                select(ContentExperiment).where(ContentExperiment.project_id == downstream.id)
            )
        ).scalar_one_or_none()
        if experiment is not None and experiment.status in {
            ExperimentStatus.DRAFT.value,
            ExperimentStatus.PLANNED.value,
        }:
            await experiments.transition_status(session, experiment, ExperimentStatus.CANCELLED)
    for row in proposals:
        row.selected = row.id == chosen.id
        if row.state != "REJECTED":
            row.state = "SELECTED" if row.selected else "CANDIDATE"
    chosen.scoring = {**chosen.scoring, "selected_by_human": True}
    flag_modified(chosen, "scoring")
    run.status = "PENDING"
    run.completed_at = None
    run.decision_summary = (
        f"Human override selected {chosen.topic!r}. Original AI recommendation: "
        f"{ai_original.topic!r}."
        if ai_original
        else f"Human selected {chosen.topic!r}."
    )
    await session.flush()
    return run, chosen


async def resume_failed_run(session: AsyncSession, *, run_id: Any) -> PlannerRun:
    """Resume at research/script preparation, reusing persisted candidates."""
    run = await session.get(PlannerRun, run_id)
    if run is None:
        raise TerminalError(f"Planner run {run_id} no longer exists.")
    if run.status != "FAILED":
        raise ConflictError(
            f"Only a FAILED planner run can resume; current status is {run.status}."
        )
    proposals = list(
        (
            await session.execute(
                select(ContentProposal).where(ContentProposal.planner_run_id == run.id)
            )
        ).scalars()
    )
    if not proposals or sum(1 for row in proposals if row.selected) != 1:
        raise ConflictError(
            "Resume requires persisted candidates and exactly one selected proposal."
        )
    if any(row.project_id is not None for row in proposals):
        raise ConflictError("A draft project already exists; resuming would create a duplicate.")
    run.status = "PENDING"
    run.completed_at = None
    await session.flush()
    return run


async def serialize_run(session: AsyncSession, run: PlannerRun) -> dict[str, Any]:
    proposals = list(
        (
            await session.execute(
                select(ContentProposal)
                .where(ContentProposal.planner_run_id == run.id)
                .order_by(ContentProposal.rank)
            )
        ).scalars()
    )
    return {
        "id": str(run.id),
        "trigger": run.trigger,
        "status": run.status,
        "provider": run.provider,
        "model": run.model,
        "planner_version": run.planner_version,
        "context": run.context_snapshot,
        "decision_summary": run.decision_summary,
        # Retain the original error on the durable row for audit/history, while
        # exposing it as the current failure only when the run is still failed.
        "error": run.error_message if run.status == "FAILED" else None,
        "previous_error": run.error_message if run.status != "FAILED" else None,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "proposals": [
            {
                "id": str(p.id),
                "rank": p.rank,
                "state": p.state,
                "selected": p.selected,
                "ai_selected": bool(p.scoring.get("ai_original_selected"))
                or (p.selected and not p.scoring.get("selected_by_human")),
                "topic": p.topic,
                "pillar": p.content_pillar,
                "hook": p.hook,
                "rationale": p.rationale,
                "novelty": p.novelty_assessment,
                "research_feasibility": p.research_feasibility,
                "visual_feasibility": p.visual_feasibility,
                "hypothesis": p.experiment_hypothesis,
                "primary_variable": p.primary_variable,
                "evidence_limitations": p.evidence_limitations,
                "rejection_reason": p.rejection_reason,
                "scoring": p.scoring,
                "project_id": str(p.project_id) if p.project_id else None,
            }
            for p in proposals
        ],
    }
