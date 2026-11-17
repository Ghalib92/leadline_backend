"""Writes for search agents and discovery runs.

User actions lock the organization and recheck the caller's role. Members create
agents; changing, deleting or running one takes an admin or the agent's owner.

Run workers have no caller: they claim a queued run, append events and candidates,
and finish it. Each worker write locks the run row and checks the run is still
running, so a run that was given up on (stale) can never be written to again.
Handing runs to the job backend and recovering lost ones is in apps.discovery.jobs.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.ai.config.loader import load_sources_file
from apps.ai.events import RunEvent as TimelineEvent
from apps.common.api.exceptions import Conflict
from apps.knowledge.scope import KnowledgeScope
from apps.opportunities import services as opportunity_services
from apps.organizations.choices import Goal
from apps.organizations.models import OrganizationMembership
from apps.organizations.permissions import require_membership
from apps.organizations.selectors import setup_status
from apps.organizations.services import lock_organization, record_event

from . import selectors
from .choices import (
    ACTIVE_RUN_STATUSES,
    GOAL_SCOPES,
    CandidateDecision,
    KnowledgeMode,
    RunStatus,
    RunTrigger,
)
from .models import DiscoveryRun, RunCandidate, RunEvent, RunSettings, SearchAgent

AGENT_FIELDS = frozenset(
    {
        "name",
        "description",
        "instructions",
        "goal",
        "opportunity_types",
        "keywords",
        "sources",
        "depth",
        "links",
        "schedule",
        "enabled",
    }
)


def _can_manage(membership, agent):
    return (
        membership.role == OrganizationMembership.Role.ADMIN or agent.owner_id == membership.user_id
    )


def _managed_agent(organization, actor, agent_id):
    membership = require_membership(organization=organization, user=actor)
    agent = get_object_or_404(organization.search_agents, pk=agent_id)
    if not _can_manage(membership, agent):
        raise PermissionDenied("Only admins and the agent's owner can change this agent.")
    return agent


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


def _default_goal(organization):
    """The organization's first goal that can run, else its first, else grants."""
    runnable = [goal for goal in organization.goals if GOAL_SCOPES[goal].runnable]
    return (runnable or organization.goals or [Goal.GRANTS])[0]


def _fit_goal(organization, data, agent=None) -> dict:
    """Keep an agent's goal, types and sources consistent (GOAL_SCOPES). A new goal must
    be one the organization picked (when it picked any). Types and sources sent with the
    request must belong to the goal; when only the goal changes, the agent's own types and
    sources that don't fit are dropped."""
    goal = data.get("goal") or (agent.goal if agent is not None else _default_goal(organization))
    new_goal = agent is None or goal != agent.goal
    if new_goal and organization.goals and goal not in organization.goals:
        raise ValidationError(
            {
                "goal": f"Your organization isn't looking for {Goal(goal).label.lower()}. "
                "Add it to what you want to find in Settings first."
            }
        )
    scope = GOAL_SCOPES[goal]
    for field, allowed in (
        ("opportunity_types", scope.opportunity_types),
        ("sources", scope.sources),
    ):
        if field in data:
            outside = [value for value in data[field] if value not in allowed]
            if outside:
                raise ValidationError(
                    {
                        field: f"Not used when looking for {Goal(goal).label.lower()}: "
                        f"{', '.join(outside)}."
                    }
                )
        elif agent is not None and new_goal:
            data[field] = [value for value in getattr(agent, field) if value in allowed]
    return {**data, "goal": goal}


@transaction.atomic
def create_agent(*, organization_id, actor, folders=None, **data):
    organization = lock_organization(organization_id, actor, admin=False)
    agent_folders = resolve_folders(organization, folders or [])
    data = _fit_goal(organization, data)
    agent = SearchAgent.objects.create(organization=organization, owner=actor, **data)
    agent.folders.set(agent_folders)
    record_event(
        organization, actor, "agent.created", target_id=agent.pk, metadata={"name": agent.name}
    )
    return agent


