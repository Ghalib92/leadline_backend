"""Reads of the opportunity feed, always scoped to one organization."""

from datetime import timedelta

from django.db.models import Count, F, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import TruncWeek
from django.utils import timezone

from apps.discovery.models import DiscoveryRun, SearchAgent
from apps.organizations.selectors import setup_status

from .choices import ACTIVE_PIPELINE_STATUSES, CLOSED_STATUSES, ReviewDecision
from .dedupe import normalize_url
from .models import Opportunity, OpportunityReview

SORTS = {
    "found_at": ("found_at", "id"),
    "-found_at": ("-found_at", "-id"),
    "match_score": ("match_score", "id"),
    "-match_score": ("-match_score", "-found_at"),
    "deadline": ("deadline", "id"),
    "-deadline": ("-deadline", "-id"),
}
DUE_SOON_DAYS = 14
OVERVIEW_LIST_SIZE = 5


def opportunities_for(
    organization,
    *,
    types=(),
    statuses=(),
    agent_id=None,
    run_id=None,
    saved=None,
    search="",
    min_score=None,
    deadline_before=None,
    review=None,
    sort="-found_at",
):
    """`review`: "pending" (never reviewed), "approved" or "rejected" (latest review)."""
    rows = with_reviews(Opportunity.objects.filter(organization=organization))
    if types:
        rows = rows.filter(opportunity_type__in=types)
    if statuses:
        rows = rows.filter(status__in=statuses)
    if agent_id:
        rows = rows.filter(agent_id=agent_id)
    if run_id:
        rows = rows.filter(run_id=run_id)
    if saved is not None:
        rows = rows.filter(saved=saved)
    if search:
        rows = rows.filter(
            Q(title__icontains=search) | Q(funder__icontains=search) | Q(summary__icontains=search)
        )
    if min_score is not None:
        rows = rows.filter(match_score__gte=min_score)
    if deadline_before is not None:
        rows = rows.filter(deadline__lte=deadline_before)
    if review == "pending":
        rows = rows.filter(latest_decision__isnull=True)
    elif review in ("approved", "rejected"):
        decision = ReviewDecision.APPROVE if review == "approved" else ReviewDecision.REJECT
        rows = rows.filter(latest_decision=decision)
    field, tiebreak = SORTS[sort]
    if field.lstrip("-") == "deadline":
        # Opportunities without a deadline go last in both directions.
        deadline = F("deadline")
        first = (
            deadline.desc(nulls_last=True)
            if field.startswith("-")
            else deadline.asc(nulls_last=True)
        )
        return rows.order_by(first, tiebreak)
    return rows.order_by(field, tiebreak)


def with_reviews(rows):
    """Adds `latest_decision` and prefetches reviews newest first (for `latest_review`)."""
    latest = OpportunityReview.objects.filter(opportunity=OuterRef("pk")).order_by(
        "-created_at", "-id"
    )
    return (
        rows.select_related("agent")
        .annotate(latest_decision=Subquery(latest.values("decision")[:1]))
        .prefetch_related(
            Prefetch(
                "reviews",
                queryset=OpportunityReview.objects.select_related("reviewer").order_by(
                    "-created_at", "-id"
                ),
            )
        )
    )


def reviews_for(opportunity):
    return opportunity.reviews.select_related("reviewer").order_by("-created_at", "-id")


def overview(organization) -> dict:
    now = timezone.now()
    opportunities = with_reviews(Opportunity.objects.filter(organization=organization))
    open_rows = opportunities.exclude(status__in=CLOSED_STATUSES)
    due_soon = open_rows.filter(
        deadline__gte=now, deadline__lte=now + timedelta(days=DUE_SOON_DAYS)
    ).order_by("deadline")
    agents = SearchAgent.objects.filter(organization=organization)
    memberships = organization.memberships.filter(status="active")
    return {
        "new_this_week": opportunities.filter(found_at__gte=now - timedelta(days=7)).count(),
        "pursuing": opportunities.filter(status__in=ACTIVE_PIPELINE_STATUSES).count(),
        "active_agents": agents.filter(enabled=True).count(),
        "due_soon": due_soon.count(),
        "upcoming": list(due_soon[:OVERVIEW_LIST_SIZE]),
        "latest": list(opportunities.order_by("-found_at", "-id")[:OVERVIEW_LIST_SIZE]),
        "pipeline": {
            status: opportunities.filter(status=status).count()
            for status in ("new", "reviewing", "pursuing", "submitted")
        },
        "attention": _attention(organization, opportunities, open_rows, due_soon, now),
        "checklist": {
            "profile": setup_status(organization)["ready_for_matching"],
            "agent": agents.filter(enabled=True).exists(),
            "teammate": memberships.count() > 1
            or organization.invitations.filter(status="pending").exists(),
        },
    }


