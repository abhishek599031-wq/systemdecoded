"""Unit coverage for the local-only Ollama provider boundary."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.core.errors import RetryableError
from app.providers.base import LLMResult, StructuredLLMResult
from app.providers.llm.ollama import OllamaLLM, StructuredOutputError
from app.providers.llm.resolver import ResilientLLM


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    duplicate: bool


def _client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler, base_url="http://ollama.test")


def _success(text: str, **extra: object) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "response": text,
            "model": "qwen3.5:9b",
            "total_duration": 12,
            "load_duration": 3,
            "eval_count": 4,
            "eval_duration": 2,
            **extra,
        },
    )


@pytest.mark.asyncio
async def test_text_generation_returns_provider_model_and_timing_metadata() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/api/generate"
        assert payload["model"] == "qwen3.5:9b"
        assert payload["options"] == {"num_ctx": 8192}
        assert payload["keep_alive"] == "0s"
        return _success("local answer")

    async with _client(httpx.MockTransport(handler)) as client:
        provider = OllamaLLM(
            base_url="http://ollama.test", model="qwen3.5:9b", client=client
        )
        result = await provider.generate_text("hello")

    assert result.text == "local answer"
    assert result.provider == "ollama"
    assert result.model == "qwen3.5:9b"
    assert result.as_metadata()["eval_count"] == 4
    assert result.as_metadata()["context_limit"] == 8192


@pytest.mark.asyncio
async def test_structured_generation_uses_pydantic_validation() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["format"]["additionalProperties"] is False
        return _success('{"topic":"DNS", "duplicate":false}')

    async with _client(httpx.MockTransport(handler)) as client:
        result = await OllamaLLM(base_url="http://ollama.test", client=client).generate_structured(
            "return JSON", Answer
        )

    assert result.value == Answer(topic="DNS", duplicate=False)
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_malformed_json_is_retried_once_and_then_validated() -> None:
    attempts = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return _success("not JSON" if attempts == 1 else '{"topic":"DNS","duplicate":true}')

    async with _client(httpx.MockTransport(handler)) as client:
        result = await OllamaLLM(
            base_url="http://ollama.test", max_attempts=2, client=client
        ).generate_structured("return JSON", Answer)

    assert attempts == 2
    assert result.attempts == 2
    assert result.value.duplicate is True


@pytest.mark.asyncio
async def test_malformed_json_fails_after_bounded_retries_without_coercion() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return _success('{"topic": 7}')

    async with _client(httpx.MockTransport(handler)) as client:
        provider = OllamaLLM(base_url="http://ollama.test", max_attempts=2, client=client)
        with pytest.raises(StructuredOutputError) as exc_info:
            await provider.generate_structured("return JSON", Answer)

    assert len(exc_info.value.raw_responses) == 2


@pytest.mark.asyncio
async def test_timeout_becomes_retryable_error() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow Ollama")

    async with _client(httpx.MockTransport(handler)) as client:
        provider = OllamaLLM(base_url="http://ollama.test", client=client)
        with pytest.raises(RetryableError, match="unavailable"):
            await provider.generate_text("hello")


class _RetryingProvider:
    name = "retrying"

    async def generate_text(self, prompt: str, **_: object) -> LLMResult:
        raise RetryableError("temporary")

    async def generate_structured(
        self, prompt: str, schema: type[Answer], **_: object
    ) -> StructuredLLMResult[Answer]:
        raise RetryableError("temporary")


class _WorkingProvider:
    name = "working"

    async def generate_text(self, prompt: str, **_: object) -> LLMResult:
        return LLMResult(text="fallback", provider=self.name, model="fallback-model")

    async def generate_structured(
        self, prompt: str, schema: type[Answer], **_: object
    ) -> StructuredLLMResult[Answer]:
        raw = LLMResult(text='{"topic":"DNS","duplicate":false}', provider=self.name, model="x")
        return StructuredLLMResult(value=Answer(topic="DNS", duplicate=False), raw=raw, attempts=1)


@pytest.mark.asyncio
async def test_transient_primary_failure_uses_configured_fallback() -> None:
    result = await ResilientLLM(_RetryingProvider(), _WorkingProvider()).generate_text("hello")
    assert result.provider == "working"


def test_ollama_configuration_is_local_and_needs_no_api_key() -> None:
    settings = Settings(
        LOCAL_LLM_PROVIDER="ollama",
        OLLAMA_BASE_URL="http://host.docker.internal:11434",
        PRIMARY_PLANNER_MODEL="qwen3.5:9b",
        FAST_UTILITY_MODEL="qwen3.5:4b",
        FALLBACK_MODEL="ministral-3:8b",
        LLM_CONTEXT_LIMIT=8192,
        LLM_TIMEOUT=120,
    )
    assert settings.LOCAL_LLM_PROVIDER == "ollama"
    assert settings.OLLAMA_BASE_URL.startswith("http://")
    assert not hasattr(settings, "OLLAMA_API_KEY")