@transaction.atomic
def update_agent(*, organization_id, agent_id, actor, folders=None, **data):
    organization = lock_organization(organization_id, actor, admin=False)
    agent = _managed_agent(organization, actor, agent_id)
    data = _fit_goal(organization, data, agent)
    changed = [
        field for field in AGENT_FIELDS & data.keys() if getattr(agent, field) != data[field]
    ]
    for field in changed:
        setattr(agent, field, data[field])
    if changed:
        agent.save(update_fields=[*changed, "updated_at"])
    if folders is not None:
        new_folders = resolve_folders(organization, folders)
        if {f.pk for f in new_folders} != set(agent.folders.values_list("pk", flat=True)):
            agent.folders.set(new_folders)
            changed.append("folders")
    if changed:
        record_event(
            organization,
            actor,
            "agent.updated",
            target_id=agent.pk,
            metadata={"fields": sorted(changed)},
        )
    return agent


@transaction.atomic
def delete_agent(*, organization_id, agent_id, actor):
    """Its runs and the opportunities it found are kept, without the agent link."""
    organization = lock_organization(organization_id, actor, admin=False)
    agent = _managed_agent(organization, actor, agent_id)
    record_event(
        organization, actor, "agent.deleted", target_id=agent.pk, metadata={"name": agent.name}
    )
    agent.delete()


# ---------------------------------------------------------------------------
# Run settings
# ---------------------------------------------------------------------------


def resolve_folders(organization, ids):
    folders = list(organization.folders.filter(pk__in=ids))
    if len(folders) != len(set(ids)):
        raise ValidationError({"folders": "Choose folders of this organization."})
    return folders


@transaction.atomic
def update_run_settings(
    *,
    organization_id,
    actor,
    depth,
    sources,
    opportunity_types,
    folders,
    knowledge_mode=None,
):
    if knowledge_mode is None:  # callers from before the mode: chosen folders mean "only these"
        knowledge_mode = KnowledgeMode.SELECTED if folders else KnowledgeMode.ALL
    organization = lock_organization(organization_id, actor)
    run_settings, _ = RunSettings.objects.get_or_create(organization=organization)
    run_settings.depth = depth
    run_settings.sources = sources
    run_settings.opportunity_types = opportunity_types
    run_settings.knowledge_mode = knowledge_mode
    run_settings.updated_by = actor
    run_settings.save()
    run_settings.folders.set(resolve_folders(organization, folders))
    record_event(organization, actor, "run_settings.updated", metadata={"depth": depth})
    return run_settings


# ---------------------------------------------------------------------------
# Starting a run
# ---------------------------------------------------------------------------


def expire_stale_runs(organization=None) -> int:
    """Fail runs whose worker died, so they stop blocking new runs. Called before a run
    starts and, for every organization, by the recovery sweep."""
    cutoff = timezone.now() - timedelta(minutes=settings.DISCOVERY_STALE_RUN_MINUTES)
    stale = DiscoveryRun.objects.filter(status__in=ACTIVE_RUN_STATUSES, created_at__lt=cutoff)
    if organization is not None:
        stale = stale.filter(organization=organization)
    return stale.update(
        status=RunStatus.FAILED,
        finished_at=timezone.now(),
        error="The run stopped responding and was cancelled.",
        updated_at=timezone.now(),
    )


@transaction.atomic
def start_search(*, organization_id, actor, overrides=None):
    """ "Search for opportunities": a run from the organization's search settings, with
    no saved search. Any member may start one. `overrides` apply to this run only."""
    organization = lock_organization(organization_id, actor, admin=False)
    if not selectors.can_search(organization):
        raise Conflict(
            "Searching for what your organization wants to find arrives in the next update.",
            code="goal_not_available",
        )
    _check_can_start(organization)
    return _create_run(
        organization, None, actor=actor, trigger=RunTrigger.MANUAL, overrides=overrides
    )


