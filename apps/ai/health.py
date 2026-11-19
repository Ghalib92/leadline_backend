"""Is every model reachable? One short call per provider, tier and key.

Used by `manage.py ai_ping` and by the daily check (apps.ai.tasks), which emails
AI_ALERT_EMAILS when something needs a person: a key was rejected, or a model was
retired or renamed. A rate limit or an outage at check time is reported, not alerted:
it passes on its own.
"""

import time
from dataclasses import dataclass

from django.conf import settings

from apps.ai.config.loader import (
    load_models_file,
    model_for,
    provider_keys,
    provider_order,
    resolve_tier,
)
from apps.ai.exceptions import AIConfigError
from apps.ai.llm import build_chat_model, registered_providers
from apps.ai.prompts import load_prompt

NEEDS_A_PERSON = {400, 401, 403, 404}  # bad key, retired or unknown model


@dataclass(frozen=True)
class ModelCheck:
    provider: str
    tier: str
    key_name: str  # the key's setting name, never the key
    model: str
    seconds: float = 0.0
    reply: str = ""
    error: str = ""
    broken: bool = False  # a person must act (not a passing rate limit or outage)

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def label(self) -> str:
        return f"{self.tier}/{self.provider}/{self.key_name or '-'}"


def providers_in_use() -> list[str]:
    """The default order, then every provider a role names, if registered."""
    names = provider_order()
    if settings.AI_ROLE_PROVIDERS:
        for spec in load_models_file().roles.values():
            names += [name for name in spec.providers if name not in names]
    return [name for name in names if name in registered_providers()]


def check_models(tiers=None) -> list[ModelCheck]:
    prompt = load_prompt("ping").render()
    checks = []
    for tier in tiers or load_models_file().tiers:
        base = resolve_tier(tier)
        for provider in providers_in_use():
            model = model_for(provider, tier)
            if not model:
                continue
            if provider in settings.AI_PROVIDER_KEYS:
                key_names = [name for name, _ in provider_keys(provider)]
            else:
                key_names = [""]  # a provider without keys (test fakes)
            for key_name in key_names:
                one = base.model_copy(
                    update={
                        "provider": provider,
                        "model": model,
                        "options": ((provider, model),),
                        "key_names": (key_name,) if key_name else (),
                    }
                )
                checks.append(_ping(one, key_name, prompt))
    return checks


def _ping(resolved, key_name: str, prompt: str) -> ModelCheck:
    started = time.monotonic()
    common = {
        "provider": resolved.provider,
        "tier": resolved.tier,
        "key_name": key_name,
        "model": resolved.model,
    }
    try:
        reply = build_chat_model(resolved).invoke(prompt)
    except Exception as exc:  # diagnostics: report every failure, keep going
        status = getattr(exc, "status_code", None)
        return ModelCheck(
            **common,
            seconds=time.monotonic() - started,
            error=f"{type(exc).__name__}: {str(exc)[:300]}",
            broken=isinstance(exc, AIConfigError) or status in NEEDS_A_PERSON,
        )
    text = str(reply.content).strip().replace("\n", " ")[:80]
    return ModelCheck(**common, seconds=time.monotonic() - started, reply=text)
