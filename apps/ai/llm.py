"""The one place chat models are constructed.

Agents ask for a role (`get_chat_model("judge")`), never for a model id. The role
resolves through apps/ai/config/models.yaml to a tier and an ordered list of options,
one (provider, model) per provider: the role's preferred providers first, then the
default order (AI_LLM_PROVIDER, AI_FALLBACK_PROVIDER). Providers without a model for
the tier or without a key are left out.

The built model tries the options in order. A call one option can't answer (its rate
limit or daily cap, a rejected key, an outage, a timeout, a request too large for the
plan) is sent to the next. A bad answer or another bad request is not: the same model
is asked again with a correction (apps.ai.structured).

Inside each option, the provider's backup keys come first (GROQ_API_KEY_2,
GEMINI_API_KEY_2): a call the first key can't make for its rate limit, or because the
key was rejected, is made with the next key on the same model. An outage or a request
too large skips the backup key, which would fail the same way.

Only the last model of the chain retries; the others fail fast, and a key that reported
its limit is skipped until it resets (apps.ai.cooldown).
"""

import logging
from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables.fallbacks import RunnableWithFallbacks

from apps.ai.config.loader import ResolvedModel, provider_keys, resolve_role
from apps.ai.exceptions import AIConfigError, KeyRejected, ProviderCooling, RequestTooLarge

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
    """Every usable option of the role, in order. An option that can't be built (no
    key, unknown provider) is skipped; when none can, the first one's error is raised."""
    options = resolved.options or ((resolved.provider, resolved.model),)
    built, errors = [], []
    # From the last option back: the last one built has no backup, so it retries as
    # the role says and is always called; every earlier one fails fast.
    for provider, model in reversed(options):
        option = resolved.model_copy(
            update={"provider": provider, "model": model, "has_backup": bool(built)}
        )
        try:
            built.insert(0, _build(option))
        except AIConfigError as exc:
            errors.insert(0, exc)
            logger.warning("%s on %s is skipped for %s: %s", model, provider, resolved.role, exc)
    if not built:
        raise errors[0]
    return chain(built, _fallback_errors())


def chain(models: list, errors: tuple[type[BaseException], ...]):
    """The first model, handing a call to the next on `errors`."""
    if len(models) == 1:
        return models[0]
    return ChatWithFallback(runnable=models[0], fallbacks=models[1:], exceptions_to_handle=errors)


def _build(resolved: ResolvedModel) -> BaseChatModel:
    factory = _PROVIDERS.get(resolved.provider)
    if factory is None:
        known = ", ".join(sorted(_PROVIDERS))
        raise AIConfigError(f"Unknown AI provider {resolved.provider!r}. Known providers: {known}.")
    return factory(resolved)


def _fallback_errors() -> tuple[type[BaseException], ...]:
    """What one option may fail with that the next can fix: rate limits, rejected keys,
    requests too large for the plan, outages, timeouts."""
    import groq

    from apps.ai.gemini import GeminiError

    return (
        groq.RateLimitError,
        groq.AuthenticationError,
        groq.PermissionDeniedError,
        groq.APIConnectionError,
        groq.InternalServerError,
        RequestTooLarge,
        KeyRejected,
        ProviderCooling,
        GeminiError,
    )


class ChatWithFallback(RunnableWithFallbacks):
    """A chat model and its fallbacks that also supports structured output, the way
    agents call models (apps.ai.structured)."""

    def with_structured_output(self, schema, **kwargs):
        return self.runnable.with_structured_output(schema, **kwargs).with_fallbacks(
            [model.with_structured_output(schema, **kwargs) for model in self.fallbacks],
            exceptions_to_handle=self.exceptions_to_handle,
        )


def _keys(resolved: ResolvedModel, env_name: str) -> list[tuple[str, str]]:
    keys = provider_keys(resolved.provider, only=resolved.key_names)
    if not keys:
        raise AIConfigError(f"{env_name} is not set.")
    return keys


def _key_options(resolved: ResolvedModel, keys: list) -> list[tuple[str, str, dict]]:
    """(key name, key, settings) per key: every key but the last of the last option has
    a backup, so it fails fast instead of retrying."""
    options = []
    for index, (name, key) in enumerate(keys):
        has_backup = resolved.has_backup or index < len(keys) - 1
        retries = 0 if has_backup else resolved.max_retries
        options.append((name, key, {"has_backup": has_backup, "max_retries": retries}))
    return options


def _settings(**values) -> dict:
    return {key: value for key, value in values.items() if value is not None}


def _build_groq(resolved: ResolvedModel) -> BaseChatModel:
    import groq

    from apps.ai.groq_chat import LeadlineChatGroq

    models = [
        LeadlineChatGroq(
            model=resolved.model,
            api_key=key,
            key_name=name,
            **_settings(
                temperature=resolved.temperature,
                max_tokens=resolved.max_tokens,
                reasoning_effort=resolved.reasoning_effort,
                timeout=resolved.timeout_seconds,
                **extra,
            ),
        )
        for name, key, extra in _key_options(resolved, _keys(resolved, "GROQ_API_KEY"))
    ]
    key_errors = (groq.RateLimitError, groq.AuthenticationError, groq.PermissionDeniedError)
    return chain(models, (*key_errors, ProviderCooling))


# Gemini's thinking budget (tokens) for each reasoning effort in models.yaml.
GEMINI_THINKING = {"low": 512, "medium": 2048, "high": 6144}


def _build_gemini(resolved: ResolvedModel) -> BaseChatModel:
    from apps.ai.gemini import ChatGemini, GeminiRateLimited

    models = [
        ChatGemini(
            model=resolved.model,
            api_key=key,
            key_name=name,
            **_settings(
                temperature=resolved.temperature,
                max_tokens=resolved.max_tokens,
                thinking_budget=GEMINI_THINKING.get(resolved.reasoning_effort or ""),
                timeout=resolved.timeout_seconds,
                **extra,
            ),
        )
        for name, key, extra in _key_options(resolved, _keys(resolved, "GEMINI_API_KEY"))
    ]
    return chain(models, (GeminiRateLimited, KeyRejected, ProviderCooling))


register_provider("groq", _build_groq)
register_provider("gemini", _build_gemini)
