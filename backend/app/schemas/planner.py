"""Strict planner and generic-scene contracts; raw model JSON never escapes these."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class VisualType(StrEnum):
    TITLE_REVEAL = "TITLE_REVEAL"
    FLOW = "FLOW"
    PARALLEL_FLOW = "PARALLEL_FLOW"
    PROCESS = "PROCESS"
    COMPARISON = "COMPARISON"
    DATABASE_FLOW = "DATABASE_FLOW"
    NETWORK_FLOW = "NETWORK_FLOW"
    TIMELINE = "TIMELINE"
    CODE_REVEAL = "CODE_REVEAL"
    CALLOUT = "CALLOUT"


class CandidateIdea(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str = Field(min_length=8, max_length=300)
    content_pillar: str = Field(min_length=3, max_length=100)
    core_question: str = Field(min_length=8, max_length=600)
    hook: str = Field(min_length=8, max_length=500)
    hook_type: str = Field(min_length=3, max_length=80)
    target_duration_seconds: int = Field(ge=30, le=40)
    why_fits: str = Field(min_length=10, max_length=420)
    why_now: str = Field(min_length=10, max_length=420)
    visual_feasibility: str = Field(min_length=5, max_length=280)
    research_feasibility: str = Field(min_length=5, max_length=280)
    experiment_hypothesis: str = Field(min_length=10, max_length=420)
    primary_experimental_variable: str = Field(min_length=3, max_length=100)
    evidence_limitation: str = Field(min_length=8, max_length=420)


class CandidateIdeas(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: list[CandidateIdea] = Field(min_length=5, max_length=5)


class SourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: HttpUrl
    title: str = Field(min_length=4, max_length=500)
    publisher: str = Field(min_length=2, max_length=300)
    summary: str = Field(min_length=20, max_length=3000)
    claims: list[str] = Field(min_length=1, max_length=8)
    uncertainty: str = Field(min_length=5, max_length=1200)


class ResearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[SourceInput] = Field(min_length=2, max_length=6)


class ScriptSceneInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    scene_number: int = Field(ge=1, le=8)
    narration: str = Field(min_length=5, max_length=700)

    @model_validator(mode="after")
    def narration_length(self) -> ScriptSceneInput:
        words = len(re.findall(r"\b[\w'-]+\b", self.narration))
        if not 15 <= words <= 22:
            raise ValueError(
                f"scene {self.scene_number} narration contains {words} words; "
                "it must contain 15-22 words"
            )
        return self


class ScriptDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=5, max_length=300)
    description: str = Field(min_length=20, max_length=1200)
    scenes: list[ScriptSceneInput] = Field(min_length=5, max_length=6)

    @model_validator(mode="after")
    def ordered_scenes(self) -> ScriptDraft:
        if [scene.scene_number for scene in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise ValueError("scene numbers must start at 1 and be consecutive")
        words = sum(len(re.findall(r"\b[\w'-]+\b", scene.narration)) for scene in self.scenes)
        if not 90 <= words <= 108:
            raise ValueError(
                f"narration contains {words} words; it must contain 90-108 words in total"
            )
        return self


class SceneNode(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,30}$")
    label: str = Field(min_length=1, max_length=42)
    emphasis: str = Field(default="normal", pattern=r"^(normal|accent|danger|success)$")


class ConnectorKind(StrEnum):
    NORMAL = "normal"
    INJECTION = "injection"
    REPLACEMENT = "replacement"
    VERIFICATION = "verification"
    REJECTION = "rejection"


class SceneConnector(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    label: str | None = Field(default=None, max_length=30)
    kind: ConnectorKind = ConnectorKind.NORMAL


class TimingAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,30}$")
    phrase: str = Field(min_length=1, max_length=150)
    intent: str = Field(min_length=2, max_length=100)


class SceneSpecInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_number: int = Field(ge=1, le=8)
    visual_type: VisualType
    visual_purpose: str = Field(min_length=8, max_length=500)
    labels: list[str] = Field(min_length=1, max_length=6)
    nodes: list[SceneNode] = Field(min_length=1, max_length=6)
    connectors: list[SceneConnector] = Field(default_factory=list, max_length=8)
    timing_anchors: list[TimingAnchor] = Field(min_length=1, max_length=5)
    animation_intent: str = Field(min_length=5, max_length=300)
    end_card: bool = False

    @model_validator(mode="after")
    def references_are_local_and_valid(self) -> SceneSpecInput:
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("scene node identifiers must be unique")
        if any(edge.from_node not in ids or edge.to_node not in ids for edge in self.connectors):
            raise ValueError("every connector must reference declared scene nodes")
        if any(edge.from_node == edge.to_node for edge in self.connectors):
            raise ValueError("connectors cannot point a node to itself")
        pairs = {(edge.from_node, edge.to_node) for edge in self.connectors}
        if len(pairs) != len(self.connectors):
            raise ValueError("duplicate directed connectors are not allowed")
        by_id = {node.id: node for node in self.nodes}
        for edge in self.connectors:
            source = by_id[edge.from_node]
            target = by_id[edge.to_node]
            if edge.kind in {ConnectorKind.INJECTION, ConnectorKind.REPLACEMENT}:
                if source.emphasis != "danger":
                    raise ValueError(
                        f"{edge.kind.value} connectors must originate at a danger node"
                    )
            if edge.kind == ConnectorKind.REJECTION and target.emphasis not in {
                "danger",
                "success",
            }:
                raise ValueError("rejection connectors must terminate at a terminal state")
        if any(len(label) > 42 for label in self.labels):
            raise ValueError("visual labels must remain mobile-readable")
        if self.end_card and self.scene_number < 5:
            raise ValueError("the SystemDecoded end card is only valid on a closing scene")
        return self


class SceneSpecSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenes: list[SceneSpecInput] = Field(min_length=5, max_length=6)

    @model_validator(mode="after")
    def ordered_scenes(self) -> SceneSpecSet:
        if [scene.scene_number for scene in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise ValueError("scene specs must cover each scene in order")
        return self


class PlannerStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger: str = Field(default="MANUAL", max_length=40)