def _attention(organization, opportunities, open_rows, due_soon, now) -> list[dict]:
    """What needs someone, most urgent first; kinds with nothing to do are left out.
    `target` is the id the page links to when there is one obvious place to go."""
    failed = DiscoveryRun.objects.filter(
        organization=organization,
        status__in=("failed", "partial"),
        created_at__gte=now - timedelta(days=7),
    ).order_by("-created_at")
    items = [
        ("deadline_without_proposal", due_soon.filter(proposal__isnull=True), None),
        (
            "to_review",
            opportunities.filter(status__in=("new", "reviewing"), latest_decision__isnull=True),
            None,
        ),
        (
            "eligibility_gap",
            open_rows.filter(
                requirements__brief__eligibility_checklist__contains=[{"meets": False}]
            ),
            None,
        ),
        ("failed_runs", failed, failed.values_list("pk", flat=True).first()),
    ]
    attention = []
    for kind, rows, target in items:
        count = rows.count()
        if count:
            attention.append({"kind": kind, "count": count, "target": target or ""})
    return attention


def known_urls(organization) -> frozenset[str]:
    """Normalised source URLs of everything already in the feed, so runs skip them."""
    urls = Opportunity.objects.filter(organization=organization).values_list(
        "source_url", flat=True
    )
    return frozenset(normalize_url(url) for url in urls)


INSIGHT_SCORE_BUCKETS = ((0, 39), (40, 54), (55, 69), (70, 84), (85, 100))
INSIGHT_TOP_SOURCES = 10


def insights(organization, *, days: int) -> dict:
    """Dashboard charts over the last `days`: runs per week, what was found (by type,
    source and score) and how people reviewed it."""
    since = timezone.now() - timedelta(days=days)
    runs = DiscoveryRun.objects.filter(organization=organization, created_at__gte=since)
    weekly = (
        runs.annotate(week=TruncWeek("created_at"))
        .values("week")
        .annotate(
            runs=Count("id"),
            succeeded=Count("id", filter=Q(status="succeeded")),
            partial=Count("id", filter=Q(status="partial")),
            failed=Count("id", filter=Q(status="failed")),
        )
        .order_by("week")
    )
    found = Opportunity.objects.filter(organization=organization, found_at__gte=since)
    weekly_found = dict(
        found.annotate(week=TruncWeek("found_at"))
        .values("week")
        .annotate(count=Count("id"))
        .values_list("week", "count")
    )
    reviews = OpportunityReview.objects.filter(organization=organization, created_at__gte=since)
    # Count each opportunity once, by its latest review in the period.
    latest_ids = (
        reviews.order_by("opportunity_id", "-created_at", "-id")
        .distinct("opportunity_id")
        .values("id")
    )
    latest = OpportunityReview.objects.filter(id__in=latest_ids)
    approved = latest.filter(decision=ReviewDecision.APPROVE).count()
    rejected = latest.filter(decision=ReviewDecision.REJECT).count()
    return {
        "days": days,
        "runs_by_week": [{**row, "found": weekly_found.get(row["week"], 0)} for row in weekly],
        "opportunities_found": found.count(),
        "by_type": _counts(found, "opportunity_type"),
        "by_source": _counts(found, "source_name")[:INSIGHT_TOP_SOURCES],
        "by_score": [
            {
                "label": f"{low}–{high}",
                "min": low,
                "max": high,
                "count": found.filter(match_score__gte=low, match_score__lte=high).count(),
            }
            for low, high in INSIGHT_SCORE_BUCKETS
        ],
        "reviews": {
            "approved": approved,
            "rejected": rejected,
            # Found in the period and never reviewed.
            "pending": found.filter(reviews__isnull=True).count(),
            "approval_rate": round(approved / (approved + rejected), 3)
            if approved + rejected
            else None,
        },
        "rejection_reasons": _counts(latest.filter(decision=ReviewDecision.REJECT), "reason"),
    }


def _counts(rows, field) -> list[dict]:
    return [
        {"key": row[field], "count": row["count"]}
        for row in rows.values(field).annotate(count=Count("id")).order_by("-count", field)
    ]


def similar_decisions(
    organization_id,
    vector,
    *,
    limit=4,
    exclude_opportunity_id=None,
    before=None,
) -> list[OpportunityReview]:
    """The organization's past decisions closest to `vector`: one per opportunity (its
    latest review), nearest first. `before` limits to reviews made earlier (evals use
    it so a case never sees its own or later decisions)."""
    from pgvector.django import CosineDistance

    reviews = OpportunityReview.objects.filter(
        organization_id=organization_id, embedding__isnull=False
    )
    if exclude_opportunity_id:
        reviews = reviews.exclude(opportunity_id=exclude_opportunity_id)
    if before is not None:
        reviews = reviews.filter(created_at__lt=before)
    latest_ids = (
        reviews.order_by("opportunity_id", "-created_at", "-id")
        .distinct("opportunity_id")
        .values("id")
    )
    return list(
        OpportunityReview.objects.filter(id__in=latest_ids)
        .annotate(distance=CosineDistance("embedding", vector))
        .order_by("distance")[:limit]
    )
