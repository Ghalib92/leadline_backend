"""Keys that reported their rate limit or daily cap are skipped for a while.

A provider that refuses for its limit usually says when to try again (Groq's
retry-after header, Gemini's retryDelay). Until then, a model that has a later option
(another key or provider) raises ProviderCooling without calling, so the next option
answers at once instead of after a wasted request. The last option is always called.

Kept in the shared cache (Redis in production), so the API and the worker agree.
"""

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache

from apps.ai.exceptions import ProviderCooling

logger = logging.getLogger(__name__)
DEFAULT_SECONDS = 60


def _cache_key(provider: str, api_key: str, model: str) -> str:
    digest = hashlib.sha256(f"{provider}\0{api_key}\0{model}".encode()).hexdigest()[:24]
    return f"ai:cooling:{digest}"


def skip_if_cooling(provider: str, api_key: str, model: str, label: str) -> None:
    try:
        cooling = cache.get(_cache_key(provider, api_key, model)) is not None
    except Exception:  # the cache being down never blocks a call
        return
    if cooling:
        raise ProviderCooling(f"{label} is resting after its rate limit ({model}).")


def cool(provider: str, api_key: str, model: str, label: str, seconds: float | None) -> None:
    """Skip this key for `seconds` (the provider's own wait, when it gave one)."""
    wait = int(min(max(seconds or DEFAULT_SECONDS, 1), settings.AI_COOLDOWN_MAX_SECONDS))
    try:
        cache.set(_cache_key(provider, api_key, model), 1, timeout=wait)
    except Exception:
        return
    logger.warning("%s hit its rate limit on %s; skipping it for %ss.", label, model, wait)
