"""System checks for the AI configuration.

A broken models.yaml or unknown provider is an error. Missing model ids or API keys
are warnings, so the rest of the API still runs on machines without AI configured.
"""

from django.conf import settings
from django.core.checks import Error, Warning, register

from apps.ai.config.loader import load_models_file, load_sources_file
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
    if settings.AI_LLM_PROVIDER == "groq" and not settings.GROQ_API_KEY:
        messages.append(Warning("GROQ_API_KEY is not set; agents cannot run.", id="ai.W002"))
    messages += _fallback_checks(models)
    return messages


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
    if provider == "gemini" and not settings.GEMINI_API_KEY:
        messages.append(Warning("GEMINI_API_KEY is not set; there is no fallback.", id="ai.W004"))
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
