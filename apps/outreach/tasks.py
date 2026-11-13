"""Background jobs: finding contacts, drafting and sending outreach, deadline reminders."""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.ai.config.loader import load_outreach_file, resolve_role
from apps.ai.exceptions import AIConfigError, StructuredOutputError
from apps.ai.graphs.budget import Budget, BudgetExhausted
from apps.common.jobs import job

from . import selectors, services
from .choices import EmailStatus
from .models import OutreachEmail, ReminderLog

logger = logging.getLogger(__name__)
NOT_CONFIGURED = "The AI is not configured on the server yet."


@job("outreach.find_contacts")
def find_contacts(search_id):
    from apps.ai.graphs.lead_scout import find_contacts as scout

    search = services.claim_contact_search(search_id)
    if search is None:
        return
    opportunity = search.opportunity

    class _TaskEmitter:
        def emit(self, event):
            logger.info("[ContactSearch %s] [%s] %s", search_id, event.step, event.message)

    try:
        result = scout(
            title=opportunity.title,
            funder=opportunity.funder,
            source_url=opportunity.source_url,
            search=_web_search(),
            emitter=_TaskEmitter(),
        )
    except AIConfigError as exc:
        logger.error("Contact search %s cannot run: %s", search_id, exc)
        services.finish_contact_search(search_id, error=NOT_CONFIGURED)
        return
    except Exception:
        logger.exception("Contact search %s failed", search_id)
        services.finish_contact_search(search_id, error="Looking for contacts failed.")
        return
    services.finish_contact_search(search_id, result=result)


def _web_search():
    """Tavily when configured, else none (the call's own pages are still read)."""
    if not settings.TAVILY_API_KEY:
        return None
    from apps.ai.tools.search.tavily import TavilySearch

    return TavilySearch()


@job("outreach.write_email")
def write_email(email_id):
    from apps.ai.agents.emailer import draft_email
    from apps.ai.graphs.runtime import TaskRuntime

    email = (
        OutreachEmail.objects.select_related("opportunity", "organization", "contact")
        .filter(pk=email_id, status=EmailStatus.DRAFTING)
        .first()
    )
    if email is None:
        return
    started_at = email.drafting_started_at
    inputs = selectors.email_inputs(email)
    try:
        budget = Budget(load_outreach_file().emailer.budget.model_dump())
        runtime = TaskRuntime(budget=budget, **inputs["organization"])
        draft = draft_email(
            runtime,
            opportunity=inputs["opportunity"],
            contact=inputs["contact"],
            purpose=email.get_purpose_display(),
            instructions=email.instructions,
        )
    except AIConfigError as exc:
        logger.error("Outreach draft %s cannot run: %s", email_id, exc)
        services.fail_drafting(email_id, started_at, NOT_CONFIGURED)
        return
    except (StructuredOutputError, BudgetExhausted):
        services.fail_drafting(
            email_id,
            started_at,
            "The AI could not write this email. Try again or write it yourself.",
        )
        return
    except Exception:
        logger.exception("Outreach draft %s failed", email_id)
        services.fail_drafting(email_id, started_at, "Drafting failed unexpectedly.")
        return
    services.save_drafted_email(
        email_id,
        started_at,
        subject=draft.subject,
        body=draft.body,
        prompt_id=runtime.prompt_versions.get("emailer", ""),
        model=resolve_role("emailer").model,
    )


# Never retried: if a worker died after the mail server accepted the email, a retry would
# send it twice. A failed send is shown to the admin, who can send again.
@job("outreach.send_email", attempts=1)
def send_email(email_id):
    """Send an approved email, at most once (see services.claim_sending)."""
    with transaction.atomic():
        email = services.claim_sending(email_id)
        if email is None:
            return
        if email.contact is None or email.contact.do_not_contact:
            email.status, email.error = EmailStatus.FAILED, "The contact can no longer be emailed."
            email.save(update_fields=["status", "error", "updated_at"])
            return
        approver = email.approved_by
        sender = approver.get_full_name() if approver else ""
        from_name = f"{sender} at {email.organization.name}" if sender else email.organization.name
        from_address = _address(settings.DEFAULT_FROM_EMAIL)
        message = EmailMessage(
            subject=" ".join(email.subject.splitlines()),
            body=email.body,
            from_email=f'"{from_name} via {settings.PRODUCT_NAME}" <{from_address}>',
            to=[email.to_email],
            reply_to=[email.reply_to] if email.reply_to else None,
            bcc=[email.reply_to] if email.reply_to else None,  # the approver keeps a copy
        )
        try:
            message.send(fail_silently=False)
        except Exception as exc:
            logger.warning("Outreach email %s failed to send: %s", email_id, type(exc).__name__)
            email.status = EmailStatus.FAILED
            email.error = "The email could not be sent. Try again."
        else:
            email.status, email.sent_at = EmailStatus.SENT, timezone.now()
        email.save(update_fields=["status", "error", "sent_at", "updated_at"])


def _address(from_email: str) -> str:
    """The bare address of DEFAULT_FROM_EMAIL ("Name <a@b>" or "a@b")."""
    return from_email.rsplit("<", 1)[-1].rstrip(">").strip()


@job("outreach.send_deadline_reminders", timeout=timedelta(minutes=15))
def send_deadline_reminders():
    """Daily (a Temporal Schedule): remind members of approaching deadlines, each reminder
    once (ReminderLog is unique, so a retried run skips those already sent)."""
    from apps.common.emails import send_templated_email

    from .calendar import deadlines_ics

    today = timezone.localdate()
    for opportunity, user, days in list(selectors.reminders_due(today)):
        deadline = selectors.deadline_day(opportunity)
        try:
            with transaction.atomic():
                ReminderLog.objects.create(
                    opportunity=opportunity, user=user, days_before=days, deadline=deadline
                )
                send_templated_email(
                    to=user.email,
                    subject=f"{_when(days)}: {opportunity.title} | {settings.PRODUCT_NAME}",
                    template="outreach/emails/deadline_reminder",
                    context={
                        "name": user.get_short_name(),
                        "title": opportunity.title,
                        "funder": opportunity.funder,
                        "when": "tomorrow" if days == 1 else f"in {days} days",
                        "deadline": deadline,
                        "opportunity_url": selectors.opportunity_link(opportunity),
                        "source_url": opportunity.source_url,
                        "organization_name": opportunity.organization.name,
                    },
                    attachments=[
                        (
                            f"deadline-{opportunity.pk}.ics",
                            deadlines_ics([opportunity], name=opportunity.title),
                            "text/calendar",
                        )
                    ],
                )
        except IntegrityError:
            continue  # already sent (another worker, or a rerun)
        except Exception:
            logger.exception("Deadline reminder for %s failed", opportunity.pk)


def _when(days: int) -> str:
    return "Deadline tomorrow" if days == 1 else f"Deadline in {days} days"
