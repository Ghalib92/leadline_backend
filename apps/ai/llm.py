"""The one place chat models are constructed.

Agents ask for a role (`get_chat_model("judge")`), never for a model id. The role
resolves to a tier and settings through apps/ai/config/models.yaml, and the provider
named by AI_LLM_PROVIDER builds the model.

With AI_FALLBACK_PROVIDER set (and a FALLBACK_<TIER>_MODEL for the tier), a call the
primary provider refuses for its rate limit, or can't answer (down, timed out), is
sent to the fallback model instead, so a run doesn't stop at a free tier's daily cap.
"""

import logging
from collections.abc import Callable

from django.conf import settings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables.fallbacks import RunnableWithFallbacks

from apps.ai.config.loader import ResolvedModel, resolve_role
from apps.ai.exceptions import AIConfigError

logger = logging.getLogger(__name__)
ProviderFactory = Callable[[ResolvedModel], BaseChatModel]

_PROVIDERS: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    _PROVIDERS[name] = factory


def registered_providers() -> frozenset[str]:
    return frozenset(_PROVIDERS)


def get_chat_model(role: str) -> BaseChatModel:
    return build_chat_model(resolve_role(role))


def build_chat_model(resolved: ResolvedModel) -> BaseChatModel:
    primary = _build(resolved)
    fallback = _fallback_for(resolved)
    if fallback is None:
        return primary
    return ChatWithFallback(
        runnable=primary, fallbacks=[fallback], exceptions_to_handle=_fallback_errors()
    )


def _build(resolved: ResolvedModel) -> BaseChatModel:
    factory = _PROVIDERS.get(resolved.provider)
    if factory is None:
        known = ", ".join(sorted(_PROVIDERS))
        raise AIConfigError(
            f"Unknown AI_LLM_PROVIDER {resolved.provider!r}. Known providers: {known}."
        )
    return factory(resolved)


def fallback_model(resolved: ResolvedModel) -> ResolvedModel | None:
    """The same role on the fallback provider, or None when there is none."""
    provider = settings.AI_FALLBACK_PROVIDER
    model = settings.AI_FALLBACK_TIERS.get(resolved.tier, "")
    if not provider or provider == resolved.provider or not model:
        return None
    return resolved.model_copy(update={"provider": provider, "model": model})


def _fallback_for(resolved: ResolvedModel) -> BaseChatModel | None:
    backup = fallback_model(resolved)
    if backup is None:
        return None
    try:
        return _build(backup)
    except AIConfigError as exc:  # a broken fallback never blocks the primary
        logger.warning("No fallback model for %s: %s", resolved.role, exc)
        return None


def _fallback_errors() -> tuple[type[BaseException], ...]:
    """What the primary may fail with that a fallback can fix: rate limits, outages,
    timeouts. A bad answer or a bad request is not among them."""
    import groq

    from apps.ai.gemini import GeminiError

    return (
        groq.RateLimitError,
        groq.APIConnectionError,
        groq.InternalServerError,
        GeminiError,
    )


class ChatWithFallback(RunnableWithFallbacks):
    """A primary chat model and its fallback that also supports structured output, the
    way agents call models (apps.ai.structured)."""

    def with_structured_output(self, schema, **kwargs):
        return self.runnable.with_structured_output(schema, **kwargs).with_fallbacks(
            [model.with_structured_output(schema, **kwargs) for model in self.fallbacks],
            exceptions_to_handle=self.exceptions_to_handle,
        )


def _build_groq(resolved: ResolvedModel) -> BaseChatModel:
    from langchain_groq import ChatGroq

    if not settings.GROQ_API_KEY:
        raise AIConfigError("GROQ_API_KEY is not set.")
    optional = {
        "temperature": resolved.temperature,
        "max_tokens": resolved.max_tokens,
        "reasoning_effort": resolved.reasoning_effort,
        "timeout": resolved.timeout_seconds,
        "max_retries": resolved.max_retries,
    }
    return ChatGroq(
        model=resolved.model,
        api_key=settings.GROQ_API_KEY,
        **{key: value for key, value in optional.items() if value is not None},
    )


# Gemini's thinking budget (tokens) for each reasoning effort in models.yaml.
GEMINI_THINKING = {"low": 512, "medium": 2048, "high": 6144}


def _build_gemini(resolved: ResolvedModel) -> BaseChatModel:
    from apps.ai.gemini import ChatGemini

    if not settings.GEMINI_API_KEY:
        raise AIConfigError("GEMINI_API_KEY is not set.")
    optional = {
        "temperature": resolved.temperature,
        "max_tokens": resolved.max_tokens,
        "thinking_budget": GEMINI_THINKING.get(resolved.reasoning_effort or ""),
        "timeout": resolved.timeout_seconds,
        "max_retries": resolved.max_retries,
    }
    return ChatGemini(
        model=resolved.model,
        api_key=settings.GEMINI_API_KEY,
        **{key: value for key, value in optional.items() if value is not None},
    )


register_provider("groq", _build_groq)
register_provider("gemini", _build_gemini)
