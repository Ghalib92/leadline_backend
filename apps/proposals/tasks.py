"""Runs proposal jobs (full drafts and section rewrites) in the background."""

import logging
from datetime import timedelta

from django.conf import settings

from apps.ai.config.loader import load_proposals_file
from apps.ai.events import RunEvent
from apps.ai.exceptions import AIConfigError
from apps.ai.graphs.proposal_runner import ProposalRequest, SectionRewriteFailed, get_writer
from apps.common.jobs import job

from . import selectors, services
from .choices import ProposalJob
from .recorder import ProposalEventRecorder

logger = logging.getLogger(__name__)


# A job past PROPOSAL_STALE_MINUTES no longer blocks new ones; it stops by then too.
@job("proposals.run_proposal_job", timeout=timedelta(minutes=settings.PROPOSAL_STALE_MINUTES))
def run_proposal_job(proposal_id):
    proposal = services.claim_job(proposal_id)
    if proposal is None:  # already claimed, finished or deleted
        return
    started_at = proposal.job_started_at
    emitter = ProposalEventRecorder(proposal.pk, started_at)
    step = "draft" if proposal.job == ProposalJob.SECTION else "requirements"
    try:
        writer = get_writer(proposal.runner or settings.PROPOSAL_RUNNER)
        request = ProposalRequest(
            proposal_id=proposal.pk,
            organization_id=proposal.organization_id,
            budget=load_proposals_file().budget.model_dump(),
            **selectors.writer_inputs(proposal),
        )
        if proposal.job == ProposalJob.SECTION:
            current = selectors.current_version(proposal)
            section = next(s for s in current.sections if s["key"] == proposal.job_section)
            result = writer.rewrite_section(
                request,
                emitter,
                section=section,
                requirements=proposal.requirements,
                instructions=proposal.job_instructions,
            )
            services.save_section(proposal.pk, started_at, result)
        else:
            result = writer.draft(request, emitter)
            services.save_draft(proposal.pk, started_at, result)
    except SectionRewriteFailed as exc:
        _fail(proposal.pk, started_at, emitter, step, str(exc))
    except AIConfigError as exc:
        logger.error("Proposal job %s cannot run: %s", proposal_id, exc)
        _fail(
            proposal.pk,
            started_at,
            emitter,
            step,
            "The Proposal Writer is not configured on the server yet.",
        )
    except Exception:
        logger.exception("Proposal job %s failed", proposal_id)
        _fail(
            proposal.pk,
            started_at,
            emitter,
            step,
            "Writing stopped because of an unexpected error.",
        )


def _fail(proposal_id, started_at, emitter, step, message):
    emitter.emit(RunEvent(step=step, message=message, level="error"))
    services.fail_job(proposal_id, started_at, message)
