"""System checks for leads, outreach and deadline reminders."""

from django.conf import settings
from django.core.checks import Error, register

from apps.ai.config.loader import load_outreach_file
from apps.ai.exceptions import AIConfigError

MAX_REMINDER_DAYS = 60


@register()
def outreach_configuration(app_configs, **kwargs):
    messages = []
    if settings.OUTREACH_DAILY_SEND_LIMIT < 1:
        messages.append(Error("OUTREACH_DAILY_SEND_LIMIT must be at least 1.", id="outreach.E001"))
    if settings.OUTREACH_REPEAT_DAYS < 0:
        messages.append(Error("OUTREACH_REPEAT_DAYS cannot be negative.", id="outreach.E002"))
    days = list(settings.DEADLINE_REMINDER_DAYS)
    if not days or any(not 1 <= day <= MAX_REMINDER_DAYS for day in days):
        messages.append(
            Error(
                f"DEADLINE_REMINDER_DAYS must list days between 1 and {MAX_REMINDER_DAYS}, "
                "e.g. 14,3,1.",
                id="outreach.E003",
            )
        )
    if settings.SCHEDULED_RUNS_ENABLED and settings.DISCOVERY_SCHEDULE_BATCH < 1:
        messages.append(Error("DISCOVERY_SCHEDULE_BATCH must be at least 1.", id="outreach.E004"))
    try:
        config = load_outreach_file()
    except AIConfigError as exc:
        return [*messages, Error(str(exc), id="outreach.E005")]
    longest = max(config.lead_scout.budget.max_wall_seconds, config.emailer.budget.max_wall_seconds)
    if settings.OUTREACH_JOB_STALE_MINUTES * 60 <= longest:
        messages.append(
            Error(
                "OUTREACH_JOB_STALE_MINUTES must be longer than the wall-time budgets in "
                f"apps/ai/config/outreach.yaml ({longest} seconds).",
                id="outreach.E006",
            )
        )
    return messages
