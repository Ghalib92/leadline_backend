"""Background jobs of the AI layer."""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail

from apps.common.jobs import job

logger = logging.getLogger(__name__)


@job("ai.check_models", timeout=timedelta(minutes=20), attempts=1)
def check_models_daily():
    """Every morning (a Temporal Schedule): one short call per provider, tier and key.
    Emails AI_ALERT_EMAILS when a key was rejected or a model is gone, so a backup
    isn't found broken only on the day it's needed."""
    from apps.ai.health import check_models

    checks = check_models()
    for check in checks:
        if not check.ok:
            logger.warning("Model check %s (%s) failed: %s", check.label, check.model, check.error)
    broken = [check for check in checks if check.broken]
    if not broken or not settings.AI_ALERT_EMAILS:
        return
    lines = [f"- {c.label}: {c.model}\n  {c.error}" for c in broken]
    send_mail(
        subject=f"{settings.PRODUCT_NAME}: {len(broken)} AI model check(s) need attention",
        message=(
            "The daily model check found keys or models that no longer work. Calls still "
            "go to the other options, but a backup is missing until this is fixed.\n\n"
            + "\n".join(lines)
            + "\n\nFix the key or the model id in the server's .env, then run "
            "`python manage.py ai_ping` to confirm."
        ),
        from_email=None,
        recipient_list=list(settings.AI_ALERT_EMAILS),
    )
