"""Reads for agents and runs, always scoped to one organization."""

from datetime import timedelta

from django.db.models import F, OuterRef, Subquery
from django.utils import timezone

from apps.knowledge.selectors import dossier_for
from apps.organizations import choices as org_choices
from apps.organizations.choices import Goal, OpportunityType
from apps.organizations.selectors import (
    client_brief_for,
    goals_in_play,
    org_sources_for,
    preferences_for,
    tender_brief_for,
)

from .choices import (
    GOAL_SCOPES,
    CandidateDecision,
    EventLevel,
    RunStatus,
    RunStep,
    SkipReason,
)
from .models import DiscoveryRun, RunEvent, RunSettings, SearchAgent

EVENTS_PAGE_SIZE = 500


def agents_for(organization):
    """Agents with a summary of their latest run, for the agents list."""
    latest = DiscoveryRun.objects.filter(agent=OuterRef("pk")).order_by("-created_at", "-id")
    return (
        SearchAgent.objects.filter(organization=organization)
        .select_related("owner")
        .annotate(
            last_run_id=Subquery(latest.values("pk")[:1]),
            last_run_at=Subquery(latest.values("finished_at")[:1]),
            last_run_found=Subquery(latest.values("found")[:1]),
            last_run_status=Subquery(latest.values("status")[:1]),
        )
    )


def runs_for(organization, *, agent_id=None, statuses=()):
    runs = DiscoveryRun.objects.filter(organization=organization).prefetch_related("opportunities")
    if agent_id:
        runs = runs.filter(agent_id=agent_id)
    if statuses:
        runs = runs.filter(status__in=statuses)
    return runs


def events_after(run, after: int = 0):
    """Timeline events newer than `after` (a seq), oldest first; for live polling."""
    return run.events.filter(seq__gt=after).order_by("seq")[:EVENTS_PAGE_SIZE]


def current_step(run_id) -> str:
    last = RunEvent.objects.filter(run_id=run_id).order_by("-seq").values("step").first()
    return last["step"] if last else RunStep.PROFILE


def run_steps(run, events=None) -> list[dict]:
    """The five steps of a run, summarised from its events.

    A step starts at its first event and ends where the next step starts (or when the
    run finished). Status: failed/warning when any event says so; running for the
    latest step of an active run; pending (active run) or skipped (finished run) for
    steps with no events.
    """
    events = list(run.events.order_by("seq") if events is None else events)
    by_step: dict[str, list] = {key: [] for key in RunStep.values}
    for event in events:
        by_step.setdefault(event.step, []).append(event)
    started = [key for key in RunStep.values if by_step[key]]
    now = timezone.now()
    steps = []
    for key, label in RunStep.choices:
        step_events = by_step[key]
        if not step_events:
            steps.append(
                {
                    "key": key,
                    "label": label,
                    "status": "pending" if run.is_active else "skipped",
                    "started_at": None,
                    "duration_ms": 0,
                    "events": [],
                }
            )
            continue
        begin = step_events[0].at
        later = [by_step[k][0].at for k in started if by_step[k][0].at > begin]
        end = min(later) if later else (run.finished_at or now)
        levels = {event.level for event in step_events}
        if EventLevel.ERROR in levels:
            status = "failed"
        elif run.is_active and key == started[-1]:
            status = "running"
        elif EventLevel.WARNING in levels:
            status = "warning"
        else:
            status = "done"
        steps.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "started_at": begin,
                "duration_ms": max(0, int((end - begin).total_seconds() * 1000)),
                "events": step_events,
            }
        )
    return steps


def run_outcome(run, events) -> dict:
    """What a run ended with, so a run that added nothing can say why: candidates by
    decision, listings dropped after reading by SkipReason, and the best score."""
    candidates = list(run.candidates.all())
    by_decision = {key: 0 for key in CandidateDecision.values}
    for candidate in candidates:
        by_decision[candidate.decision] = by_decision.get(candidate.decision, 0) + 1
    skipped = {key: 0 for key in SkipReason.values}
    for event in events:
        reason = (event.data or {}).get("skip")
        if reason in skipped:
            skipped[reason] += 1
    return {
        "added": by_decision[CandidateDecision.ADDED],
        "duplicates": by_decision[CandidateDecision.DUPLICATE],
        "below_threshold": by_decision[CandidateDecision.BELOW_THRESHOLD],
        "skipped": skipped,
        "best_score": max((c.match_score for c in candidates), default=None),
        "match_threshold": run.config.get("match_threshold"),
    }


