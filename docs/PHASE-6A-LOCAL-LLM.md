# Phase 6A.0 — local LLM readiness

Phase 6A.0 adds a local inference boundary and an explicit evaluation harness.
It does **not** add the Phase 6A planner, planner database models, content
proposals, Video #3, research, scripts, or production work.

## Why Ollama

Ollama provides a local API, runs the requested models on the Windows host, and
does not require a paid API or an API key. The backend calls it through one
provider boundary (`app.providers.llm`); services should never contain Ollama
HTTP or JSON parsing logic. Docker reaches the host using
`http://host.docker.internal:11434`; direct host development normally uses
`http://127.0.0.1:11434`.

Keep the runtime local. Do not set `OLLAMA_HOST` to a public interface and do
not publish port 11434 through Docker.

## Hardware and resource policy

The supported development target is an i7-14650HX, 16 GB RAM and an RTX 4060
Laptop GPU with 8 GB VRAM. The evaluated candidates use an 8,192-token context
limit, not their much larger advertised maximums. Only one model is loaded at a
time. Application requests use `OLLAMA_KEEP_ALIVE=0s`, which unloads the model
after each response.

Recommended workflow:

1. Planning/evaluation: start Ollama and let the selected LLM use the GPU.
2. Before production: ensure `ollama ps` is empty (or run `ollama stop <model>`).
3. Production: load Chatterbox, generate narration, then use Motion Canvas and
   FFmpeg. Do not keep an LLM resident alongside Chatterbox.

## Model roles

The evaluation harness tests these exact official Ollama tags, sequentially:

- `qwen3.5:9b` — primary planner candidate.
- `ministral-3:8b` — structured-output/planner challenger.
- `qwen3.5:4b` — fast utility candidate.

Benchmark results selected `ministral-3:8b` as the primary planner model,
`qwen3.5:4b` as the fast utility model, and `qwen3.5:9b` as the fallback. They
are configured with `PRIMARY_PLANNER_MODEL`, `FAST_UTILITY_MODEL`, and
`FALLBACK_MODEL`. See the result summary below for the trade-offs.

## Provider guarantees

`OllamaLLM` supports text and structured generation. Structured output is sent
with the Pydantic-generated JSON schema, parsed through Pydantic, and retried
at most once if invalid. Raw invalid responses are retained in the raised
`StructuredOutputError`; the provider never coerces malformed output into
application state. Returned metadata includes provider, model, Ollama timing,
context limit, and keep-alive policy for a future planner record.

A fallback model is used only for transient runtime errors, not bad schemas or
other terminal failures. Phase 6A must persist the returned metadata alongside
its eventual planner run; Phase 6A.0 intentionally adds no such database model.

## Local commands

```powershell
ollama --version
ollama pull qwen3.5:9b
ollama pull ministral-3:8b
ollama pull qwen3.5:4b
ollama list
ollama ps
```

Run the explicit development evaluation from `backend` on the Windows host:

```powershell
$env:DEBUG = "true" # only needed if a host-level DEBUG variable is non-boolean
.venv\Scripts\python.exe scripts\evaluate_local_llms.py `
  --base-url http://127.0.0.1:11434 --context-limit 8192
```

It writes raw output and timing evidence to the ignored
`.poc/llm-evaluations/` directory. The harness runs the real Phase 6A.0 task
set (ideas, semantic duplicates, DNS script, SceneSpecs, five strict JSON
attempts, and immature-evidence discipline), then stops each model before
loading the next one.

## Configuration

```dotenv
LOCAL_LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://host.docker.internal:11434
PRIMARY_PLANNER_MODEL=ministral-3:8b
FAST_UTILITY_MODEL=qwen3.5:4b
FALLBACK_MODEL=qwen3.5:9b
LLM_CONTEXT_LIMIT=8192
LLM_TIMEOUT=120
LLM_MAX_ATTEMPTS=3
OLLAMA_KEEP_ALIVE=0s
```

No local LLM API key exists or is required.

## 2026-10-12 evaluation result

The raw run is deliberately kept in ignored local POC storage:
`.poc/llm-evaluations/evaluation-20261012T113329Z.json`. It used an 8,192-token
context, `think=false` for production-shaped strict JSON, one model at a time,
and five repeated strict-JSON attempts per model.

| Candidate | Result | Selection rationale |
| --- | --- | --- |
| `ministral-3:8b` | 5/5 strict JSON first pass; 3/4 duplicate cases correct; correctly classified evidence as `LIMITED`; about 25-27 generated tokens/sec | **Primary planner.** Its factual/evidence discipline and duplicate detection were strongest, even though its DNS script was over-length and needs the future planner's human review gate. |
| `qwen3.5:4b` | 5/5 strict JSON first pass; generated the only schema-valid 90-105 word DNS script; about 42-53 generated tokens/sec | **Fast utility.** It is materially faster and schema-reliable, but its duplicate detection and SceneSpec variety were weak, so it is not the strategic planner. |
| `qwen3.5:9b` | 5/5 strict JSON first pass; about 23-29 generated tokens/sec; good candidate variety and SceneSpec sequence | **Fallback.** It is a usable larger alternative, but mislabeled evidence maturity as `MATURE` and made poor duplicate decisions in this run. |

This is a practical engineering evaluation, not a scientifically precise
benchmark. All three candidates produced schema-valid SceneSpecs and passed the
repeated JSON exercise. Both 8B/9B candidates failed the word-bounded DNS
script after the one permitted repair attempt; this is an intentional signal for
the Phase 6A validation/review gate, not a reason to silently accept bad copy.
