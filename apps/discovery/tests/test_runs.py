from datetime import timedelta

import pytest
from django.core.checks import run_checks
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.ai.config.loader import load_sources_file
from apps.ai.events import ListEmitter, RunEvent
from apps.ai.graphs import discovery_runner
from apps.ai.graphs.discovery_runner import DiscoveryRequest
from apps.ai.graphs.fake_discovery import run_fake_discovery
from apps.discovery import selectors, services
from apps.discovery.models import DiscoveryRun
from apps.opportunities.models import Opportunity

from .factories import make_agent, search_settings

# Starting a run hands it over on commit; with JOBS_RUN_INLINE it then runs inline.
pytestmark = pytest.mark.django_db(transaction=True)


def url(name, organization, **kwargs):
    return reverse(f"v1:discovery:{name}", kwargs={"organization_id": organization.pk, **kwargs})


def run_now(client, organization, agent):
    return client.post(url("agent-run", organization, agent_id=agent.pk))


def make_run(organization, agent, **overrides):
    return DiscoveryRun.objects.create(
        organization=organization,
        agent=agent,
        agent_name=agent.name,
        trigger="manual",
        config={"match_threshold": 55},
        **overrides,
    )


def test_run_now_executes_the_fake_runner_end_to_end(auth_client, user, organization):
    search_settings(organization, sources=["web", "grants"])
    agent = make_agent(organization, user)
    response = run_now(auth_client, organization, agent)
    assert response.status_code == 202, response.data

    run = DiscoveryRun.objects.get(pk=response.data["id"])
    assert run.status == "succeeded"
    assert run.runner == "fake"
    assert run.started_at and run.finished_at
    assert run.sources_scanned > 0 and run.pages_read > 0
    assert run.budget["max_searches"] == load_sources_file().discovery.budget.max_searches

    detail = auth_client.get(url("run-detail", organization, run_id=run.pk)).data
    steps = [step["key"] for step in detail["steps"]]
    assert steps == ["profile", "search", "read", "match", "save"]
    assert all(step["status"] in {"done", "warning"} for step in detail["steps"])
    assert all(step["events"] for step in detail["steps"])
    assert detail["steps"][1]["events"][0]["tool"] == "search"
    decisions = {candidate["decision"] for candidate in detail["candidates"]}
    assert decisions <= {"added", "duplicate", "below_threshold"}

    added = [c for c in detail["candidates"] if c["decision"] == "added"]
    assert run.found == len(added) == Opportunity.objects.filter(run=run).count()
    assert all(c["match_score"] >= 55 for c in added)
    opportunity = Opportunity.objects.filter(run=run).first()
    if opportunity:  # every sample says so
        assert opportunity.title.startswith("[Sample]")
        assert opportunity.source_url.startswith("https://example.org/")
        assert opportunity.agent == agent

    agent_data = auth_client.get(url("agent-detail", organization, agent_id=agent.pk)).data
    assert agent_data["last_run_id"] == run.pk
    assert agent_data["last_run_status"] == "succeeded"
    assert agent_data["last_run_found"] == run.found
    assert len(agent_data["last_run_opportunities"]) == run.found
    assert "source_url" in agent_data["last_run_opportunities"][0]


