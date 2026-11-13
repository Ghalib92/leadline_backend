"""System checks for discovery runs."""

from django.conf import settings
from django.core.checks import Error, Warning, register

from apps.ai.config.loader import load_sources_file
from apps.ai.exceptions import AIConfigError
from apps.ai.graphs.discovery_runner import registered_runners
from apps.ai.tools.sources.registry import KNOWN_ADAPTERS


@register()
def discovery_configuration(app_configs, **kwargs):
    messages = []
    if settings.DISCOVERY_RUNNER not in registered_runners():
        messages.append(
            Error(
                f"DISCOVERY_RUNNER {settings.DISCOVERY_RUNNER!r} is not a registered runner.",
                hint=f"Use one of: {', '.join(sorted(registered_runners()))}.",
                id="discovery.E001",
            )
        )
    elif settings.DISCOVERY_RUNNER == "fake" and not settings.DEBUG:
        messages.append(
            Warning(
                "DISCOVERY_RUNNER is 'fake': runs produce sample opportunities, not real ones.",
                id="discovery.W001",
            )
        )
    if settings.DISCOVERY_FAKE_STEP_SECONDS < 0:
        messages.append(
            Error("DISCOVERY_FAKE_STEP_SECONDS cannot be negative.", id="discovery.E002")
        )
    messages += _job_backend_checks()
    try:
        sources = load_sources_file()
    except AIConfigError:
        return messages  # reported by the ai checks
    unknown = {
        name: spec.adapter
        for name, spec in sources.sources.items()
        if spec.adapter not in KNOWN_ADAPTERS
    }
    if unknown:
        messages.append(
            Error(f"sources.yaml uses unknown adapters: {unknown}.", id="discovery.E004")
        )
    uses_tavily = any(
        spec.adapter == "tavily" and spec.enabled for spec in sources.sources.values()
    )
    if settings.DISCOVERY_RUNNER == "graph" and uses_tavily and not settings.TAVILY_API_KEY:
        messages.append(
            Warning(
                "TAVILY_API_KEY is not set: web and news search are skipped in discovery runs.",
                id="discovery.W002",
            )
        )
    if settings.TAVILY_REQUESTS_PER_MINUTE < 1:
        messages.append(
            Error("TAVILY_REQUESTS_PER_MINUTE must be at least 1.", id="discovery.E005")
        )
    if settings.LANGSMITH_TRACING and not settings.LANGSMITH_API_KEY:
        messages.append(
            Warning(
                "LANGSMITH_TRACING is on but LANGSMITH_API_KEY is empty; tracing stays off.",
                id="discovery.W003",
            )
        )
    wall_minutes = max(p.max_wall_seconds for p in sources.discovery.depths.values()) / 60
    if settings.DISCOVERY_STALE_RUN_MINUTES <= wall_minutes:
        messages.append(
            Error(
                "DISCOVERY_STALE_RUN_MINUTES must be longer than the wall-time budget in "
                f"apps/ai/config/sources.yaml ({wall_minutes:g} minutes), or live runs would "
                "be cancelled.",
                id="discovery.E003",
            )
        )
    return messages


def _job_backend_checks():
    messages = []
    at_least_one = {
        "DISCOVERY_DISPATCH_ATTEMPTS": settings.DISCOVERY_DISPATCH_ATTEMPTS,
        "DISCOVERY_RUN_ATTEMPTS": settings.DISCOVERY_RUN_ATTEMPTS,
        "TEMPORAL_MAX_CONCURRENT_ACTIVITIES": settings.TEMPORAL_MAX_CONCURRENT_ACTIVITIES,
    }
    for name, value in at_least_one.items():
        if value < 1:
            messages.append(Error(f"{name} must be at least 1.", id="discovery.E007"))
    if not (
        settings.TEMPORAL_ADDRESS and settings.TEMPORAL_NAMESPACE and settings.TEMPORAL_TASK_QUEUE
    ):
        messages.append(
            Error(
                "TEMPORAL_ADDRESS, TEMPORAL_NAMESPACE and TEMPORAL_TASK_QUEUE must be set.",
                id="discovery.E008",
            )
        )
    if settings.JOBS_RUN_INLINE and not settings.DEBUG:
        messages.append(
            Warning(
                "JOBS_RUN_INLINE is on: background jobs run inside web requests. "
                "It is meant for tests.",
                id="discovery.W004",
            )
        )
    return messages