@transaction.atomic
def start_run(*, organization_id, agent_id, actor, trigger=RunTrigger.MANUAL, overrides=None):
    """Run a saved search now. `overrides` (depth, sources, opportunity_types, folders)
    apply to this run only."""
    organization = lock_organization(organization_id, actor, admin=False)
    agent = _managed_agent(organization, actor, agent_id)
    if not agent.enabled:
        raise Conflict("Turn this agent on before running it.", code="agent_disabled")
    if not selectors.can_search(organization, agent):
        raise Conflict(
            f"Searching for {Goal(agent.goal).label.lower()} arrives in the next update. "
            "You can set this worker up now.",
            code="goal_not_available",
        )
    _check_can_start(organization)
    return _create_run(organization, agent, actor=actor, trigger=trigger, overrides=overrides)


def _check_can_start(organization):
    if not setup_status(organization)["ready_for_matching"]:
        raise Conflict(
            "Finish the organization setup (profile and preferences) before searching.",
            code="setup_incomplete",
        )
    expire_stale_runs(organization)
    if organization.discovery_runs.filter(status__in=ACTIVE_RUN_STATUSES).exists():
        raise Conflict(
            "A search is already running for this organization. Wait for it to finish.",
            code="run_in_progress",
        )


@transaction.atomic
def start_scheduled_run(agent_id, *, slot: str | None = None, now=None) -> DiscoveryRun | None:
    """Run a daily or weekly agent on its schedule. Quietly skips (returns None) when it
    should not run now: turned off, setup incomplete, owner gone, not due, already ran for this
    slot, or another run active."""
    from apps.organizations.models import Organization

    now = now or timezone.now()
    agent = SearchAgent.objects.filter(pk=agent_id).first()
    if agent is None:
        return None
    organization = Organization.objects.select_for_update().get(pk=agent.organization_id)
    agent.refresh_from_db()

    # Recheck eligibility
    if not selectors.is_agent_eligible(agent):
        return None

    # Recheck whether agent is due inside the locked scheduling operation
    if not selectors.is_agent_due(agent, now=now):
        return None

    # Give scheduled executions a unique slot identifier
    slot_id = slot or selectors.schedule_slot(agent, now=now)
    if slot_id and DiscoveryRun.objects.filter(agent=agent, schedule_slot=slot_id).exists():
        return None

    expire_stale_runs(organization)
    if organization.discovery_runs.filter(status__in=ACTIVE_RUN_STATUSES).exists():
        return None  # tried again on the next tick
    return _create_run(
        organization,
        agent,
        actor=None,
        trigger=RunTrigger.SCHEDULE,
        schedule_slot=slot_id,
    )


OVERRIDABLE = ("depth", "sources", "opportunity_types", "folders", "knowledge_mode")

# The run name shown in the history for "Search for opportunities" (no saved search).
ORG_SEARCH_NAME = "Opportunity search"


def _resolve_run_config(organization, agent, overrides):
    """The organization's search settings plus what the agent adds (see
    selectors.agent_scope), with the request's `overrides` (Configure this run) on top."""
    overrides = dict(overrides or {})
    limits = selectors.search_limits(organization, agent)
    if limits is not None:  # a run may narrow its goal, never leave it
        for field in ("opportunity_types", "sources"):
            if field in overrides:
                allowed = limits[field]
                overrides[field] = [v for v in overrides[field] if v in allowed] or allowed
    scope = selectors.agent_scope(organization, agent)

    def values(field):
        return [row["value"] for row in scope[field]]

    if "folders" in overrides:
        folders = [
            {"id": f.pk, "name": f.name, "purpose": f.purpose}
            for f in resolve_folders(organization, overrides["folders"])
        ]
    else:
        folders = [{k: f[k] for k in ("id", "name", "purpose")} for f in scope["folders"]]
    knowledge = _knowledge(organization, agent, overrides, folders)
    # Only the folders the run may read; with all or no documents there are none to list.
    folders = [f for f in folders if f["id"] in knowledge.folder_ids]
    return {
        "goal": scope["goal"],
        "depth": overrides.get("depth", scope["depth"]["value"]),
        "opportunity_types": overrides.get("opportunity_types", values("opportunity_types")),
        "keywords": values("keywords"),
        "sources": overrides.get("sources", values("sources")),
        # Frozen with name and purpose, so the run's history survives renames.
        "folders": folders,
        "instructions": getattr(agent, "instructions", "") if agent is not None else "",
        # The websites source_reader reads, frozen like the folders.
        "links": [{k: link[k] for k in ("url", "label", "notes")} for link in scope["links"]],
        # Which documents the AI may read (apps.knowledge.scope.KnowledgeScope).
        "knowledge": knowledge.to_config(),
        "overrides": sorted(field for field in OVERRIDABLE if field in overrides),
        # Where each part came from (organization, agent or this run), for the run page.
        "origins": _origins(scope, overrides),
    }


