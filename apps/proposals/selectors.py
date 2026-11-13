"""Reads of proposals, always scoped to one organization."""

from apps.discovery.selectors import dossier_content, profile_context
from apps.knowledge.scope import KnowledgeScope

from .models import Proposal

EVENTS_PAGE_SIZE = 500


def proposals_for(organization):
    return Proposal.objects.filter(organization=organization).select_related("opportunity")


def current_version(proposal):
    if proposal.version == 0:
        return None
    return proposal.versions.select_related("created_by").get(number=proposal.version)


def versions_for(proposal):
    return proposal.versions.select_related("created_by").order_by("-number")


def events_after(proposal, after: int = 0):
    return proposal.events.filter(seq__gt=after).order_by("seq")[:EVENTS_PAGE_SIZE]


def knowledge_scope(proposal) -> tuple[KnowledgeScope, str]:
    """Where evidence comes from, and who chose it: the proposal's own choice, else
    the documents the run that found the opportunity was allowed to read."""
    if proposal.knowledge is not None:
        return KnowledgeScope.from_config(proposal.knowledge), "proposal"
    opportunity = proposal.opportunity
    run_config = opportunity.run.config if opportunity.run_id else None
    return KnowledgeScope.of_run(run_config), "run"


def writer_inputs(proposal) -> dict:
    """What the Proposal Writer is given: the opportunity and the organization."""
    opportunity = proposal.opportunity
    organization = proposal.organization
    return {
        "organization_name": organization.name,
        "profile": profile_context(organization),
        "dossier": dossier_content(organization),
        "knowledge": knowledge_scope(proposal)[0].to_config(),
        "opportunity": {
            "title": opportunity.title,
            "funder": opportunity.funder,
            "opportunity_type": opportunity.opportunity_type,
            "summary": opportunity.summary,
            "eligibility": opportunity.eligibility,
            "requirements": opportunity.requirements or {},
            "deadline": opportunity.deadline.date().isoformat() if opportunity.deadline else "",
            "amount_min": _plain(opportunity.amount_min),
            "amount_max": _plain(opportunity.amount_max),
            "currency": opportunity.currency,
            "location": opportunity.location,
            "source_url": opportunity.source_url,
            "source_name": opportunity.source_name,
        },
    }


def _plain(value):
    return None if value is None else str(value)