def test_runs_list_and_live_events(auth_client, user, organization):
    search_settings(organization, sources=["web", "grants"])
    agent = make_agent(organization, user)
    run_id = run_now(auth_client, organization, agent).data["id"]

    listing = auth_client.get(url("runs", organization), {"agent": agent.pk})
    assert listing.data["count"] == 1
    assert listing.data["results"][0]["duration_ms"] >= 0
    assert "opportunities" in listing.data["results"][0]
    assert len(listing.data["results"][0]["opportunities"]) == listing.data["results"][0]["found"]
    if listing.data["results"][0]["found"] > 0:
        opp = listing.data["results"][0]["opportunities"][0]
        assert "source_url" in opp
        assert "funder" in opp
    assert auth_client.get(url("runs", organization), {"status": "failed"}).data["count"] == 0
    assert auth_client.get(url("runs", organization), {"status": "nope"}).status_code == 400

    events_url = url("run-events", organization, run_id=run_id)
    first = auth_client.get(events_url).data
    assert first["run_status"] == "succeeded"
    seqs = [event["seq"] for event in first["events"]]
    assert seqs == list(range(1, len(seqs) + 1))
    assert first["last_seq"] == seqs[-1]
    rest = auth_client.get(events_url, {"after": seqs[2]}).data
    assert [event["seq"] for event in rest["events"]] == seqs[3:]
    caught_up = auth_client.get(events_url, {"after": first["last_seq"]}).data
    assert caught_up["events"] == [] and caught_up["last_seq"] == first["last_seq"]
    assert auth_client.get(events_url, {"after": "x"}).status_code == 400


def test_the_same_listing_is_not_added_twice(auth_client, user, organization):
    agent = make_agent(organization, user)
    run_now(auth_client, organization, agent)
    second = DiscoveryRun.objects.get(pk=run_now(auth_client, organization, agent).data["id"])
    # The fake runner repeats one organization-specific candidate in every run.
    assert second.candidates.filter(decision="duplicate").exists()
    titles = list(Opportunity.objects.values_list("title", flat=True))
    assert len(titles) == len(set(titles))


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("disabled", "agent_disabled"),
        ("incomplete", "setup_incomplete"),
        ("busy", "run_in_progress"),
    ],
)
def test_runs_that_cannot_start(auth_client, user, organization, setup, code):
    agent = make_agent(organization, user, enabled=setup != "disabled")
    if setup == "incomplete":
        organization.description = ""
        organization.save()
    if setup == "busy":
        make_run(organization, agent, status="running")
    response = run_now(auth_client, organization, agent)
    assert response.status_code == 409
    assert response.data["code"] == code


def test_a_stale_run_no_longer_blocks_new_ones(auth_client, user, organization, settings):
    agent = make_agent(organization, user)
    stale = make_run(organization, agent, status="running")
    old = timezone.now() - timedelta(minutes=settings.DISCOVERY_STALE_RUN_MINUTES + 1)
    DiscoveryRun.objects.filter(pk=stale.pk).update(created_at=old)
    assert run_now(auth_client, organization, agent).status_code == 202
    stale.refresh_from_db()
    assert stale.status == "failed" and "stopped responding" in stale.error


def test_one_active_run_per_organization_is_enforced_by_the_database(user, organization):
    agent = make_agent(organization, user)
    make_run(organization, agent, status="queued")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_run(organization, agent, status="running")


def test_budget_limits_end_a_run_as_partial(auth_client, user, organization, monkeypatch):
    sources = load_sources_file()
    tight = sources.model_copy(deep=True)
    standard = tight.discovery.depths["standard"]
    tight.discovery.depths["standard"] = standard.model_copy(update={"max_searches": 1})
    monkeypatch.setattr("apps.discovery.services.load_sources_file", lambda: tight)

    agent = make_agent(organization, user)
    run = DiscoveryRun.objects.get(pk=run_now(auth_client, organization, agent).data["id"])
    assert run.status == "partial"
    assert run.error == "Search budget reached (1 searches)."
    assert run.events.filter(level="warning", step="search").exists()
    assert run.usage["searches"] == 1


def test_a_crashing_runner_fails_the_run_cleanly(auth_client, user, organization, monkeypatch):
    def boom(request, emitter, sink):
        emitter.emit(RunEvent(step="search", message="Searching grants.gov"))
        raise RuntimeError("secret internals")

    monkeypatch.setitem(discovery_runner._RUNNERS, "fake", boom)
    agent = make_agent(organization, user)
    run = DiscoveryRun.objects.get(pk=run_now(auth_client, organization, agent).data["id"])
    assert run.status == "failed"
    assert run.error == "The search stopped because of an unexpected error."
    last = run.events.order_by("-seq").first()
    assert (last.step, last.level) == ("search", "error")
    steps = {step["key"]: step["status"] for step in selectors.run_steps(run)}
    assert steps["search"] == "failed" and steps["save"] == "skipped"


