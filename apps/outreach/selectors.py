"""Reads for contacts, outreach and deadlines, always scoped to one organization."""

from datetime import datetime, time, timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.discovery.selectors import dossier_content, profile_context
from apps.opportunities.choices import ACTIVE_PIPELINE_STATUSES, ReviewDecision
from apps.opportunities.models import Opportunity
from apps.organizations.models import OrganizationMembership

from .models import OutreachEmail, ReminderLog, ReminderPreference


def contacts_for(opportunity):
    return opportunity.contacts.all()


def latest_contact_search(opportunity):
    return opportunity.contact_searches.order_by("-created_at", "-id").first()


def outreach_for(organization, *, statuses=(), opportunity_id=None):
    emails = OutreachEmail.objects.filter(organization=organization).select_related(
        "opportunity", "approved_by", "created_by"
    )
    if statuses:
        emails = emails.filter(status__in=statuses)
    if opportunity_id:
        emails = emails.filter(opportunity_id=opportunity_id)
    return emails


def deadline_day(opportunity):
    """The deadline's date in the server's time zone (East Africa)."""
    return timezone.localtime(opportunity.deadline).date()


def deadlines_for(organization, *, start, end, include_saved=True):
    """Opportunities the organization is working on (or saved) with a deadline in range."""
    wanted = Q(status__in=ACTIVE_PIPELINE_STATUSES)
    if include_saved:
        wanted |= Q(saved=True, status__in=("new", "reviewing"))
    return (
        Opportunity.objects.filter(organization=organization, deadline__gte=start, deadline__lt=end)
        .filter(wanted)
        .order_by("deadline", "id")
    )


def opportunity_link(opportunity) -> str:
    return (
        f"{settings.FRONTEND_URL.rstrip('/')}/o/{opportunity.organization.slug}"
        f"/opportunities/{opportunity.pk}"
    )


# ---------------------------------------------------------------------------
# Deadline reminders
# ---------------------------------------------------------------------------


def reminder_preference(organization, user) -> dict:
    """The member's settings, or the defaults when they never changed them."""
    preference = ReminderPreference.objects.filter(organization=organization, user=user).first()
    if preference is None:
        return {"enabled": True, "days_before": list(settings.DEADLINE_REMINDER_DAYS)}
    return {"enabled": preference.enabled, "days_before": list(preference.days_before)}


def reminder_recipients(opportunity) -> list:
    """Active admins, plus whoever approved it (they own the application)."""
    admins = opportunity.organization.memberships.filter(
        status=OrganizationMembership.Status.ACTIVE, role=OrganizationMembership.Role.ADMIN
    ).select_related("user")
    users = {membership.user for membership in admins}
    approval = (
        opportunity.reviews.filter(decision=ReviewDecision.APPROVE)
        .select_related("reviewer")
        .first()
    )
    if (
        approval
        and approval.reviewer
        and opportunity.organization.memberships.filter(
            user=approval.reviewer, status=OrganizationMembership.Status.ACTIVE
        ).exists()
    ):
        users.add(approval.reviewer)
    return sorted((user for user in users if user.is_active), key=lambda user: user.email)


def reminders_due(today):
    """(opportunity, user, days_before) for each reminder to send today and not sent yet:
    opportunities being pursued or submitted, in organizations that are not archived."""
    days = set(settings.DEADLINE_REMINDER_DAYS)
    days.update(
        day
        for chosen in ReminderPreference.objects.values_list("days_before", flat=True)
        for day in chosen
    )
    if not days:
        return
    start = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min))
    end = start + timedelta(days=max(days))
    opportunities = (
        Opportunity.objects.filter(
            status__in=ACTIVE_PIPELINE_STATUSES,
            deadline__gte=start,
            deadline__lt=end,
            organization__archived_at__isnull=True,
        )
        .select_related("organization")
        .order_by("deadline", "id")
    )
    for opportunity in opportunities:
        days_left = (deadline_day(opportunity) - today).days
        for user in reminder_recipients(opportunity):
            preference = reminder_preference(opportunity.organization, user)
            if not preference["enabled"] or days_left not in preference["days_before"]:
                continue
            already = ReminderLog.objects.filter(
                opportunity=opportunity,
                user=user,
                deadline=deadline_day(opportunity),
                days_before=days_left,
            ).exists()
            if not already:
                yield opportunity, user, days_left


# ---------------------------------------------------------------------------
# What the agents see
# ---------------------------------------------------------------------------


def _opportunity_brief(opportunity) -> dict:
    return {
        "title": opportunity.title,
        "funder": opportunity.funder,
        "summary": opportunity.summary,
        "eligibility": opportunity.eligibility,
        "deadline": deadline_day(opportunity).isoformat() if opportunity.deadline else "",
        "source_url": opportunity.source_url,
    }


def email_inputs(email) -> dict:
    """The email agent's inputs: the organization (for its runtime), the opportunity and
    the contact."""
    organization = email.organization
    return {
        "organization": {
            "organization_name": organization.name,
            "profile": profile_context(organization),
            "dossier": dossier_content(organization),
        },
        "opportunity": _opportunity_brief(email.opportunity),
        "contact": {"name": email.to_name, "role": email.contact.role if email.contact else ""},
    }