def _goals(organization, goal) -> list[str]:
    """The goal of the run, or every goal the organization works on."""
    return [goal] if goal else goals_in_play(organization)


def _labels(choice_class, values) -> list[str]:
    """Controlled values as the words people (and models) read."""
    labels = dict(choice_class.choices)
    return [labels.get(value, value) for value in values]


def _filled(facts: dict) -> dict:
    return {key: value for key, value in facts.items() if value not in ("", None, [], {})}


def client_brief_context(organization) -> dict:
    brief = client_brief_for(organization)
    return _filled(
        {
            "services": [_filled(service) for service in brief.services],
            "customer_types": _labels(org_choices.BusinessCategory, brief.customer_types),
            "customer_keywords": brief.customer_keywords,
            "customer_sizes": _labels(org_choices.BusinessSize, brief.customer_sizes),
            "target_countries": brief.target_countries,
            "target_cities": brief.target_cities,
            "signs_they_need_us": _labels(org_choices.LeadSignal, brief.signals),
            "notes": brief.notes,
        }
    )


def tender_brief_context(organization) -> dict:
    brief = tender_brief_for(organization)
    return _filled(
        {
            "categories": _labels(org_choices.TenderCategory, brief.categories),
            "keywords": brief.keywords,
            "buyer_types": _labels(org_choices.BuyerType, brief.buyer_types),
            "target_countries": brief.target_countries,
            "currency": brief.currency,
            "min_value": _plain(brief.min_value),
            "max_value": _plain(brief.max_value),
            "eligibility": _labels(org_choices.TenderEligibility, brief.eligibility),
            "certifications": brief.certifications,
            "capacity_notes": brief.capacity_notes,
        }
    )


def profile_context(organization, goal: str | None = None) -> dict:
    """The profile facts a runner plans and filters with (the dossier adds the rest), plus
    the brief of the run's goal: the grant preferences, the tenders brief or the clients
    brief. Without a goal, the briefs of every goal the organization works on."""
    goals = _goals(organization, goal)
    facts = {
        "goal": goal or "",
        "organization_type": organization.organization_type,
        "country": organization.country,
        "operating_countries": organization.operating_countries,
        "sectors": organization.sectors,
        "languages": organization.languages,
        "team_size": organization.team_size,
        "annual_budget": organization.annual_budget,
        "year_founded": organization.year_founded,
        "description": organization.description,
    }
    excluded = []
    if Goal.GRANTS in goals:
        preferences = preferences_for(organization)
        facts |= {
            "geographic_scopes": preferences.geographic_scopes,
            "target_countries": preferences.target_countries,
            "target_regions": preferences.target_regions,
            "focus_areas": preferences.focus_areas,
            "eligibility": preferences.eligibility,
            "preferred_funders": preferences.preferred_funders,
            "excluded_funders": preferences.excluded_funders,
            "funding_currency": preferences.funding_currency,
            "min_funding_amount": _plain(preferences.min_funding_amount),
            "max_funding_amount": _plain(preferences.max_funding_amount),
            "open_to_partnerships": preferences.open_to_partnerships,
            "can_provide_cofunding": preferences.can_provide_cofunding,
        }
        excluded += preferences.excluded_keywords
    if Goal.TENDERS in goals:
        facts["tender_brief"] = tender_brief_context(organization)
        excluded += tender_brief_for(organization).excluded_keywords
    if Goal.CLIENTS in goals:
        facts["client_brief"] = client_brief_context(organization)
        excluded += client_brief_for(organization).excluded_keywords
    facts["excluded_keywords"] = list(dict.fromkeys(excluded))
    return _filled(facts)


def _plain(value):
    return None if value is None else str(value)


def dossier_content(organization) -> dict | None:
    return dossier_for(organization).content


def org_sources_context(organization) -> list[dict]:
    """Enabled custom org sources for discovery searches and source_reader."""
    sources = org_sources_for(organization, enabled_only=True)
    return [
        {
            "id": s.pk,
            "url": s.url,
            "label": s.label,
            "notes": s.notes,
        }
        for s in sources
    ]


SCHEDULE_INTERVALS = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}


def schedule_slot(agent, now=None) -> str:
    """Deterministic, unique slot identifier for an agent's scheduled execution."""
    now = now or timezone.now()
    schedule = getattr(agent, "schedule", "")
    if schedule == "daily":
        return f"{agent.pk}:daily:{now.strftime('%Y-%m-%d')}"
    if schedule == "weekly":
        year, week, _ = now.isocalendar()
        return f"{agent.pk}:weekly:{year}-W{week:02d}"
    return f"{agent.pk}:{schedule}:{now.strftime('%Y-%m-%d')}"


