"""The weekly digest: one organization's week, for one member, in a few lines.

What its workers found (best first), what waits for review, approved bids and
proposals closing soon (with the documents a bid still misses), and call-list
follow-ups due. Nothing to report means no email.
"""

from datetime import datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from apps.opportunities.choices import ACTIVE_PIPELINE_STATUSES, OpportunityStatus
from apps.opportunities.models import Opportunity
from apps.organizations.choices import OpportunityType

from .selectors import CLOSED_OUTCOMES, call_list, opportunity_link

TOP_MATCHES = 6
CLOSING_WITHIN_DAYS = 14
FOLLOW_UPS_WITHIN_DAYS = 7
LIST_LIMIT = 8

KIND_LABELS = {
    OpportunityType.CLIENT: ("client lead", "client leads"),
    OpportunityType.TENDER: ("tender", "tenders"),
}


def _plural(count: int, kind: str) -> str:
    one, many = KIND_LABELS.get(kind, ("opportunity", "opportunities"))
    return f"{count} {one if count == 1 else many}"


def week_of(today) -> datetime:
    """The start of the week a digest sent `today` covers (seven days back)."""
    return timezone.make_aware(datetime.combine(today - timedelta(days=7), time.min))


def digest_for(organization, today) -> dict | None:
    """The digest's content, or None when there is nothing to tell."""
    since = week_of(today)
    found = organization.opportunities.filter(found_at__gte=since).exclude(
        status=OpportunityStatus.DISMISSED
    )
    counts = {}
    for kind in found.values_list("opportunity_type", flat=True):
        counts[kind] = counts.get(kind, 0) + 1
    top = list(found.order_by("-match_score", "-found_at")[:TOP_MATCHES])

    to_review = organization.opportunities.filter(status=OpportunityStatus.NEW).count()

    start = timezone.make_aware(datetime.combine(today, time.min))
    closing = (
        organization.opportunities.filter(
            status__in=ACTIVE_PIPELINE_STATUSES,
            deadline__gte=start,
            deadline__lt=start + timedelta(days=CLOSING_WITHIN_DAYS + 1),
        )
        .exclude(opportunity_type=OpportunityType.CLIENT)
        .select_related("organization")
        .order_by("deadline")[:LIST_LIMIT]
    )
    follow_ups = [
        lead
        for lead in call_list(organization)
        if lead.follow_up_on
        and lead.follow_up_on <= today + timedelta(days=FOLLOW_UPS_WITHIN_DAYS)
        and lead.last_outcome not in CLOSED_OUTCOMES
    ][:LIST_LIMIT]

    if not (top or closing or follow_ups):
        return None
    return {
        "organization_name": organization.name,
        "found_summary": ", ".join(_plural(n, kind) for kind, n in sorted(counts.items())),
        "found_total": sum(counts.values()),
        "top": [_line(item) for item in top],
        "to_review": to_review,
        "review_url": f"{_org_url(organization)}/opportunities?review=1",
        "closing": [_closing(item, today) for item in closing],
        "follow_ups": [
            {
                "title": lead.title,
                "when": _days(lead.follow_up_on, today),
                "url": opportunity_link(lead),
            }
            for lead in follow_ups
        ],
        "calls_url": f"{_org_url(organization)}/calls",
    }


def _org_url(organization) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/o/{organization.slug}"


def _line(item: Opportunity) -> dict:
    kind = KIND_LABELS.get(item.opportunity_type, ("opportunity",))[0]
    client = item.opportunity_type == OpportunityType.CLIENT
    where = item.location if client else (item.funder or item.location)
    return {
        "title": item.title,
        "detail": f"{kind}, {item.match_score}% match" + (f" · {where}" if where else ""),
        "url": opportunity_link(item),
    }


def _closing(item: Opportunity, today) -> dict:
    day = timezone.localtime(item.deadline).date()
    line = {"title": item.title, "when": _days(day, today), "url": opportunity_link(item)}
    proposal = getattr(item, "proposal", None)
    checklist = (proposal.checklist or []) if proposal is not None else []
    missing = [c for c in checklist if c.get("status") == "missing" and not c.get("done")]
    if missing:
        line["note"] = f"{len(missing)} document(s) still missing"
    return line


def _days(day, today) -> str:
    days = (day - today).days
    if days < 0:
        return "overdue"
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return f"in {days} days ({day:%a %d %b})"
