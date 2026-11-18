"""Writes for proposals.

Members start drafts, edit and rewrite sections, and restore versions: each locks the
organization, then the proposal. Every change to the text makes a new version.

Workers (no caller) claim a queued job and save its result. Each worker write checks
the job is still the one it claimed (`job_started_at`), so a worker that was given up
on can never overwrite newer work.
"""

import re
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common.api.exceptions import Conflict
from apps.common.jobs import enqueue
from apps.knowledge.scope import KnowledgeScope
from apps.opportunities.choices import OpportunityStatus
from apps.organizations.choices import OpportunityType
from apps.organizations.services import lock_organization, record_event

from .choices import ProposalJob, ProposalStatus, VersionSource
from .models import Proposal, ProposalEvent, ProposalVersion

# A proposal is written for an approved opportunity, and may be revised after submitting.
DRAFTABLE_STATUSES = (OpportunityStatus.PURSUING, OpportunityStatus.SUBMITTED)
EDITABLE_FIELDS = frozenset({"title", "content"})


def _is_stale(proposal) -> bool:
    started = proposal.job_started_at or proposal.job_queued_at
    cutoff = timezone.now() - timedelta(minutes=settings.PROPOSAL_STALE_MINUTES)
    return started is not None and started < cutoff


def _locked_proposal(organization, proposal_id) -> Proposal:
    return get_object_or_404(organization.proposals.select_for_update(), pk=proposal_id)


def _ensure_idle(proposal):
    if proposal.is_busy and not _is_stale(proposal):
        raise Conflict(
            "The AI is still working on this proposal. Wait for it to finish.", code="busy"
        )


def _queue(proposal, *, job, actor, section="", instructions=""):
    proposal.status = ProposalStatus.QUEUED
    proposal.job = job
    proposal.job_section = section
    proposal.job_instructions = instructions
    proposal.job_requested_by = actor
    proposal.job_queued_at = timezone.now()
    proposal.job_started_at = None
    proposal.error = ""
    proposal.runner = settings.PROPOSAL_RUNNER
    proposal.save()

    from .tasks import run_proposal_job

    enqueue(run_proposal_job, proposal.pk)


def _new_version(proposal, sections, *, source, note, actor=None) -> ProposalVersion:
    proposal.version += 1
    version = ProposalVersion.objects.create(
        proposal=proposal,
        number=proposal.version,
        sections=sections,
        source=source,
        note=note[:200],
        created_by=actor,
    )
    return version


def _requirement_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _with_ticks(checklist: list[dict], previous: list[dict]) -> list[dict]:
    """A new checklist keeps what people ticked on the last one (same requirement)."""
    ticked = {
        _requirement_key(item.get("requirement", "")): item
        for item in previous or []
        if item.get("done")
    }
    merged = []
    for item in checklist:
        old = ticked.get(_requirement_key(item.get("requirement", "")))
        merged.append(
            {
                **item,
                "done": bool(old),
                "done_at": old.get("done_at") if old else None,
            }
        )
    return merged


def _is_tender(proposal) -> bool:
    return proposal.opportunity.opportunity_type == OpportunityType.TENDER


def _current_sections(proposal) -> list[dict]:
    if proposal.version == 0:
        return []
    return proposal.versions.get(number=proposal.version).sections


# ---------------------------------------------------------------------------
# Member actions
# ---------------------------------------------------------------------------


