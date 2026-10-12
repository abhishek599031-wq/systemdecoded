"""Ollama implementation of the local LLM provider boundary.

This module intentionally owns all Ollama wire details.  Services receive a
small provider interface and Pydantic-validated data; they do not need to know
whether the local runtime exposes ``/api/generate`` or another compatible API.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError

from app.config import settings
from app.core.errors import RetryableError, TerminalError
from app.core.logging import get_logger
from app.providers.base import LLMResult, StructuredLLMResult, TStructured

log = get_logger("llm.ollama")


class StructuredOutputError(TerminalError):
    """The model did not produce data that the requested schema accepts."""

    def __init__(self, message: str, *, raw_responses: list[str]) -> None:
        self.raw_responses = raw_responses
        super().__init__(message)


class OllamaLLM:
    """Local-only Ollama provider with bounded schema repair retries."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        context_limit: int | None = None,
        timeout: float | None = None,
        max_attempts: int | None = None,
        keep_alive: str | None = None,
        think: bool | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or settings.OLLAMA_BASE_URL).rstrip("/")
        self.model = model or settings.PRIMARY_PLANNER_MODEL
        self.context_limit = context_limit or settings.LLM_CONTEXT_LIMIT
        self.timeout = timeout or settings.LLM_TIMEOUT
        self.max_attempts = max_attempts or settings.LLM_MAX_ATTEMPTS
        self.keep_alive = keep_alive if keep_alive is not None else settings.OLLAMA_KEEP_ALIVE
        self.think = think
        self._client = client

    async def generate_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | str | None = None,
    ) -> LLMResult:
        """Generate one complete response and retain Ollama's timing metadata."""
        selected_model = model or self.model
        payload: dict[str, Any] = {
            "model": selected_model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.context_limit},
        }
        if system:
            payload["system"] = system
        if response_format is not None:
            payload["format"] = response_format
        if self.think is not None:
            payload["think"] = self.think

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(self.timeout))
        try:
            try:
                response = await client.post(f"{self.base_url}/api/generate", json=payload)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise RetryableError(f"Ollama is unavailable at {self.base_url}: {exc}") from exc

            if response.status_code == 429 or response.status_code >= 500:
                raise RetryableError(
                    f"Ollama returned HTTP {response.status_code}: {response.text[:300]}"
                )
            if response.is_error:
                raise TerminalError(
                    "Ollama rejected the request with HTTP "
                    f"{response.status_code}: {response.text[:300]}"
                )

            try:
                body = response.json()
                text = body["response"]
            except (KeyError, TypeError, ValueError) as exc:
                raise TerminalError("Ollama returned a malformed generate response") from exc
            if not isinstance(text, str):
                raise TerminalError("Ollama returned a non-string response")

            metadata = {
                key: value
                for key, value in body.items()
                if key
                in {
                    "created_at",
                    "done_reason",
                    "total_duration",
                    "load_duration",
                    "prompt_eval_count",
                    "prompt_eval_duration",
                    "eval_count",
                    "eval_duration",
                }
            }
            metadata.update(
                {
                    "base_url": self.base_url,
                    "context_limit": self.context_limit,
                    "keep_alive": self.keep_alive,
                    "think": self.think,
                }
            )
            log.info(
                "llm.generated",
                provider=self.name,
                model=selected_model,
                eval_count=metadata.get("eval_count"),
                total_duration=metadata.get("total_duration"),
            )
            return LLMResult(text=text, provider=self.name, model=selected_model, metadata=metadata)
        finally:
            if owns_client:
                await client.aclose()

    async def generate_structured(
        self,
        prompt: str,
        schema: type[TStructured],
        *,
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredLLMResult[TStructured]:
        """Generate strict JSON, validate it, and make at most one repair retry."""
        raw_responses: list[str] = []
        current_prompt = prompt
        schema_json = schema.model_json_schema()
        for attempt in range(1, self.max_attempts + 1):
            result = await self.generate_text(
                current_prompt,
                system=system,
                model=model,
                response_format=schema_json,
            )
            raw_responses.append(result.text)
            try:
                value = schema.model_validate_json(result.text)
            except ValidationError as exc:
                log.warning(
                    "llm.structured_validation_failed",
                    provider=self.name,
                    model=result.model,
                    attempt=attempt,
                    of=self.max_attempts,
                    error_count=len(exc.errors()),
                )
                if attempt == self.max_attempts:
                    raise StructuredOutputError(
                        f"Ollama did not satisfy {schema.__name__} after {attempt} attempts",
                        raw_responses=raw_responses,
                    ) from exc
                current_prompt = (
                    f"{prompt}\n\nYour previous response was invalid for the required JSON schema. "
                    "Return only one valid JSON object; do not add markdown or explanation."
                )
                continue
            return StructuredLLMResult(value=value, raw=result, attempts=attempt)
        raise AssertionError("bounded structured retry loop unexpectedly ended")
