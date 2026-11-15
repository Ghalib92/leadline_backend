"""Executes a discovery run: `run_discovery`, called by the Temporal activity
(apps.discovery.temporal.activities)."""

import dataclasses
import logging

from django.conf import settings
from django.utils import timezone

from apps.ai.events import RunEvent
from apps.ai.exceptions import AIConfigError
from apps.ai.graphs.discovery_runner import DiscoveryRequest, get_runner
from apps.knowledge.scope import KnowledgeScope
from apps.opportunities import selectors as opportunity_selectors

from . import selectors, services
from .choices import RunStatus
from .recorder import RunEventRecorder, RunResultsSink

logger = logging.getLogger(__name__)


def run_discovery(run_id, *, resume=False) -> str | None:
    """Run one discovery run to the end; returns its final status, or None when there was
    nothing to do (already started, finished or deleted).

    `resume`: a retry after the worker running it died. The run starts over, within what
    is left of its budget (limits minus usage, wall time counted from its first start)."""
    run = services.claim_run(run_id, resume=resume)
    if run is None:
        return None
    emitter = RunEventRecorder(run.pk)
    request = _request(run)
    if resume:
        request = dataclasses.replace(request, budget=_budget_left(run))
        emitter.emit(
            RunEvent(
                step=selectors.current_step(run.pk),
                message="The search was interrupted and is continuing with the time left.",
                level="warning",
            )
        )
    try:
        runner = get_runner(run.runner or settings.DISCOVERY_RUNNER)
        outcome = runner(request, emitter, RunResultsSink(run.pk))
    except AIConfigError as exc:
        logger.error("Discovery run %s cannot start: %s", run_id, exc)
        return _fail(run.pk, emitter, "Discovery is not configured on the server yet.")
    except Exception:
        logger.exception("Discovery run %s failed", run_id)
        return _fail(run.pk, emitter, "The search stopped because of an unexpected error.")
    services.finish_run(
        run.pk,
        status=outcome.status,
        error=outcome.warning,
        prompt_versions=outcome.prompt_versions,
        models_used=outcome.models_used,
    )
    return outcome.status


def _fail(run_id, emitter, message):
    emitter.emit(RunEvent(step=selectors.current_step(run_id), message=message, level="error"))
    services.finish_run(run_id, status=RunStatus.FAILED, error=message)
    return RunStatus.FAILED


def _budget_left(run) -> dict:
    used = run.usage or {}
    left = dict(run.budget)
    spent = {"max_searches": "searches", "max_pages": "pages", "max_tokens": "tokens"}
    for limit, key in spent.items():
        if limit in left:
            left[limit] = max(0, left[limit] - int(used.get(key) or 0))
    if "max_wall_seconds" in left and run.started_at:
        elapsed = (timezone.now() - run.started_at).total_seconds()
        left["max_wall_seconds"] = max(0, int(left["max_wall_seconds"] - elapsed))
    return left


def _request(run) -> DiscoveryRequest:
    organization = run.organization
    return DiscoveryRequest(
        run_id=run.pk,
        organization_id=organization.pk,
        organization_name=organization.name,
        agent_name=run.agent_name,
        goal=run.config.get("goal", ""),
        instructions=run.config.get("instructions", ""),
        opportunity_types=run.config.get("opportunity_types", []),
        keywords=run.config.get("keywords", []),
        source_categories=run.config.get("sources", []),
        budget=run.budget,
        match_threshold=run.config.get("match_threshold", 0),
        profile=selectors.profile_context(organization, run.config.get("goal") or None),
        dossier=selectors.dossier_content(organization),
        known_urls=opportunity_selectors.known_urls(organization),
        # Runs record their websites; older ones read the organization's current list.
        org_sources=run.config.get("links") or selectors.org_sources_context(organization),
        folders=run.config.get("folders", []),
        knowledge=KnowledgeScope.of_run(run.config).to_config(),
        client_search=(
            selectors.client_search_plan(organization)
            if run.config.get("goal") == "clients"
            else {}
        ),
    )
