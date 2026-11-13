"""The one place chat models are constructed.

Agents ask for a role (`get_chat_model("judge")`), never for a model id. The role
resolves to a tier and settings through apps/ai/config/models.yaml, and the provider
named by AI_LLM_PROVIDER builds the model.
"""

from collections.abc import Callable

from django.conf import settings
from langchain_core.language_models.chat_models import BaseChatModel

from apps.ai.config.loader import ResolvedModel, resolve_role
from apps.ai.exceptions import AIConfigError

ProviderFactory = Callable[[ResolvedModel], BaseChatModel]

_PROVIDERS: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    _PROVIDERS[name] = factory


def registered_providers() -> frozenset[str]:
    return frozenset(_PROVIDERS)


def get_chat_model(role: str) -> BaseChatModel:
    return build_chat_model(resolve_role(role))


def build_chat_model(resolved: ResolvedModel) -> BaseChatModel:
    factory = _PROVIDERS.get(resolved.provider)
    if factory is None:
        known = ", ".join(sorted(_PROVIDERS))
        raise AIConfigError(
            f"Unknown AI_LLM_PROVIDER {resolved.provider!r}. Known providers: {known}."
        )
    return factory(resolved)


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


register_provider("groq", _build_groq)