@transaction.atomic
def start_draft(*, organization_id, opportunity_id, actor) -> Proposal:
    """Write (or rewrite from scratch) the proposal for an approved opportunity. Earlier
    versions are kept."""
    organization = lock_organization(organization_id, actor, admin=False)
    opportunity = get_object_or_404(organization.opportunities, pk=opportunity_id)
    if opportunity.status not in DRAFTABLE_STATUSES:
        raise Conflict(
            "Approve the opportunity before writing a proposal for it.", code="not_approved"
        )
    if opportunity.opportunity_type == OpportunityType.CLIENT:
        raise Conflict("Client leads get a pitch, not a proposal.", code="client_lead")
    proposal = Proposal.objects.select_for_update().filter(
        opportunity=opportunity
    ).first() or Proposal(organization=organization, opportunity=opportunity, created_by=actor)
    if proposal.pk:
        _ensure_idle(proposal)
    _queue(proposal, job=ProposalJob.DRAFT, actor=actor)
    record_event(
        organization,
        actor,
        "proposal.draft_requested",
        target_id=proposal.pk,
        metadata={"opportunity": opportunity.pk},
    )
    return proposal


@transaction.atomic
def start_draft_for_approval(*, organization_id, opportunity_id) -> Proposal | None:
    """On approval: write the first draft, unless a proposal already exists (approving
    again after a rejection keeps the earlier work)."""
    from apps.opportunities.models import Opportunity

    opportunity = (
        Opportunity.objects.select_for_update()
        .filter(pk=opportunity_id, organization_id=organization_id)
        .select_related("organization")
        .first()
    )
    if opportunity is None or opportunity.organization.is_archived:
        return None
    if opportunity.status not in DRAFTABLE_STATUSES:
        return None
    if opportunity.opportunity_type == OpportunityType.CLIENT:
        return None  # client leads get a pitch (apps.outreach), not a proposal
    if Proposal.objects.filter(opportunity=opportunity).exists():
        return None
    proposal = Proposal(organization_id=organization_id, opportunity=opportunity)
    _queue(proposal, job=ProposalJob.DRAFT, actor=None)
    return proposal


@transaction.atomic
def rewrite_section(*, organization_id, proposal_id, key, actor, instructions="") -> Proposal:
    organization = lock_organization(organization_id, actor, admin=False)
    proposal = _locked_proposal(organization, proposal_id)
    _ensure_idle(proposal)
    if not any(section["key"] == key for section in _current_sections(proposal)):
        raise ValidationError({"section": "This proposal has no such section."})
    _queue(
        proposal,
        job=ProposalJob.SECTION,
        actor=actor,
        section=key,
        instructions=instructions.strip(),
    )
    record_event(
        organization,
        actor,
        "proposal.section_rewrite_requested",
        target_id=proposal.pk,
        metadata={"section": key},
    )
    return proposal


@transaction.atomic
def edit_section(*, organization_id, proposal_id, key, actor, base_version, **changes):
    """A person's edit. `base_version` is the version they edited, so two people
    editing at once cannot silently overwrite each other."""
    organization = lock_organization(organization_id, actor, admin=False)
    proposal = _locked_proposal(organization, proposal_id)
    _ensure_idle(proposal)
    if base_version != proposal.version:
        raise Conflict(
            f"The proposal changed since you opened it (now version {proposal.version}). "
            "Reload and apply your edit again.",
            code="version_conflict",
        )
    sections = [dict(section) for section in _current_sections(proposal)]
    section = next((s for s in sections if s["key"] == key), None)
    if section is None:
        raise ValidationError({"section": "This proposal has no such section."})
    changed = {f: v for f, v in changes.items() if f in EDITABLE_FIELDS and section.get(f) != v}
    if not changed:
        return proposal
    section.update(changed)
    if "content" in changed:
        section["drafted"] = True
    _new_version(
        proposal,
        sections,
        source=VersionSource.EDIT,
        actor=actor,
        note=f"Edited “{section['title']}”",
    )
    proposal.save(update_fields=["version", "updated_at"])
    record_event(
        organization,
        actor,
        "proposal.edited",
        target_id=proposal.pk,
        metadata={"section": key, "version": proposal.version},
    )
    return proposal


