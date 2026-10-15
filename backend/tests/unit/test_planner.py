"""Deterministic Phase 6A safeguards do not depend on live local models."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.planner import ResearchPlan, SceneSpecInput, SceneSpecSet, ScriptDraft
from app.services.planner import (
    _grounded_sources,
    _research_prompt,
    _script_prompt,
    _verify_sources,
    duplicate_reason,
    topic_key,
    unsupported_hook_reason,
    validate_script_grounding,
    word_count,
)


def test_topic_key_is_stable_and_punctuation_insensitive() -> None:
    assert topic_key("How DNS Works!") == "how dns works"


def test_obvious_topic_duplicate_is_blocked_without_an_llm() -> None:
    reason = duplicate_reason(
        "Why databases don't save your password",
        ["What websites store instead of your password"],
    )
    assert reason is not None


def test_distinct_topics_are_not_false_positive_duplicates() -> None:
    assert duplicate_reason("How browsers cache images", ["How cookies remember a login"]) is None


def test_word_count_is_programmatic() -> None:
    assert word_count("One, two -- three's four.") == 4


def test_scene_spec_rejects_connector_to_unknown_node() -> None:
    with pytest.raises(ValidationError, match="declared scene nodes"):
        SceneSpecInput.model_validate(
            {
                "scene_number": 1,
                "visual_type": "FLOW",
                "visual_purpose": "Show a safe visual flow.",
                "labels": ["REQUEST", "SERVER"],
                "nodes": [{"id": "request", "label": "REQUEST"}],
                "connectors": [{"from": "request", "to": "missing"}],
                "timing_anchors": [{"id": "request", "phrase": "request", "intent": "reveal"}],
                "animation_intent": "Reveal the request, then its destination.",
            }
        )


def test_scene_spec_rejects_semantically_reversed_injection() -> None:
    with pytest.raises(ValidationError, match="injection connectors must originate"):
        SceneSpecInput.model_validate(
            {
                "scene_number": 1,
                "visual_type": "FLOW",
                "visual_purpose": "Show who introduces untrusted information.",
                "labels": ["FORGED DATA"],
                "nodes": [
                    {"id": "client", "label": "CLIENT", "emphasis": "normal"},
                    {"id": "cache", "label": "CACHE", "emphasis": "accent"},
                ],
                "connectors": [{"from": "client", "to": "cache", "kind": "injection"}],
                "timing_anchors": [{"id": "inject", "phrase": "forged data", "intent": "inject"}],
                "animation_intent": "Show the forged data entering the cache.",
            }
        )


def test_scene_spec_set_requires_consecutive_scene_numbers() -> None:
    with pytest.raises(ValidationError, match="cover each scene"):
        SceneSpecSet.model_validate(
            {
                "scenes": [
                    {
                        "scene_number": index,
                        "visual_type": "FLOW",
                        "visual_purpose": "Show a safe visual flow.",
                        "labels": ["REQUEST"],
                        "nodes": [{"id": f"node{index}", "label": "REQUEST"}],
                        "connectors": [],
                        "timing_anchors": [
                            {"id": f"anchor{index}", "phrase": "request", "intent": "reveal"}
                        ],
                        "animation_intent": "Reveal it.",
                    }
                    for index in [1, 2, 3, 5, 4]
                ]
            }
        )


def test_script_draft_rejects_total_word_count_outside_target() -> None:
    with pytest.raises(ValidationError, match=r"scene 1 narration contains 3 words"):
        ScriptDraft.model_validate(
            {
                "title": "A clear technical title",
                "description": "A sufficiently detailed description for the validation contract.",
                "scenes": [
                    {"scene_number": number, "narration": "One two three."}
                    for number in range(1, 6)
                ],
            }
        )


def test_dnssec_prompts_keep_research_and_script_angle_grounded() -> None:
    proposal = SimpleNamespace(topic="How DNSSEC Protects Against Cache Poisoning")
    research_prompt = _research_prompt(proposal)
    script_prompt = _script_prompt(proposal, [])

    assert "RFC 4033" in research_prompt
    assert "RFC 4035" in research_prompt
    assert "does not encrypt DNS traffic" in research_prompt
    assert "Do not include adoption rates" in research_prompt
    assert "exactly 90 narration words" in script_prompt
    assert "exactly 18 spoken words" in script_prompt
    assert "Do not state adoption percentages" in script_prompt


def test_dnssec_research_uses_reviewed_rfc_claims_not_model_claims() -> None:
    proposal = SimpleNamespace(topic="How DNSSEC Protects Against Cache Poisoning")
    model_plan = ResearchPlan.model_validate(
        {
            "sources": [
                {
                    "url": "https://example.com/one",
                    "title": "Fabricated model title",
                    "publisher": "Model",
                    "summary": "A model-authored summary that must not become stored evidence.",
                    "claims": ["An unsupported adoption percentage."],
                    "uncertainty": "This is intentionally untrusted test input.",
                },
                {
                    "url": "https://example.com/two",
                    "title": "Another fabricated title",
                    "publisher": "Model",
                    "summary": "Another model-authored summary that must not become evidence.",
                    "claims": ["Another unsupported claim."],
                    "uncertainty": "This is intentionally untrusted test input.",
                },
            ]
        }
    )
    sources = _grounded_sources(proposal, model_plan)

    assert [str(source.url) for source in sources] == [
        "https://www.rfc-editor.org/rfc/rfc4033.html",
        "https://www.rfc-editor.org/rfc/rfc4035.html",
    ]
    assert any("does not provide confidentiality" in claim for claim in sources[0].claims)
    assert all("percentage" not in claim for source in sources for claim in source.claims)


def test_dnssec_grounding_rejects_absolute_or_unsigned_response_claims() -> None:
    proposal = SimpleNamespace(topic="How DNSSEC Protects Against Cache Poisoning")
    unsafe = ScriptDraft.model_validate(
        {
            "title": "A factual DNSSEC explanation",
            "description": "A sufficiently detailed description for the validation contract.",
            "scenes": [
                {
                    "scene_number": number,
                    "narration": (
                        "Fake entries are impossible because DNSSEC checks every answer "
                        "through a chain of trust and does not encrypt lookups."
                    ),
                }
                for number in range(1, 6)
            ],
        }
    )

    with pytest.raises(Exception, match="DNSSEC factual-grounding blocker"):
        validate_script_grounding(proposal, unsafe)


def test_dnssec_grounding_accepts_beginner_chain_of_trusted_keys_wording() -> None:
    proposal = SimpleNamespace(topic="How DNSSEC Protects Against Cache Poisoning")
    reviewed = ScriptDraft.model_validate(
        {
            "title": "How DNSSEC Protects Against Cache Poisoning",
            "description": (
                "A beginner-friendly explanation grounded in the persisted DNSSEC RFC research."
            ),
            "scenes": [
                {
                    "scene_number": 1,
                    "narration": (
                        "What if your browser got a fake answer to one simple question: "
                        "where is this website?"
                    ),
                },
                {
                    "scene_number": 2,
                    "narration": (
                        "Attackers can poison a DNS cache, replacing a real server address "
                        "with a false one that points somewhere else."
                    ),
                },
                {
                    "scene_number": 3,
                    "narration": (
                        "DNSSEC makes signed DNS answers verifiable. A resolver checks digital "
                        "signatures and follows a chain of trusted keys."
                    ),
                },
                {
                    "scene_number": 4,
                    "narration": (
                        "When a signed answer fails validation, the resolver treats it as bogus "
                        "and normally returns an error instead of trusting it."
                    ),
                },
                {
                    "scene_number": 5,
                    "narration": (
                        "But DNSSEC doesn't encrypt your lookups or hide the websites you request. "
                        "It checks authenticity, not privacy."
                    ),
                },
            ],
        }
    )

    validate_script_grounding(proposal, reviewed)
    assert word_count(" ".join(scene.narration for scene in reviewed.scenes)) == 91


def test_candidate_percentage_requires_research_before_selection() -> None:
    assert unsupported_hook_reason("Why do 99% of sites skip DNSSEC?") is not None
    assert unsupported_hook_reason("How does DNSSEC authenticate an answer?") is None


def test_script_draft_reports_aggregate_word_count_after_scene_lengths_pass() -> None:
    narration = (
        "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen"
    )
    with pytest.raises(ValidationError, match=r"contains 75 words.*90-108 words"):
        ScriptDraft.model_validate(
            {
                "title": "A clear technical title",
                "description": "A sufficiently detailed description for the validation contract.",
                "scenes": [
                    {"scene_number": number, "narration": narration} for number in range(1, 6)
                ],
            }
        )


@pytest.mark.asyncio
async def test_source_verification_receives_the_validated_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the planner must not pass `ResearchPlan.sources` twice."""

    plan = ResearchPlan.model_validate(
        {
            "sources": [
                {
                    "url": "https://example.com/spec",
                    "title": "Primary specification",
                    "publisher": "Example Standards",
                    "summary": "A sufficiently long factual summary for validation.",
                    "claims": ["The source supports this narrow claim."],
                    "uncertainty": "The example URL is only used for this unit test.",
                },
                {
                    "url": "https://example.com/guide",
                    "title": "Implementation guide",
                    "publisher": "Example Standards",
                    "summary": "Another sufficiently long factual summary for validation.",
                    "claims": ["The guide supports another narrow claim."],
                    "uncertainty": "The example URL is only used for this unit test.",
                },
            ]
        }
    )

    class Response:
        status_code = 200
        content = b"x" * 200

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, *_: object, **__: object) -> Response:
            return Response()

    monkeypatch.setattr("app.services.planner.httpx.AsyncClient", lambda **_: Client())
    assert await _verify_sources(plan) == plan.sources
