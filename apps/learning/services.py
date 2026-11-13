"""Writes for the learning loop: preference suggestions from repeated rejections.

Suggestions are made by code where a count is enough (funders, amounts) and by the
preference advisor agent where judgment is needed (which keyword explains a run of
"not our focus" rejections). Each rejection is evidence for at most one suggestion of
a kind, so the same rejections never produce suggestions twice; dismissed values are
never suggested again. Accepting applies the change through the organizations
service, which checks the caller is an admin.
"""

import logging
import statistics
from datetime import timedelta
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.common.api.exceptions import Conflict
from apps.opportunities.choices import RejectionReason, ReviewDecision
from apps.opportunities.dedupe import normalize_text
from apps.opportunities.models import OpportunityReview
from apps.organizations import services as organization_services
from apps.organizations.models import Organization
from apps.organizations.selectors import preferences_for
from apps.organizations.services import lock_organization, record_event

from .choices import SuggestionKind, SuggestionStatus
from .models import PreferenceSuggestion

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 180
# How many rejections make a pattern.
MIN_FUNDER_REJECTIONS = 2
MIN_AMOUNT_REJECTIONS = 3
MIN_TOPIC_REJECTIONS = 3
TOPIC_REASONS = (RejectionReason.WRONG_FOCUS, RejectionReason.WRONG_LOCATION)


def _latest_rejections(organization):
    """Each opportunity's latest review in the lookback window, if it is a rejection."""
    since = timezone.now() - timedelta(days=LOOKBACK_DAYS)
    latest_ids = (
        OpportunityReview.objects.filter(organization=organization, created_at__gte=since)
        .order_by("opportunity_id", "-created_at", "-id")
        .distinct("opportunity_id")
        .values("id")
    )
    return list(
        OpportunityReview.objects.filter(
            id__in=latest_ids, decision=ReviewDecision.REJECT
        ).order_by("created_at")
    )


def _used_evidence(organization, kind) -> set[str]:
    used = set()
    for evidence in PreferenceSuggestion.objects.filter(
        organization=organization, kind=kind
    ).values_list("evidence", flat=True):
        used.update(evidence)
    return used


def _suggest(organization, kind, value, *, rationale, evidence, currency=""):
    """Create a pending suggestion unless this value was ever suggested before."""
    key = normalize_text(str(value))
    if not key:
        return None
    suggestion, created = PreferenceSuggestion.objects.get_or_create(
        organization=organization,
        kind=kind,
        value_key=key,
        defaults={
            "value": str(value)[:200],
            "currency": currency,
            "rationale": rationale[:500],
            "evidence": sorted(evidence),
        },
    )
    return suggestion if created else None


def refresh_suggestions(organization_id) -> list[PreferenceSuggestion]:
    """Look for new patterns in the organization's rejections. Safe to run often.

    The model call (keywords) happens outside any lock; its suggestions are then saved
    under the lock like the rest.
    """
    created = _rule_suggestions(organization_id)
    advice = _keyword_advice(organization_id)
    if advice:
        created += _save_keyword_suggestions(organization_id, *advice)
    return created


def _locked(organization_id):
    organization = Organization.objects.select_for_update().filter(pk=organization_id).first()
    return None if organization is None or organization.is_archived else organization


@transaction.atomic
def _rule_suggestions(organization_id) -> list[PreferenceSuggestion]:
    organization = _locked(organization_id)
    if organization is None:
        return []
    rejections = _latest_rejections(organization)
    preferences = preferences_for(organization)
    created = [
        *_funder_suggestions(organization, rejections, preferences),
        *_amount_suggestions(organization, rejections, preferences),
    ]
    return [suggestion for suggestion in created if suggestion is not None]