@transaction.atomic
def restore_version(*, organization_id, proposal_id, number, actor) -> Proposal:
    organization = lock_organization(organization_id, actor, admin=False)
    proposal = _locked_proposal(organization, proposal_id)
    _ensure_idle(proposal)
    old = get_object_or_404(proposal.versions, number=number)
    if number == proposal.version:
        raise Conflict("That is already the current version.", code="already_current")
    _new_version(
        proposal,
        old.sections,
        source=VersionSource.RESTORE,
        actor=actor,
        note=f"Restored version {number}",
    )
    proposal.save(update_fields=["version", "updated_at"])
    record_event(
        organization,
        actor,
        "proposal.restored",
        target_id=proposal.pk,
        metadata={"from": number, "version": proposal.version},
    )
    return proposal


@transaction.atomic
def recheck_documents(*, organization_id, proposal_id, actor) -> Proposal:
    """Check the tender's requirements against company documents again, e.g. after
    uploading a missing certificate. The text is not touched."""
    organization = lock_organization(organization_id, actor, admin=False)
    proposal = _locked_proposal(organization, proposal_id)
    _ensure_idle(proposal)
    if not _is_tender(proposal):
        raise Conflict("Only tenders have a document checklist.", code="not_tender")
    if not proposal.checklist:
        raise Conflict(
            "There is no checklist yet. Write the bid first, or redraft it.", code="no_checklist"
        )
    _queue(proposal, job=ProposalJob.CHECKLIST, actor=actor)
    record_event(organization, actor, "proposal.checklist_recheck_requested", target_id=proposal.pk)
    return proposal


@transaction.atomic
def tick_checklist_item(*, organization_id, proposal_id, key, done, actor) -> Proposal:
    """Mark a requirement as in hand for this bid (or not). Allowed while the AI
    works: a finishing job keeps the ticks."""
    organization = lock_organization(organization_id, actor, admin=False)
    proposal = _locked_proposal(organization, proposal_id)
    checklist = [dict(item) for item in proposal.checklist or []]
    item = next((item for item in checklist if item.get("key") == key), None)
    if item is None:
        raise ValidationError({"key": "This checklist has no such item."})
    if bool(item.get("done")) == done:
        return proposal
    item["done"] = done
    item["done_at"] = timezone.now().isoformat() if done else None
    proposal.checklist = checklist
    proposal.save(update_fields=["checklist", "updated_at"])
    record_event(
        organization,
        actor,
        "proposal.checklist_ticked",
        target_id=proposal.pk,
        metadata={"item": key, "done": done},
    )
    return proposal


# ---------------------------------------------------------------------------
# Workers (no caller)
# ---------------------------------------------------------------------------


def _claimed(proposal_id, started_at):
    return (
        Proposal.objects.select_for_update()
        .filter(pk=proposal_id, status=ProposalStatus.DRAFTING, job_started_at=started_at)
        .first()
    )


@transaction.atomic
def claim_job(proposal_id) -> Proposal | None:
    proposal = Proposal.objects.select_for_update().filter(pk=proposal_id).first()
    if proposal is None or proposal.status != ProposalStatus.QUEUED:
        return None
    proposal.status = ProposalStatus.DRAFTING
    proposal.job_started_at = timezone.now()
    proposal.save(update_fields=["status", "job_started_at", "updated_at"])
    return proposal


@transaction.atomic
def append_event(proposal_id, started_at, event) -> ProposalEvent | None:
    proposal = _claimed(proposal_id, started_at)
    if proposal is None:
        return None
    Proposal.objects.filter(pk=proposal.pk).update(last_event_seq=F("last_event_seq") + 1)
    proposal.refresh_from_db(fields=["last_event_seq"])
    return ProposalEvent.objects.create(
        proposal=proposal,
        seq=proposal.last_event_seq,
        step=event.step,
        level=event.level,
        message=event.message[:500],
        tool=event.tool[:60],
        url=event.url[:1000],
        data=dict(event.data),
        at=event.at,
    )