def test_an_unknown_runner_is_reported(auth_client, user, organization, settings):
    settings.DISCOVERY_RUNNER = "nowhere"
    agent = make_agent(organization, user)
    run = DiscoveryRun.objects.get(pk=run_now(auth_client, organization, agent).data["id"])
    assert run.status == "failed"
    assert run.error == "Discovery is not configured on the server yet."


def test_a_finished_run_accepts_no_more_writes(user, organization):
    agent = make_agent(organization, user)
    run = make_run(organization, agent)
    assert services.claim_run(run.pk) is not None
    assert services.claim_run(run.pk) is None  # only once
    assert services.finish_run(run.pk, status="succeeded") is True
    assert services.append_event(run.pk, RunEvent(step="save", message="late")) is None
    assert services.finish_run(run.pk, status="failed") is False


def test_steps_of_a_running_run(user, organization):
    agent = make_agent(organization, user)
    run = make_run(organization, agent)
    services.claim_run(run.pk)
    services.append_event(run.pk, RunEvent(step="profile", message="Reading"))
    services.append_event(run.pk, RunEvent(step="search", message="Searching", level="warning"))
    run.refresh_from_db()
    steps = {step["key"]: step["status"] for step in selectors.run_steps(run)}
    assert steps == {
        "profile": "done",
        "search": "running",
        "read": "pending",
        "match": "pending",
        "save": "pending",
    }


class ListSink:
    def __init__(self):
        self.candidates, self.progress_calls = [], []

    def candidate(self, candidate):
        self.candidates.append(candidate)
        return "added"

    def progress(self, **counters):
        self.progress_calls.append(counters)


def fake_request(run_id="run-1", **overrides):
    data = {
        "run_id": run_id,
        "organization_id": "org-1",
        "organization_name": "Acme Africa",
        "agent_name": "Grants",
        "opportunity_types": ["grant", "tender"],
        "keywords": ["water"],
        "source_categories": ["grants", "procurement"],
        "budget": {"max_searches": 20, "max_pages": 40, "max_tokens": 1, "max_wall_seconds": 60},
        "match_threshold": 55,
        "profile": {"sectors": ["health"], "operating_countries": ["KE"]},
    }
    return DiscoveryRequest(**{**data, **overrides})


def test_the_fake_runner_is_deterministic_and_honest():
    outputs = []
    for _ in range(2):
        emitter, sink = ListEmitter(), ListSink()
        outcome = run_fake_discovery(fake_request(), emitter, sink)
        outputs.append(([e.message for e in emitter.events], [c.title for c in sink.candidates]))
    assert outputs[0] == outputs[1]
    assert outcome.status == "succeeded"
    messages, titles = outputs[0]
    assert any(message.startswith("Searching grants.gov") for message in messages)
    assert "No company dossier yet; planning from the profile only" in messages
    assert titles and all(title.startswith("[Sample]") for title in titles)
    # A different run differs, except for the organization's repeating candidate.
    other = ListSink()
    run_fake_discovery(fake_request("run-2"), ListEmitter(), other)
    assert other.candidates[0].title == sink.candidates[0].title
    assert [c.title for c in other.candidates] != titles


def test_discovery_system_checks(settings):
    def ids():
        return {m.id for m in run_checks() if m.id.startswith("discovery")}

    settings.DEBUG = True
    assert ids() == set()
    settings.DEBUG = False
    assert ids() == {"discovery.W001", "discovery.W004"}  # fake runner, inline jobs
    settings.JOBS_RUN_INLINE = False
    assert ids() == {"discovery.W001"}
    settings.DISCOVERY_RUNNER = "nowhere"
    settings.DISCOVERY_STALE_RUN_MINUTES = 1
    settings.DISCOVERY_FAKE_STEP_SECONDS = -1
    assert ids() == {"discovery.E001", "discovery.E002", "discovery.E003"}
