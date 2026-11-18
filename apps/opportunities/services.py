"""Writes for opportunities: saving what discovery found, reviews and triage by members."""

import logging
from functools import partial

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from apps.common.api.exceptions import Conflict
from apps.common.jobs import enqueue
from apps.organizations.choices import OpportunityType
from apps.organizations.services import lock_organization, record_event

from .choices import REVIEWABLE_STATUSES, OpportunityStatus, ReviewDecision
from .dedupe import title_key, url_key
from .models import Opportunity, OpportunityReview
from .signals import opportunity_approved, opportunity_reviewed

logger = logging.getLogger(__name__)

TRIAGE_FIELDS = frozenset({"status", "saved", "notes"})
# Approving and rejecting carry a decision and a reason, so they go through review().
REVIEW_ONLY_STATUSES = frozenset({OpportunityStatus.PURSUING, OpportunityStatus.DISMISSED})
SNAPSHOT_FIELDS = (
    "title",
    "funder",
    "opportunity_type",
    "summary",
    "eligibility",
    "location",
    "source_name",
    "source_url",
    "match_reasons",
)


def add_discovered(*, run, candidate) -> tuple[Opportunity, bool]:
    """Save a candidate a run accepted, unless the organization already has it.

    Called by apps.discovery.services inside its locked transaction; runs of one
    organization never overlap, and the unique key backs that up.
    """
    is_client = candidate.opportunity_type == OpportunityType.CLIENT
    keys = {
        "dedupe_key": url_key(str(candidate.source_url)),
        # A business has no funder: its name and place tell branches and namesakes apart.
        "title_key": title_key(
            candidate.title, candidate.location if is_client else candidate.funder
        ),
    }
    existing = Opportunity.objects.filter(
        Q(dedupe_key=keys["dedupe_key"]) | Q(title_key=keys["title_key"]),
        organization_id=run.organization_id,
    ).first()
    if existing is not None:
        return existing, False
    opportunity = Opportunity.objects.create(
        organization_id=run.organization_id,
        agent_id=run.agent_id,
        run=run,
        opportunity_type=candidate.opportunity_type,
        title=candidate.title,
        funder=candidate.funder,
        summary=candidate.summary,
        eligibility=candidate.eligibility,
        amount_min=candidate.amount_min,
        amount_max=candidate.amount_max,
        currency=candidate.currency,
        deadline=candidate.deadline,
        location=candidate.location,
        source_url=str(candidate.source_url),
        source_name=candidate.source_name,
        match_score=candidate.match_score,
        match_reasons=candidate.match_reasons,
        requirements=candidate.requirements,
        verification=candidate.verification,
        business=candidate.business,
        tender=candidate.tender,
        **keys,
    )
    if is_client:
        return opportunity, True  # the opportunity brief is for calls; leads get a pitch
    if not (isinstance(opportunity.requirements, dict) and "brief" in opportunity.requirements):
        from .tasks import analyze_opportunity

        enqueue(analyze_opportunity, opportunity.pk)
    return opportunity, True


@transaction.atomic
def save_lead_lookup(opportunity_id, business: dict) -> bool:
    """A worker write: an approved client lead's business after its website was looked
    up (Opportunity.business). Its source link becomes its own website when one was
    found. False when the lead is gone or no longer a client lead."""
    opportunity = (
        Opportunity.objects.select_for_update()
        .filter(pk=opportunity_id, opportunity_type=OpportunityType.CLIENT)
        .first()
    )
    if opportunity is None:
        return False
    opportunity.business = business
    fields = ["business", "updated_at"]
    if business.get("website"):
        opportunity.source_url = business["website"]
        fields.append("source_url")
    opportunity.save(update_fields=fields)
    return True


@transaction.atomic
def update_opportunity(*, organization_id, opportunity_id, actor, **data):
    """Any member may triage: move it along the pipeline, save it, keep notes."""
    organization = lock_organization(organization_id, actor, admin=False)
    opportunity = get_object_or_404(organization.opportunities, pk=opportunity_id)
    if data.get("status") in REVIEW_ONLY_STATUSES and data["status"] != opportunity.status:
        raise ValidationError(
            {
                "status": "Approve or reject it with a review instead.",
                "code": "use_review",
            }
        )
    changed = [
        field for field in TRIAGE_FIELDS & data.keys() if getattr(opportunity, field) != data[field]
    ]
    for field in changed:
        setattr(opportunity, field, data[field])
    if changed:
        opportunity.save(update_fields=[*changed, "updated_at"])
        metadata = {"fields": sorted(changed)}
        if "status" in changed:
            metadata["status"] = opportunity.status
        record_event(
            organization, actor, "opportunity.updated", target_id=opportunity.pk, metadata=metadata
        )
    return opportunity


