"""Explicit development benchmark for Phase 6A.0 local model selection.

This does not create projects, planner records, scripts, or production assets.
It only writes a local JSON report beneath ``.poc/llm-evaluations`` so that
human review can inspect both score inputs and raw model output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import RetryableError, TerminalError
from app.providers.llm.ollama import OllamaLLM, StructuredOutputError


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    content_pillar: str
    core_question: str
    hook: str
    hook_type: str
    target_duration_seconds: int = Field(ge=20, le=45)
    why_fits: str
    why_now: str
    visual_feasibility: str
    research_feasibility: str
    experiment_hypothesis: str
    primary_experimental_variable: str
    evidence_limitation: str


class CandidateSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[Candidate] = Field(min_length=7, max_length=7)


class DuplicateAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duplicate: bool
    reason: str
    confidence_label: Literal["LOW", "MEDIUM", "HIGH"]


class DuplicateSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessments: list[DuplicateAssessment] = Field(min_length=4, max_length=4)


class ScriptScene(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_number: int = Field(ge=1, le=5)
    narration: str


class ScriptPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    scenes: list[ScriptScene] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def _validate_narration_shape(self) -> ScriptPlan:
        if [scene.scene_number for scene in self.scenes] != [1, 2, 3, 4, 5]:
            raise ValueError("scene numbers must be exactly 1 through 5 in order")
        words = len(re.findall(r"\b[\w'-]+\b", " ".join(scene.narration for scene in self.scenes)))
        if not 90 <= words <= 105:
            raise ValueError(f"narration must contain 90-105 words, got {words}")
        return self


class SceneSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_number: int = Field(ge=1, le=5)
    visual_type: Literal[
        "TITLE_REVEAL",
        "FLOW",
        "PARALLEL_FLOW",
        "COMPARISON",
        "DATABASE_FLOW",
        "NETWORK_FLOW",
        "TIMELINE",
        "CODE_REVEAL",
        "METRIC",
        "PROCESS",
        "CALLOUT",
    ]
    labels: list[str] = Field(min_length=1, max_length=5)
    connectors: list[str] = Field(max_length=4)
    visual_purpose: str


class SceneSpecSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenes: list[SceneSpec] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def _validate_scene_numbers(self) -> SceneSpecSet:
        if [scene.scene_number for scene in self.scenes] != [1, 2, 3, 4, 5]:
            raise ValueError("scene numbers must be exactly 1 through 5 in order")
        return self


class EvidenceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation_basis: str
    must_not_conclude: list[str] = Field(min_length=2)
    next_step: str
    evidence_maturity: Literal["LIMITED", "MATURE"]


SYSTEM = """You are evaluating an internal SystemDecoded planning task. Return only JSON that
matches the supplied schema. Never invent performance data. Be concise, technically accurate,
and explain uncertainty plainly."""

CHANNEL_CONTEXT = """SystemDecoded explains complex technology to a curious general audience.
Pillars: cybersecurity, internet infrastructure, software engineering, AI systems, consumer
technology, and data/privacy. Existing published Shorts are about TOTP/authenticator codes and
what websites store instead of passwords; both are cybersecurity. Video #2 is newly published:
D1/D3/D7 analytics are pending, so no performance conclusion is justified. Production uses
Original Chatterbox narration, Motion Canvas, and FFmpeg. Target Shorts are 30-40 seconds."""

PROMPTS: dict[str, tuple[str, type[BaseModel]]] = {
    "A_candidates": (
        CHANNEL_CONTEXT
        + "\nGenerate exactly seven genuinely distinct Video #3 candidates. Diversify away from "
        "cybersecurity where sensible; do not claim analytics prove a winner.",
        CandidateSet,
    ),
    "B_duplicates": (
        "Assess these in order: (1) existing: How websites store passwords | candidate: Why "
        "databases do not save your password; (2) existing: TOTP authenticator codes | candidate: "
        "How DNS turns a domain into a server address; (3) existing: How DNS turns a domain into "
        "a server address | candidate: Why typing a web address starts a global lookup; (4) "
        "existing: How cookies remember a login | candidate: Why browsers cache images. Return one "
        "assessment per pair, in order.",
        DuplicateSet,
    ),
    "C_script": (
        "Write a 5-scene, 90-105-word beginner Short narration titled How DNS turns a domain "
        "name into a server address. Make the opening curiosity-driven, technically accurate, and "
        "suited to 30-40 seconds. Do not make fake claims. Put all narration only in scenes.",
        ScriptPlan,
    ),
    "D_scene_specs": (
        "Create renderable SceneSpecs for this five-scene DNS narration: (1) You type a domain, "
        "but how does it find the right computer? (2) Your device asks a DNS resolver. (3) If it "
        "doesn't know, the resolver follows the domain hierarchy. (4) The answer is an IP address. "
        "(5) Your browser can finally connect. Use short labels, not full narration. Use only the "
        "allowed visual types.",
        SceneSpecSet,
    ),
    "F_evidence_maturity": (
        CHANNEL_CONTEXT
        + "\nChoose how to decide the next topic. State exactly what cannot be concluded while "
        "Video #2 windows are pending. Prefer strategy, novelty, diversification and feasibility.",
        EvidenceDecision,
    ),
}


def _tokens_per_second(metadata: dict[str, object]) -> float | None:
    tokens, duration = metadata.get("eval_count"), metadata.get("eval_duration")
    if not isinstance(tokens, int) or not isinstance(duration, int) or duration <= 0:
        return None
    return round(tokens / (duration / 1_000_000_000), 2)


def _gpu_snapshot() -> str | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


async def _run_one(
    provider: OllamaLLM,
    task_name: str,
    prompt: str,
    schema: type[BaseModel],
) -> dict[str, object]:
    started = time.perf_counter()
    try:
        result = await provider.generate_structured(prompt, schema, system=SYSTEM)
    except StructuredOutputError as exc:
        return {
            "task": task_name,
            "ok": False,
            "error": str(exc),
            "raw_responses": exc.raw_responses,
            "latency_seconds": round(time.perf_counter() - started, 3),
            "gpu_after": _gpu_snapshot(),
        }
    except (RetryableError, TerminalError) as exc:
        return {
            "task": task_name,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_seconds": round(time.perf_counter() - started, 3),
            "gpu_after": _gpu_snapshot(),
        }
    return {
        "task": task_name,
        "ok": True,
        "attempts": result.attempts,
        "latency_seconds": round(time.perf_counter() - started, 3),
        "tokens_per_second": _tokens_per_second(result.raw.metadata),
        "provider_metadata": result.raw.as_metadata(),
        "value": result.value.model_dump(),
        "gpu_after": _gpu_snapshot(),
    }


async def evaluate_model(
    model: str,
    base_url: str,
    context_limit: int,
    strict_attempts: int,
    timeout: float,
) -> dict[str, object]:
    # Keep one model warm while its task group runs, then explicitly unload it.
    provider = OllamaLLM(
        base_url=base_url,
        model=model,
        context_limit=context_limit,
        timeout=timeout,
        max_attempts=2,
        keep_alive="2m",
        # Benchmarks measure the production-shaped JSON path. Disabling hidden
        # chain-of-thought keeps latency and structured output predictable.
        think=False,
    )
    report: dict[str, object] = {
        "model": model,
        "context_limit": context_limit,
        "timeout_seconds": timeout,
        "tasks": [],
        "strict_json": [],
    }
    try:
        warmup_started = time.perf_counter()
        warmup = await provider.generate_text("Reply with READY only.", system=SYSTEM)
        report["warmup_latency_seconds"] = round(time.perf_counter() - warmup_started, 3)
        report["warmup_metadata"] = warmup.as_metadata()

        tasks = report["tasks"]
        assert isinstance(tasks, list)
        for task_name, (prompt, schema) in PROMPTS.items():
            tasks.append(await _run_one(provider, task_name, prompt, schema))

        strict = report["strict_json"]
        assert isinstance(strict, list)
        strict_prompt = (
            "Classify this pair as duplicate or not: existing topic How websites store passwords; "
            "candidate Why databases do not save your password."
        )
        for attempt in range(1, strict_attempts + 1):
            item = await _run_one(
                provider,
                f"E_strict_json_{attempt}",
                strict_prompt,
                DuplicateAssessment,
            )
            strict.append(item)
    except RetryableError as exc:
        report["warmup_error"] = str(exc)
    finally:
        await asyncio.to_thread(
            subprocess.run,
            ["ollama", "stop", model],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        report["gpu_after_unload"] = _gpu_snapshot()
    return report


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--context-limit", type=int, default=8192)
    parser.add_argument("--strict-attempts", type=int, default=5)
    parser.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        help="Per-request benchmark timeout; app requests default to LLM_TIMEOUT (120 seconds).",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=["qwen3.5:9b", "ministral-3:8b", "qwen3.5:4b"],
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1024 <= args.context_limit <= 16384:
        raise SystemExit("--context-limit must be between 1024 and 16384")
    if args.strict_attempts < 5:
        raise SystemExit("--strict-attempts must be at least 5")

    report: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "purpose": "Phase 6A.0 local model evaluation; no planner state was created",
        "base_url": args.base_url,
        "models": [],
    }
    models = report["models"]
    assert isinstance(models, list)
    for model in args.models:
        models.append(
            await evaluate_model(
                model,
                args.base_url,
                args.context_limit,
                args.strict_attempts,
                args.timeout,
            )
        )

    output = args.output or Path("..") / ".poc" / "llm-evaluations" / (
        f"evaluation-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