def is_agent_eligible(agent, *, org_ready_cache: dict | None = None) -> bool:
    """True when the agent can be executed on schedule: enabled, scheduled, its goal
    can run, organization active and ready for matching, owner active."""
    organization = agent.organization
    if organization.archived_at is not None:
        return False
    if not agent.enabled or agent.schedule not in SCHEDULE_INTERVALS:
        return False
    if not GOAL_SCOPES[agent.goal].runnable:
        return False
    if not agent.owner_id:
        return False
    from apps.organizations.models import OrganizationMembership

    owner_active = organization.memberships.filter(
        user_id=agent.owner_id, status=OrganizationMembership.Status.ACTIVE
    ).exists()
    if not owner_active:
        return False

    from apps.organizations.selectors import setup_status

    if org_ready_cache is not None and organization.pk in org_ready_cache:
        ready = org_ready_cache[organization.pk]
    else:
        ready = setup_status(organization)["ready_for_matching"]
        if org_ready_cache is not None:
            org_ready_cache[organization.pk] = ready
    return ready


def is_agent_due(agent, now=None) -> bool:
    """True when an agent's last run started at least an interval ago (or it never ran)."""
    if not agent.enabled or agent.schedule not in SCHEDULE_INTERVALS:
        return False
    now = now or timezone.now()
    interval = SCHEDULE_INTERVALS[agent.schedule] - timedelta(minutes=30)
    latest_run_time = (
        DiscoveryRun.objects.filter(agent=agent)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if latest_run_time is None:
        return True
    return latest_run_time <= now - interval


def agents_due(now=None, *, limit: int) -> list[str]:
    """Ids of scheduled agents whose last run (of any kind) started a full interval ago,
    or never ran. Oldest first, so no agent is starved. Ineligible agents are excluded
    before applying the batch limit."""
    now = now or timezone.now()
    due = []
    latest = DiscoveryRun.objects.filter(agent=OuterRef("pk")).order_by("-created_at")
    agents = (
        SearchAgent.objects.filter(enabled=True, schedule__in=SCHEDULE_INTERVALS)
        .filter(organization__archived_at__isnull=True)
        .select_related("organization")
        .annotate(last_run=Subquery(latest.values("created_at")[:1]))
        .order_by(F("last_run").asc(nulls_first=True), "created_at")
    )
    org_ready_cache: dict[str, bool] = {}
    for agent in agents:
        # A little slack so a daily agent run at 07:01 is due again at 07:00 next day.
        interval = SCHEDULE_INTERVALS[agent.schedule] - timedelta(minutes=30)
        if agent.last_run is None or agent.last_run <= now - interval:
            if not is_agent_eligible(agent, org_ready_cache=org_ready_cache):
                continue
            due.append(agent.pk)
            if len(due) >= limit:
                break
    return due


def undelivered_runs(*, created_before) -> list[tuple[str, int]]:
    """(id, hand-over attempts) of queued runs never handed to the job backend, for the
    recovery sweep (apps.discovery.jobs.recover_runs)."""
    return list(
        DiscoveryRun.objects.filter(
            status=RunStatus.QUEUED, dispatched_at__isnull=True, created_at__lt=created_before
        )
        .order_by("created_at")
        .values_list("pk", "dispatch_attempts")
    )


def run_wall_seconds(run_id) -> int:
    """A run's wall-time budget, which bounds how long its worker may take."""
    budget = DiscoveryRun.objects.filter(pk=run_id).values_list("budget", flat=True).first()
    return int((budget or {}).get("max_wall_seconds") or 0)


def run_settings_for(organization):
    """The organization's search settings, created on first use. New settings start from
    the opportunity types in the preferences rather than every type."""
    run_settings = RunSettings.objects.filter(organization=organization).first()
    if run_settings is not None:
        return run_settings
    allowed = set(OpportunityType.values)
    preferred = [t for t in preferences_for(organization).opportunity_types if t in allowed]
    defaults = {"opportunity_types": preferred} if preferred else {}
    return RunSettings.objects.get_or_create(organization=organization, defaults=defaults)[0]


def search_keywords(organization, goal: str | None = None) -> list[str]:
    """The organization's keywords for a run of `goal` (or of every goal it works on):
    the grant preferences' keywords, the tenders brief's (its categories when it has
    none) and the clients brief's customer keywords (its customer types when none)."""
    words = []
    for each in _goals(organization, goal):
        if each == Goal.GRANTS:
            words += preferences_for(organization).keywords
        elif each == Goal.TENDERS:
            brief = tender_brief_for(organization)
            words += brief.keywords or _labels(org_choices.TenderCategory, brief.categories)
        elif each == Goal.CLIENTS:
            brief = client_brief_for(organization)
            words += brief.customer_keywords or _labels(
                org_choices.BusinessCategory, brief.customer_types
            )
    return list(dict.fromkeys(words))


ORG, AGENT = "organization", "agent"


def _tagged(org_values, agent_values):
    """The organization's values, then the agent's that aren't already there."""
    rows = [{"value": v, "origin": ORG} for v in dict.fromkeys(org_values)]
    seen = {row["value"] for row in rows}
    rows += [{"value": v, "origin": AGENT} for v in dict.fromkeys(agent_values) if v not in seen]
    return rows


def search_limits(organization, agent=None) -> dict | None:
    """The opportunity types and source categories a run may use: the agent's goal, or
    for an organization-wide search, the runnable goals the organization picked. None
    means no limit (an organization that has not picked goals)."""
    if agent is not None:
        goals = [agent.goal]
    elif organization.goals:
        goals = [goal for goal in organization.goals if GOAL_SCOPES[goal].runnable]
    else:
        return None
    limits = {"opportunity_types": [], "sources": []}
    for goal in goals:
        scope = GOAL_SCOPES[goal]
        for field, values in (
            ("opportunity_types", scope.opportunity_types),
            ("sources", scope.sources),
        ):
            limits[field] += [v for v in values if v not in limits[field]]
    return limits


def can_search(organization, agent=None) -> bool:
    """Whether a run has anything to search: its goal (or one of the organization's
    goals) has sources yet."""
    limits = search_limits(organization, agent)
    return limits is None or bool(limits["sources"])


def _within(rows, allowed, origin):
    """Keep the rows whose value is allowed; with none left, all allowed values."""
    kept = [row for row in rows if row["value"] in allowed]
    return kept or [{"value": value, "origin": origin} for value in allowed]


def agent_scope(organization, agent=None) -> dict:
    """What a run of `agent` searches: the organization's search settings, plus whatever
    the agent adds. Each part says where it came from (organization or agent). With no
    agent, it is the organization's alone."""
    defaults = run_settings_for(organization)
    org_folders = list(defaults.folders.order_by("name"))
    agent_folders = list(agent.folders.order_by("name")) if agent is not None else []
    org_ids = {f.pk for f in org_folders}
    folders = [(f, ORG) for f in org_folders] + [
        (f, AGENT) for f in agent_folders if f.pk not in org_ids
    ]
    depth = agent.depth if agent is not None and agent.depth else ""
    sources = _tagged(defaults.sources, agent.sources if agent else [])
    types = _tagged(defaults.opportunity_types, agent.opportunity_types if agent else [])
    limits = search_limits(organization, agent)
    if limits is not None:  # only what the goal uses; its defaults when nothing is left
        origin = AGENT if agent is not None else ORG
        sources = _within(sources, limits["sources"], origin)
        types = _within(types, limits["opportunity_types"], origin)
    return {
        "goal": agent.goal if agent is not None else "",
        "depth": {"value": depth or defaults.depth, "origin": AGENT if depth else ORG},
        "sources": sources,
        "opportunity_types": types,
        "keywords": _tagged(
            search_keywords(organization, agent.goal if agent else None),
            agent.keywords if agent else [],
        ),
        "folders": [
            {"id": f.pk, "name": f.name, "purpose": f.purpose, "origin": origin}
            for f, origin in folders
        ],
        "links": _links(organization, agent),
        # The org's documents mode (all, selected, none); folders only matter for selected,
        # or for none when the agent adds its own.
        "knowledge_mode": defaults.knowledge_mode,
    }


def _links(organization, agent) -> list[dict]:
    """The organization's enabled sources, then the agent's enabled links not already there."""
    rows = [
        {"url": s["url"], "label": s["label"], "notes": s["notes"], "origin": ORG}
        for s in org_sources_context(organization)
    ]
    seen = {row["url"] for row in rows}
    for link in agent.links if agent is not None else []:
        if link.get("enabled", True) and link["url"] not in seen:
            seen.add(link["url"])
            rows.append(
                {
                    "url": link["url"],
                    "label": link.get("label", ""),
                    "notes": link.get("notes", ""),
                    "origin": AGENT,
                }
            )
    return rows