def _funder_suggestions(organization, rejections, preferences):
    excluded = {normalize_text(f) for f in preferences.excluded_funders}
    used = _used_evidence(organization, SuggestionKind.EXCLUDE_FUNDER)
    by_funder: dict[str, list] = {}
    for review in rejections:
        funder = (review.snapshot.get("funder") or "").strip()
        if review.reason == RejectionReason.FUNDER and funder and review.pk not in used:
            by_funder.setdefault(normalize_text(funder), []).append((funder, review))
    for key, items in by_funder.items():
        if len(items) < MIN_FUNDER_REJECTIONS or key in excluded:
            continue
        name = items[-1][0]
        yield _suggest(
            organization,
            SuggestionKind.EXCLUDE_FUNDER,
            name,
            rationale=f"You rejected {len(items)} opportunities from {name} because of the funder.",
            evidence={review.pk for _, review in items},
        )


def _amount_suggestions(organization, rejections, preferences):
    currency = preferences.funding_currency
    rules = (
        (RejectionReason.AMOUNT_TOO_SMALL, SuggestionKind.MIN_AMOUNT, "amount_max"),
        (RejectionReason.AMOUNT_TOO_LARGE, SuggestionKind.MAX_AMOUNT, "amount_min"),
    )
    for reason, kind, field in rules:
        used = _used_evidence(organization, kind)
        items = [
            (Decimal(review.snapshot[field]), review)
            for review in rejections
            if review.reason == reason
            and review.pk not in used
            and review.snapshot.get(field)
            and review.snapshot.get("currency") == currency
        ]
        if len(items) < MIN_AMOUNT_REJECTIONS:
            continue
        typical = Decimal(statistics.median(amount for amount, _ in items))
        if kind == SuggestionKind.MIN_AMOUNT:
            value = _round(typical, ROUND_UP)
            current = preferences.min_funding_amount
            if current is not None and current >= value:
                continue
            rationale = (
                f"You rejected {len(items)} opportunities as too small; they offered "
                f"up to about {currency} {typical:,.0f}."
            )
        else:
            value = _round(typical, ROUND_DOWN)
            current = preferences.max_funding_amount
            if current is not None and current <= value:
                continue
            rationale = (
                f"You rejected {len(items)} opportunities as too large; they started "
                f"at about {currency} {typical:,.0f}."
            )
        yield _suggest(
            organization,
            kind,
            value,
            rationale=rationale,
            currency=currency,
            evidence={review.pk for _, review in items},
        )


def _round(amount: Decimal, rounding) -> Decimal:
    """To two significant figures: 23,456 → 24,000 (up) or 23,000 (down)."""
    if amount <= 0:
        return Decimal(0)
    step = Decimal(10) ** max(0, len(str(int(amount))) - 2)
    return (amount / step).quantize(Decimal(1), rounding=rounding) * step


def _keyword_advice(organization_id):
    """(topical rejections, advice) from the preference advisor, or None."""
    organization = Organization.objects.filter(pk=organization_id).first()
    if organization is None or organization.is_archived:
        return None
    used = _used_evidence(organization, SuggestionKind.EXCLUDE_KEYWORD)
    topical = [
        review
        for review in _latest_rejections(organization)
        if review.reason in TOPIC_REASONS and review.pk not in used
    ]
    if len(topical) < MIN_TOPIC_REJECTIONS:
        return None
    from apps.ai.agents.preference_advisor import suggest_keywords
    from apps.ai.exceptions import AIConfigError, StructuredOutputError
    from apps.ai.graphs.budget import Budget, BudgetExhausted
    from apps.ai.graphs.runtime import TaskRuntime

    preferences = preferences_for(organization)
    limits = {"max_searches": 0, "max_pages": 0, "max_tokens": 20_000, "max_wall_seconds": 120}
    try:
        advice = suggest_keywords(
            TaskRuntime(budget=Budget(limits)),
            focus=_focus(organization, preferences),
            rejections=[
                {
                    "title": review.snapshot.get("title", ""),
                    "funder": review.snapshot.get("funder", ""),
                    "reason": RejectionReason(review.reason).label,
                    "comment": review.comment,
                }
                for review in topical
            ],
            already_excluded=preferences.excluded_keywords,
        )
    except (AIConfigError, StructuredOutputError, BudgetExhausted) as exc:
        logger.warning("Preference advice for %s failed: %s", organization_id, exc)
        return None
    return topical, advice