def _knowledge(organization, agent, overrides, folders) -> KnowledgeScope:
    """The org's mode decides; an automation's extra folders widen "none" to those
    folders (it can only add), and a run may choose for itself."""
    folder_ids = [f["id"] for f in folders]
    mode = overrides.get("knowledge_mode")
    if mode is None and "folders" in overrides:
        mode = KnowledgeMode.SELECTED if overrides["folders"] else KnowledgeMode.ALL
    if mode is None:
        mode = selectors.run_settings_for(organization).knowledge_mode
        if mode == KnowledgeMode.NONE and agent is not None:
            own = [f.pk for f in agent.folders.all()]
            if own:
                return KnowledgeScope.of_folders(sorted(own, key=folder_ids.index))
    if mode == KnowledgeMode.ALL:
        return KnowledgeScope.all()
    if mode == KnowledgeMode.NONE:
        return KnowledgeScope.none()
    return KnowledgeScope.of_folders(folder_ids)


def _origins(scope, overrides) -> dict:
    def tagged(field, key="value"):
        if field in overrides:
            values = overrides[field]
            return {str(v): "run" for v in values}
        return {str(row[key]): row["origin"] for row in scope[field]}

    return {
        "depth": "run" if "depth" in overrides else scope["depth"]["origin"],
        "sources": tagged("sources"),
        "opportunity_types": tagged("opportunity_types"),
        "keywords": tagged("keywords"),
        "folders": tagged("folders", "id"),
        "links": {row["url"]: row["origin"] for row in scope["links"]},
    }


def _create_run(organization, agent, *, actor, trigger, overrides=None, schedule_slot=None):
    sources_file = load_sources_file()
    discovery = sources_file.discovery
    config = _resolve_run_config(organization, agent, overrides)
    clients = config["goal"] == "clients"
    threshold = sources_file.clients.match_threshold if clients else discovery.match_threshold
    budget = (
        sources_file.clients.budget_for(config["depth"], discovery)
        if clients
        else discovery.budget_for(config["depth"])
    )
    if schedule_slot:
        config["schedule_slot"] = schedule_slot
    name = agent.name if agent is not None else ORG_SEARCH_NAME
    run = DiscoveryRun.objects.create(
        organization=organization,
        agent=agent,
        agent_name=name,
        trigger=trigger,
        triggered_by=actor,
        config={**config, "match_threshold": threshold},
        budget=budget,
        runner=settings.DISCOVERY_RUNNER,
        schedule_slot=schedule_slot,
    )
    record_event(
        organization,
        actor,
        "run.started",
        target_id=run.pk,
        metadata={
            "agent": name,
            "trigger": trigger,
            "depth": config["depth"],
            "folders": [f["name"] for f in config.get("folders", [])],
        },
    )

    from .jobs import dispatch_run

    # Handed over only once the run is committed; a failed hand-over is recorded on the
    # run, and the recovery sweep tries again.
    transaction.on_commit(lambda: dispatch_run(run.pk), robust=True)
    return run


# ---------------------------------------------------------------------------
# Run workers (no caller)
# ---------------------------------------------------------------------------


def _running(run_id):
    return (
        DiscoveryRun.objects.select_for_update().filter(pk=run_id, status=RunStatus.RUNNING).first()
    )


