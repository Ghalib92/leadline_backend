from django.conf import settings
from django.core.checks import Error, register

from apps.ai.config.loader import load_proposals_file
from apps.ai.exceptions import AIConfigError
from apps.ai.graphs.proposal_runner import registered_writers


@register()
def proposal_configuration(app_configs, **kwargs):
    messages = []
    if settings.PROPOSAL_RUNNER not in registered_writers():
        messages.append(
            Error(
                f"PROPOSAL_RUNNER {settings.PROPOSAL_RUNNER!r} is not a registered writer.",
                hint=f"Use one of: {', '.join(sorted(registered_writers()))}.",
                id="proposals.E001",
            )
        )
    if settings.PROPOSAL_FAKE_STEP_SECONDS < 0:
        messages.append(
            Error("PROPOSAL_FAKE_STEP_SECONDS cannot be negative.", id="proposals.E002")
        )
    try:
        config = load_proposals_file()
    except AIConfigError as exc:
        return [*messages, Error(str(exc), id="proposals.E003")]
    wall_minutes = config.budget.max_wall_seconds / 60
    if settings.PROPOSAL_STALE_MINUTES <= wall_minutes:
        messages.append(
            Error(
                "PROPOSAL_STALE_MINUTES must be longer than the wall-time budget in "
                f"apps/ai/config/proposals.yaml ({wall_minutes:g} minutes).",
                id="proposals.E004",
            )
        )
    return messages