def _focus(organization, preferences) -> str:
    areas = ", ".join([*organization.sectors, *preferences.focus_areas, *preferences.keywords])
    return f"{organization.description[:500]} Focus: {areas}"


@transaction.atomic
def _save_keyword_suggestions(organization_id, topical, advice) -> list[PreferenceSuggestion]:
    organization = _locked(organization_id)
    if organization is None:
        return []
    preferences = preferences_for(organization)
    excluded = {normalize_text(keyword) for keyword in preferences.excluded_keywords}
    focus_words = set(normalize_text(_focus(organization, preferences)).split())
    created = []
    for item in advice.keywords[:3]:
        evidence = {topical[n - 1].pk for n in item.rejections if 1 <= n <= len(topical)}
        keyword = item.keyword.strip()
        key = normalize_text(keyword)
        # Code checks what the model was told: real support, nothing they already want.
        if len(evidence) < 2 or not key or key in excluded or set(key.split()) & focus_words:
            continue
        suggestion = _suggest(
            organization,
            SuggestionKind.EXCLUDE_KEYWORD,
            keyword,
            rationale=item.why or f"Explains {len(evidence)} of your recent rejections.",
            evidence=evidence,
        )
        if suggestion is not None:
            created.append(suggestion)
    return created


# ---------------------------------------------------------------------------
# Admin decisions
# ---------------------------------------------------------------------------


@transaction.atomic
def accept_suggestion(*, organization_id, suggestion_id, actor) -> PreferenceSuggestion:
    organization = lock_organization(organization_id, actor)  # admins only
    suggestion = _pending(organization, suggestion_id)
    preferences = preferences_for(organization)
    if suggestion.kind == SuggestionKind.EXCLUDE_FUNDER:
        change = {"excluded_funders": [*preferences.excluded_funders, suggestion.value]}
    elif suggestion.kind == SuggestionKind.EXCLUDE_KEYWORD:
        change = {"excluded_keywords": [*preferences.excluded_keywords, suggestion.value]}
    else:
        if suggestion.currency != preferences.funding_currency:
            raise Conflict(
                "Your funding currency changed since this was suggested; dismiss it instead.",
                code="currency_changed",
            )
        field = (
            "min_funding_amount"
            if suggestion.kind == SuggestionKind.MIN_AMOUNT
            else ("max_funding_amount")
        )
        change = {field: Decimal(suggestion.value)}
    # Validates (e.g. min above max) and records its own event.
    organization_services.update_preferences(organization_id=organization.pk, actor=actor, **change)
    _decide(suggestion, SuggestionStatus.ACCEPTED, actor)
    record_event(
        organization,
        actor,
        "suggestion.accepted",
        target_id=suggestion.pk,
        metadata={"kind": suggestion.kind, "value": suggestion.value},
    )
    return suggestion


@transaction.atomic
def dismiss_suggestion(*, organization_id, suggestion_id, actor) -> PreferenceSuggestion:
    organization = lock_organization(organization_id, actor)
    suggestion = _pending(organization, suggestion_id)
    _decide(suggestion, SuggestionStatus.DISMISSED, actor)
    record_event(
        organization,
        actor,
        "suggestion.dismissed",
        target_id=suggestion.pk,
        metadata={"kind": suggestion.kind, "value": suggestion.value},
    )
    return suggestion


def _pending(organization, suggestion_id):
    suggestion = get_object_or_404(
        PreferenceSuggestion.objects.select_for_update(),
        pk=suggestion_id,
        organization=organization,
    )
    if suggestion.status != SuggestionStatus.PENDING:
        raise Conflict(f"This suggestion was already {suggestion.status}.", code="decided")
    return suggestion


def _decide(suggestion, status, actor):
    suggestion.status = status
    suggestion.decided_by = actor
    suggestion.decided_at = timezone.now()
    suggestion.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
