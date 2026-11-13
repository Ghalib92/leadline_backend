"""Runs never get stranded (apps.discovery.jobs): a failed hand-over to Temporal is
recorded and retried by the recovery sweep, runs whose worker died are failed without
waiting for anyone, and a retried run resumes within what is left of its budget."""

from datetime import timedelta

import pytest
from django.core.checks import run_checks
from django.utils import timezone

from apps.ai.graphs import discovery_runner
from apps.ai.graphs.discovery_runner import DiscoveryOutcome
from apps.discovery import jobs, services, tasks
from apps.discovery.models import DiscoveryRun

from .factories import make_agent

# Starting a run hands it over on commit; with JOBS_RUN_INLINE it then runs inline.
pytestmark = pytest.mark.django_db(transaction=True)


class TemporalDown:
    """Starting a workflow fails, as when the Temporal server is unreachable."""

    def __init__(self, settings, monkeypatch):
        self.settings, self.monkeypatch = settings, monkeypatch
        settings.JOBS_RUN_INLINE = False
        monkeypatch.setattr(
            "apps.discovery.temporal.client.start_run_workflow", self.unreachable
        )

    @staticmethod
    def unreachable(run_id, *, wall_seconds):
        raise TimeoutError("temporal unreachable")

    def recover(self):
        """Back up; the inline runner stands in for the worker."""
        self.settings.JOBS_RUN_INLINE = True


@pytest.fixture
def temporal_down(settings, monkeypatch):
    return TemporalDown(settings, monkeypatch)


def start(organization, user):
    return services.start_search(organization_id=organization.pk, actor=user)


def age(run, **delta):
    DiscoveryRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(**delta))


def test_a_failed_hand_over_is_recorded_and_the_sweep_delivers_it(
    organization, user, temporal_down
):
    run = start(organization, user)
    run.refresh_from_db()
    assert run.status == "queued"
    assert run.dispatched_at is None and run.dispatch_attempts == 1

    assert jobs.recover_runs()["redispatched"] == 0  # a new run gets a moment first
    temporal_down.recover()
    age(run, minutes=2)
    assert jobs.recover_runs() == {"expired": 0, "redispatched": 1, "failed": 0}

    run.refresh_from_db()
    assert run.status == "succeeded"  # delivered, and run
    assert run.dispatched_at is not None and run.dispatch_attempts == 2


def test_a_run_that_cannot_be_handed_over_fails_clearly(
    organization, user, temporal_down, settings
):
    settings.DISCOVERY_DISPATCH_ATTEMPTS = 2
    run = start(organization, user)
    age(run, minutes=2)
    assert jobs.recover_runs()["redispatched"] == 0  # tried, failed again (attempt 2)
    assert jobs.recover_runs()["failed"] == 1

    run.refresh_from_db()
    assert run.status == "failed"
    assert run.error == jobs.NOT_STARTED
    assert run.finished_at is not None
    start(organization, user)  # no longer blocks the organization


def test_a_delivered_run_is_not_sent_again(organization, user, settings, monkeypatch):
    sent = []
    settings.JOBS_RUN_INLINE = False
    monkeypatch.setattr(  # delivered, not yet run
        "apps.discovery.temporal.client.start_run_workflow",
        lambda run_id, *, wall_seconds: sent.append(run_id),
    )
    run = start(organization, user)
    age(run, minutes=2)
    jobs.recover_runs()
    assert sent == [run.pk]


def test_the_sweep_fails_runs_whose_worker_died(organization, user, settings):
    agent = make_agent(organization, user)
    run = DiscoveryRun.objects.create(
        organization=organization,
        agent=agent,
        agent_name=agent.name,
        trigger="manual",
        status="running",
        dispatched_at=timezone.now(),
    )
    age(run, minutes=settings.DISCOVERY_STALE_RUN_MINUTES + 1)
    assert jobs.recover_runs()["expired"] == 1
    run.refresh_from_db()
    assert run.status == "failed" and "stopped responding" in run.error


# ---------------------------------------------------------------------------
# Resuming after a worker died (Temporal retries the activity)
# ---------------------------------------------------------------------------


@pytest.fixture
def captured(monkeypatch):
    requests = []

    def runner(request, emitter, sink):
        requests.append(request)
        return DiscoveryOutcome(status="succeeded")

    monkeypatch.setitem(discovery_runner._RUNNERS, "fake", runner)
    return requests


def running_run(organization, user, *, started_ago, usage):
    agent = make_agent(organization, user)
    return DiscoveryRun.objects.create(
        organization=organization,
        agent=agent,
        agent_name=agent.name,
        trigger="manual",
        status="running",
        runner="fake",
        started_at=timezone.now() - started_ago,
        budget={"max_searches": 12, "max_pages": 40, "max_tokens": 200000, "max_wall_seconds": 600},
        usage=usage,
    )


def test_a_resumed_run_continues_within_what_is_left_of_its_budget(
    organization, user, captured
):
    run = running_run(
        organization,
        user,
        started_ago=timedelta(seconds=200),
        usage={"searches": 5, "pages": 10, "tokens": 50000},
    )
    assert tasks.run_discovery(run.pk) is None  # a running run is not claimed again…
    assert tasks.run_discovery(run.pk, resume=True) == "succeeded"  # …unless resumed

    budget = captured[0].budget
    assert (budget["max_searches"], budget["max_pages"], budget["max_tokens"]) == (7, 30, 150000)
    assert 395 <= budget["max_wall_seconds"] <= 400
    run.refresh_from_db()
    assert run.status == "succeeded"
    warning = run.events.get(level="warning")
    assert "interrupted" in warning.message


def test_a_resumed_run_past_its_time_gets_no_time(organization, user, captured):
    run = running_run(organization, user, started_ago=timedelta(minutes=30), usage={})
    tasks.run_discovery(run.pk, resume=True)
    assert captured[0].budget["max_wall_seconds"] == 0


def test_abandoning_a_run(organization, user):
    run = running_run(organization, user, started_ago=timedelta(minutes=1), usage={})
    assert services.abandon_run(run.pk, error="The workers kept failing.") is True
    run.refresh_from_db()
    assert (run.status, run.error) == ("failed", "The workers kept failing.")
    assert run.events.get(level="error").message == "The workers kept failing."
    assert services.abandon_run(run.pk, error="again") is False  # finished runs stay as they are


def test_job_backend_checks(settings):
    settings.TEMPORAL_ADDRESS = ""
    settings.DISCOVERY_RUN_ATTEMPTS = 0
    ids = {m.id for m in run_checks()}
    assert {"discovery.E007", "discovery.E008"} <= ids
