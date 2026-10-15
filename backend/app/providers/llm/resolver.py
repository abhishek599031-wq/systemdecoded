"""Configuration-based local LLM selection and conservative fallback."""

from __future__ import annotations

from app.config import settings
from app.core.errors import RetryableError
from app.providers.base import LLMProvider, LLMResult, StructuredLLMResult, TStructured
from app.providers.llm.ollama import OllamaLLM


def build_provider(*, model: str) -> LLMProvider:
    if settings.LOCAL_LLM_PROVIDER == "ollama":
        return OllamaLLM(model=model)
    raise AssertionError(f"Unsupported local LLM provider: {settings.LOCAL_LLM_PROVIDER}")


class ResilientLLM:
    """Use a configured fallback model only for transient runtime failures."""

    name = "resilient-local-llm"

    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback

    async def generate_text(self, prompt: str, **kwargs: object) -> LLMResult:
        try:
            return await self.primary.generate_text(prompt, **kwargs)  # type: ignore[arg-type]
        except RetryableError:
            if self.fallback is None:
                raise
            return await self.fallback.generate_text(prompt, **kwargs)  # type: ignore[arg-type]

    async def generate_structured(
        self, prompt: str, schema: type[TStructured], **kwargs: object
    ) -> StructuredLLMResult[TStructured]:
        try:
            return await self.primary.generate_structured(prompt, schema, **kwargs)  # type: ignore[arg-type]
        except RetryableError:
            if self.fallback is None:
                raise
            return await self.fallback.generate_structured(prompt, schema, **kwargs)  # type: ignore[arg-type]


def get_planner_provider() -> ResilientLLM:
    # Planning requires structured, user-visible facts rather than hidden
    # chain-of-thought.  Disabling model thinking matches the evaluated local
    # configuration and prevents it from spending the job budget on it.
    def planner_model(model: str) -> LLMProvider:
        if settings.LOCAL_LLM_PROVIDER == "ollama":
            return OllamaLLM(model=model, think=False)
        return build_provider(model=model)

    fallback = (
        planner_model(settings.FALLBACK_MODEL)
        if settings.FALLBACK_MODEL != settings.PRIMARY_PLANNER_MODEL
        else None
    )
    return ResilientLLM(planner_model(settings.PRIMARY_PLANNER_MODEL), fallback)


def get_fast_utility_provider() -> LLMProvider:
    return build_provider(model=settings.FAST_UTILITY_MODEL)