@transaction.atomic
def save_draft(proposal_id, started_at, result) -> bool:
    proposal = _claimed(proposal_id, started_at)
    if proposal is None:
        return False
    drafted = sum(1 for section in result.sections if section.get("drafted"))
    note = f"AI draft ({drafted} of {len(result.sections)} sections)"
    _new_version(
        proposal,
        result.sections,
        source=VersionSource.AI_DRAFT,
        note=note,
        actor=proposal.job_requested_by,
    )
    proposal.requirements = result.requirements or {}
    proposal.review = result.review
    proposal.drafted_at = timezone.now()
    if result.checklist is not None:
        proposal.checklist = _with_ticks(result.checklist, proposal.checklist)
        proposal.checked_at = timezone.now()
    _finish(proposal, result)
    return True


@transaction.atomic
def save_checklist(proposal_id, started_at, result) -> bool:
    """A document recheck: the new checklist, keeping people's ticks."""
    proposal = _claimed(proposal_id, started_at)
    if proposal is None:
        return False
    proposal.checklist = _with_ticks(result.checklist or [], proposal.checklist)
    proposal.checked_at = timezone.now()
    _finish(proposal, result)
    return True


@transaction.atomic
def save_section(proposal_id, started_at, result) -> bool:
    """Put a rewritten section into a new version, on top of the current text."""
    proposal = _claimed(proposal_id, started_at)
    if proposal is None:
        return False
    [rewritten] = result.sections
    sections = [
        rewritten if section["key"] == rewritten["key"] else section
        for section in _current_sections(proposal)
    ]
    note = f"AI rewrote “{rewritten['title']}”"
    if proposal.job_instructions:
        note += f": {proposal.job_instructions}"
    _new_version(
        proposal,
        sections,
        source=VersionSource.AI_SECTION,
        note=note,
        actor=proposal.job_requested_by,
    )
    _finish(proposal, result)
    return True


def _finish(proposal, result):
    proposal.status = ProposalStatus.READY
    proposal.warning = result.warning[:500]
    proposal.error = ""
    proposal.prompt_versions = {**proposal.prompt_versions, **result.prompt_versions}
    proposal.models_used = {**proposal.models_used, **result.models_used}
    proposal.usage = result.usage
    proposal.job_started_at = None
    proposal.save()


@transaction.atomic
def fail_job(proposal_id, started_at, error) -> bool:
    proposal = _claimed(proposal_id, started_at)
    if proposal is None:
        return False
    proposal.status = ProposalStatus.FAILED
    proposal.error = error[:500]
    proposal.job_started_at = None
    proposal.save(update_fields=["status", "error", "job_started_at", "updated_at"])
    return True


@transaction.atomic
def set_knowledge(*, organization_id, proposal_id, actor, knowledge_mode, folders=()):
    """Choose which company documents this proposal's evidence comes from: all, only
    some folders, none, or "run" to follow the run that found the opportunity. It
    applies to the next draft or section rewrite."""
    organization = lock_organization(organization_id, actor, admin=False)
    proposal = _locked_proposal(organization, proposal_id)
    if knowledge_mode == "run":
        proposal.knowledge = None
    elif knowledge_mode == "selected":
        found = list(organization.folders.filter(pk__in=folders).values_list("pk", flat=True))
        if not folders or len(found) != len(set(folders)):
            raise ValidationError({"folders": "Choose folders of this organization."})
        proposal.knowledge = KnowledgeScope.of_folders(
            [fid for fid in folders if fid in found]
        ).to_config()
    elif knowledge_mode == "none":
        proposal.knowledge = KnowledgeScope.none().to_config()
    else:
        proposal.knowledge = KnowledgeScope.all().to_config()
    proposal.save(update_fields=["knowledge", "updated_at"])
    record_event(
        organization,
        actor,
        "proposal.knowledge_set",
        target_id=proposal.pk,
        metadata={"mode": knowledge_mode},
    )
    return proposal