def record_dispatch(run_id, *, delivered: bool) -> None:
    """Count one hand-over to the job backend; a delivered run is no longer the recovery
    sweep's to send again."""
    changes = {"dispatch_attempts": F("dispatch_attempts") + 1}
    if delivered:
        changes["dispatched_at"] = timezone.now()
    DiscoveryRun.objects.filter(pk=run_id).update(**changes)


@transaction.atomic
def claim_run(run_id, *, resume=False) -> DiscoveryRun | None:
    """Start a queued run. With `resume` (a retry after its worker died) a run that is
    already running is handed back too."""
    run = DiscoveryRun.objects.select_for_update().filter(pk=run_id).first()
    if run is None:
        return None
    if run.status == RunStatus.QUEUED:
        run.status = RunStatus.RUNNING
        run.started_at = timezone.now()
        run.save(update_fields=["status", "started_at", "updated_at"])
        return run
    if resume and run.status == RunStatus.RUNNING:
        return run
    return None


@transaction.atomic
def append_event(run_id, event) -> RunEvent | None:
    """Store one apps.ai.events.RunEvent with the run's next sequence number."""
    run = _running(run_id)
    if run is None:
        return None
    run.last_event_seq = F("last_event_seq") + 1
    run.save(update_fields=["last_event_seq"])
    run.refresh_from_db(fields=["last_event_seq"])
    return RunEvent.objects.create(
        run=run,
        seq=run.last_event_seq,
        step=event.step,
        level=event.level,
        message=event.message[:500],
        tool=event.tool[:60],
        source=event.source[:120],
        url=event.url[:1000],
        data=dict(event.data),
        at=event.at,
    )


@transaction.atomic
def record_progress(run_id, *, sources_scanned, pages_read, usage) -> bool:
    run = _running(run_id)
    if run is None:
        return False
    run.sources_scanned, run.pages_read = sources_scanned, pages_read
    run.usage = {**run.usage, **usage}
    run.save(update_fields=["sources_scanned", "pages_read", "usage", "updated_at"])
    return True


@transaction.atomic
def record_candidate(run_id, candidate) -> str | None:
    """Dedupe and save one apps.ai.schemas.discovery.Candidate; returns the decision."""
    run = _running(run_id)
    if run is None:
        return None
    opportunity = None
    if candidate.match_score < run.config.get("match_threshold", 0):
        decision = CandidateDecision.BELOW_THRESHOLD
    else:
        opportunity, created = opportunity_services.add_discovered(run=run, candidate=candidate)
        decision = CandidateDecision.ADDED if created else CandidateDecision.DUPLICATE
        if created:
            run.found = F("found") + 1
            run.save(update_fields=["found"])
    RunCandidate.objects.create(
        run=run,
        title=candidate.title,
        funder=candidate.funder,
        source_name=candidate.source_name,
        source_url=str(candidate.source_url),
        opportunity_type=candidate.opportunity_type,
        match_score=candidate.match_score,
        match_reasons=candidate.match_reasons,
        decision=decision,
        opportunity=opportunity,
    )
    return decision


@transaction.atomic
def abandon_run(run_id, *, error) -> bool:
    """Fail a queued or running run from outside its worker: it could not be started, or
    the workers running it kept dying."""
    run = (
        DiscoveryRun.objects.select_for_update()
        .filter(pk=run_id, status__in=ACTIVE_RUN_STATUSES)
        .first()
    )
    if run is None:
        return False
    if run.status == RunStatus.RUNNING:
        append_event(
            run_id,
            TimelineEvent(step=selectors.current_step(run_id), message=error, level="error"),
        )
    run.status = RunStatus.FAILED
    run.error = error[:500]
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at", "updated_at"])
    return True


@transaction.atomic
def finish_run(run_id, *, status, error="", prompt_versions=None, models_used=None) -> bool:
    run = _running(run_id)
    if run is None:
        return False
    run.status = status
    run.error = error[:500]
    run.finished_at = timezone.now()
    run.prompt_versions = prompt_versions or {}
    run.models_used = models_used or {}
    run.save(
        update_fields=[
            "status",
            "error",
            "finished_at",
            "prompt_versions",
            "models_used",
            "updated_at",
        ]
    )
    return True