@transaction.atomic
def review(*, organization_id, opportunity_id, actor, decision, reason="", comment=""):
    """Approve (→ pursuing, starts the Proposal Writer) or reject (→ dismissed) an
    opportunity. Any member may review; a later review replaces the decision, and
    every review is kept for the learning loop."""
    organization = lock_organization(organization_id, actor, admin=False)
    opportunity = get_object_or_404(
        organization.opportunities.select_for_update(of=("self",)).select_related("run"),
        pk=opportunity_id,
    )
    approve = decision == ReviewDecision.APPROVE
    comment = comment.strip()
    if approve:
        reason = ""
    elif not reason or not comment:
        raise ValidationError(
            {
                **({"reason": "Choose why you are rejecting it."} if not reason else {}),
                **({"comment": "Add a short note on why."} if not comment else {}),
            }
        )
    if opportunity.status not in REVIEWABLE_STATUSES:
        raise Conflict(
            f"It is already {opportunity.get_status_display().lower()}; reviews are for "
            "opportunities not yet submitted.",
            code="not_reviewable",
        )
    target = OpportunityStatus.PURSUING if approve else OpportunityStatus.DISMISSED
    latest = opportunity.reviews.first()
    # Rejecting again with a different reason corrects it; anything else is a repeat.
    if (
        opportunity.status == target
        and latest is not None
        and latest.decision == decision
        and (approve or latest.reason == reason)
    ):
        raise Conflict(
            "It is already approved." if approve else "It is already rejected.",
            code="already_reviewed",
        )

    run = opportunity.run
    review = OpportunityReview.objects.create(
        organization=organization,
        opportunity=opportunity,
        reviewer=actor,
        decision=decision,
        reason=reason,
        comment=comment,
        status_before=opportunity.status,
        match_score=opportunity.match_score,
        snapshot=_snapshot(opportunity),
        judge_prompt=_judge_used(run, "prompt_versions"),
        judge_model=_judge_used(run, "models_used"),
    )
    opportunity.status = target
    opportunity.save(update_fields=["status", "updated_at"])
    record_event(
        organization,
        actor,
        f"opportunity.{'approved' if approve else 'rejected'}",
        target_id=opportunity.pk,
        metadata={"title": opportunity.title[:120], **({"reason": reason} if reason else {})},
    )
    transaction.on_commit(
        partial(
            opportunity_reviewed.send,
            sender=Opportunity,
            organization_id=organization.pk,
            opportunity_id=opportunity.pk,
            review_id=review.pk,
            decision=decision,
        ),
        robust=True,
    )
    if approve:
        transaction.on_commit(
            partial(
                opportunity_approved.send,
                sender=Opportunity,
                organization_id=organization.pk,
                opportunity_id=opportunity.pk,
                review_id=review.pk,
            ),
            robust=True,
        )
    return review


def _judge_used(run, field: str) -> str:
    """The judge behind the run's scores: the opportunity judge, or the tender judge."""
    used = (getattr(run, field, None) or {}) if run else {}
    return used.get("judge") or used.get("tender_judge") or ""


def _snapshot(opportunity) -> dict:
    data = {field: getattr(opportunity, field) for field in SNAPSHOT_FIELDS}
    data["amount_min"] = str(opportunity.amount_min) if opportunity.amount_min is not None else None
    data["amount_max"] = str(opportunity.amount_max) if opportunity.amount_max is not None else None
    data["currency"] = opportunity.currency
    data["deadline"] = opportunity.deadline.isoformat() if opportunity.deadline else None
    return data


def _brief_evidence(opportunity: Opportunity, facts: dict) -> tuple[str, dict]:
    """Passages for the brief, from the documents the run that found it could read, and
    what they came from: {mode, folders (names), documents (titles)}."""
    from apps.ai.tools.knowledge import (
        NO_DOCUMENTS,
        evidence_query,
        format_hits,
        search_company_knowledge,
    )
    from apps.knowledge.scope import KnowledgeScope

    run_config = opportunity.run.config if opportunity.run_id else None
    scope = KnowledgeScope.of_run(run_config)
    folders = [f["name"] for f in (run_config or {}).get("folders", [])]
    sources = {"mode": scope.mode, "folders": folders, "documents": []}
    if scope.reads_nothing:
        return NO_DOCUMENTS, sources
    try:
        hits = search_company_knowledge(
            opportunity.organization_id, evidence_query(facts), scope=scope, limit=4
        )
    except Exception:  # evidence improves the brief; it must never stop it
        logger.warning("Brief evidence lookup failed", exc_info=True)
        return "", sources
    sources["documents"] = sorted({hit.document_title for hit in hits})
    return format_hits(hits), sources


def generate_brief_for_opportunity(opportunity: Opportunity, *, force: bool = False):
    has_brief = isinstance(opportunity.requirements, dict) and "brief" in opportunity.requirements
    if not force and has_brief:
        brief_data = opportunity.requirements["brief"]
        if isinstance(brief_data, dict):
            from apps.ai.schemas.discovery import OpportunityBrief

            return OpportunityBrief(**brief_data)

    from apps.ai.agents.opportunity_analyst import write_brief
    from apps.ai.graphs.budget import Budget
    from apps.ai.graphs.runtime import TaskRuntime
    from apps.discovery.selectors import dossier_content, profile_context

    organization = opportunity.organization
    profile = profile_context(organization)
    dossier = dossier_content(organization)

    opp_data = {
        "title": opportunity.title,
        "funder": opportunity.funder,
        "opportunity_type": opportunity.opportunity_type,
        "summary": opportunity.summary,
        "eligibility": opportunity.eligibility,
        "amount_min": float(opportunity.amount_min) if opportunity.amount_min is not None else None,
        "amount_max": float(opportunity.amount_max) if opportunity.amount_max is not None else None,
        "currency": opportunity.currency,
        "deadline": opportunity.deadline.isoformat() if opportunity.deadline else None,
        "location": opportunity.location,
        "source_url": opportunity.source_url,
        "requirements": opportunity.requirements,
    }

    limits = {"max_searches": 0, "max_pages": 0, "max_tokens": 10_000, "max_wall_seconds": 60}
    runtime = TaskRuntime(
        budget=Budget(limits),
        organization_name=organization.name,
        profile=profile,
        dossier=dossier,
    )

    evidence, sources = _brief_evidence(opportunity, opp_data)
    brief = write_brief(runtime, opportunity=opp_data, company_evidence=evidence)
    current_requirements = (
        opportunity.requirements if isinstance(opportunity.requirements, dict) else {}
    )
    current_requirements.pop("brief_error", None)
    opportunity.requirements = {
        **current_requirements,
        "brief": brief.model_dump(),
        "brief_sources": sources,
    }
    opportunity.save(update_fields=["requirements", "updated_at"])
    return brief
