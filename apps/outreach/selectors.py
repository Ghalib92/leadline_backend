"""Reads for contacts, outreach, client-lead pitches, the call list and deadlines, always
scoped to one organization."""

from datetime import datetime, time, timedelta

from django.conf import settings
from django.db.models import Case, F, IntegerField, OuterRef, Q, Subquery, Value, When
from django.utils import timezone

from apps.discovery.selectors import dossier_content, profile_context
from apps.opportunities.choices import ACTIVE_PIPELINE_STATUSES, ReviewDecision
from apps.opportunities.models import Opportunity
from apps.organizations.choices import LeadSignal, OpportunityType
from apps.organizations.models import OrganizationMembership

from .choices import CLOSED_OUTCOMES, CallListView
from .models import LeadActivity, OutreachEmail, ReminderLog, ReminderPreference


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


# ---------------------------------------------------------------------------
# Client leads: pitches and the call list
# ---------------------------------------------------------------------------

WEBSITE_STATUS = {
    "listed": "has a website",
    "found": "has a website (found in a web search)",
    "down": "has a website, but it was down when checked",
    "none": "no website (its listing shows none)",
    "not_found": "no website found on its listing or in a web search",
    "unknown": "not known",
}
# Approved client leads still being worked.
CALL_LIST_STATUSES = ("pursuing", "submitted")


def pitch_for(opportunity):
    return getattr(opportunity, "pitch", None)


def pitch_inputs(pitch) -> dict:
    """The pitch writer's inputs: the organization with its clients brief, and the lead's
    facts and checked evidence (never page text)."""
    organization, opportunity = pitch.organization, pitch.opportunity
    business = opportunity.business or {}
    signals = dict(LeadSignal.choices)
    lead = {
        "name": opportunity.title,
        "kind": business.get("category", ""),
        "place": opportunity.location or business.get("city", ""),
        "rating": business.get("rating"),
        "reviews": business.get("reviews"),
        "website": business.get("website", ""),
        "website_status": WEBSITE_STATUS.get(business.get("website_status", ""), "not known"),
        "evidence": [item["detail"] for item in business.get("evidence", []) if item.get("detail")],
        "signs": [signals.get(code, code) for code in business.get("signals", [])],
        "on_social": sorted(business.get("socials", {})),
        "why_picked": opportunity.match_reasons,
        "pitch_angle": business.get("pitch_angle", ""),
    }
    return {
        "organization": {
            "organization_name": organization.name,
            "profile": profile_context(organization, "clients"),
            "dossier": dossier_content(organization),
        },
        "lead": {key: value for key, value in lead.items() if value not in ("", None, [])},
    }


def activities_for(opportunity):
    return opportunity.lead_activities.select_related("actor")


def call_list(organization, *, view: str = CallListView.ALL):
    """Approved client leads with their latest contact, in working order: follow-ups that
    are due, then leads never contacted (best first), then the rest by follow-up date."""
    latest = LeadActivity.objects.filter(opportunity=OuterRef("pk")).order_by("-created_at", "-id")
    leads = (
        Opportunity.objects.filter(
            organization=organization,
            opportunity_type=OpportunityType.CLIENT,
            status__in=CALL_LIST_STATUSES,
        )
        .select_related("pitch", "organization")
        .annotate(
            last_outcome=Subquery(latest.values("outcome")[:1]),
            last_channel=Subquery(latest.values("channel")[:1]),
            last_note=Subquery(latest.values("note")[:1]),
            last_contacted_at=Subquery(latest.values("created_at")[:1]),
            follow_up_on=Subquery(latest.values("follow_up_on")[:1]),
        )
    )
    if view == CallListView.TO_CONTACT:
        leads = leads.filter(last_outcome__isnull=True)
    elif view == CallListView.FOLLOW_UP:
        leads = leads.filter(last_outcome__isnull=False).exclude(last_outcome__in=CLOSED_OUTCOMES)
    elif view == CallListView.CLOSED:
        leads = leads.filter(last_outcome__in=CLOSED_OUTCOMES)
    today = timezone.localdate()
    return leads.annotate(
        rank=Case(
            When(follow_up_on__lte=today, then=Value(0)),
            When(last_outcome__isnull=True, then=Value(1)),
            When(last_outcome__in=CLOSED_OUTCOMES, then=Value(3)),
            default=Value(2),
            output_field=IntegerField(),
        )
    ).order_by(
        "rank",
        F("follow_up_on").asc(nulls_last=True),
        "-match_score",
        "id",
    )
