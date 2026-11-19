"""System checks for the AI configuration.

A broken models.yaml or unknown provider is an error. Missing model ids or API keys
are warnings, so the rest of the API still runs on machines without AI configured.
"""

from django.conf import settings
from django.core.checks import Error, Warning, register

from apps.ai.config.loader import has_keys, load_models_file, load_sources_file
from apps.ai.exceptions import AIConfigError
from apps.ai.llm import registered_providers


@register()
def ai_configuration(app_configs, **kwargs):
    try:
        models = load_models_file()
    except AIConfigError as exc:
        return [Error(str(exc), id="ai.E001")]
    try:
        load_sources_file()
    except AIConfigError as exc:
        return [Error(str(exc), id="ai.E003")]

    messages = []
    if settings.AI_LLM_PROVIDER not in registered_providers():
        messages.append(
            Error(
                f"AI_LLM_PROVIDER {settings.AI_LLM_PROVIDER!r} is not a registered provider.",
                hint=f"Use one of: {', '.join(sorted(registered_providers()))}.",
                id="ai.E002",
            )
        )
    for tier in models.tiers:
        if not settings.AI_MODEL_TIERS.get(tier):
            messages.append(
                Warning(
                    f"{tier.upper()}_MODEL is not set; agents on the {tier!r} tier cannot run.",
                    id="ai.W001",
                )
            )
    if not has_keys(settings.AI_LLM_PROVIDER):
        messages.append(
            Warning(
                f"{_key_name(settings.AI_LLM_PROVIDER)} is not set; agents cannot run.",
                id="ai.W002",
            )
        )
    messages += _fallback_checks(models)
    messages += _role_provider_checks(models)
    missing_default_key = any(message.id == "ai.W002" for message in messages)
    private = settings.AI_PRIVATE_DATA_PROVIDERS
    if not missing_default_key and not any(has_keys(name) for name in private):
        messages.append(
            Warning(
                "No provider in AI_PRIVATE_DATA_PROVIDERS has a key; roles that see users' "
                "private data (most of them) cannot run.",
                hint="Usually AI_PRIVATE_DATA_PROVIDERS=groq with GROQ_API_KEY set.",
                id="ai.W006",
            )
        )
    return messages


def _role_provider_checks(models) -> list:
    """A role naming a provider that isn't registered is an error (a typo in
    models.yaml); one without a key or model is fine (it's skipped until configured)."""
    unknown = {
        role: [name for name in spec.providers if name not in registered_providers()]
        for role, spec in models.roles.items()
    }
    return [
        Error(
            f"models.yaml: role {role!r} lists unknown provider(s) {', '.join(names)}.",
            hint=f"Use one of: {', '.join(sorted(registered_providers()))}.",
            id="ai.E004",
        )
        for role, names in unknown.items()
        if names
    ]


def _fallback_checks(models) -> list:
    provider = settings.AI_FALLBACK_PROVIDER
    if not provider:
        return []
    if provider not in registered_providers():
        return [
            Warning(
                f"AI_FALLBACK_PROVIDER {provider!r} is not a registered provider; "
                "there is no fallback.",
                hint=f"Use one of: {', '.join(sorted(registered_providers()))}, or leave it empty.",
                id="ai.W003",
            )
        ]
    messages = []
    if not has_keys(provider):
        messages.append(
            Warning(f"{_key_name(provider)} is not set; there is no fallback.", id="ai.W004")
        )
    missing = [tier for tier in models.tiers if not settings.AI_FALLBACK_TIERS.get(tier)]
    if missing:
        messages.append(
            Warning(
                "No fallback model for the "
                + ", ".join(repr(tier) for tier in missing)
                + " tier(s); set FALLBACK_<TIER>_MODEL.",
                id="ai.W005",
            )
        )
    return messages


def _key_name(provider: str) -> str:
    names = settings.AI_PROVIDER_KEYS.get(provider) or [f"{provider.upper()}_API_KEY"]
    return names[0]
