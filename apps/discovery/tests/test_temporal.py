"""Discovery on Temporal, against Temporal's own test server (see the `temporal` fixtures in
the root conftest.py). Deselect with `-m "not temporal"`."""

import asyncio
import dataclasses

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment
from temporalio.worker import Worker

from apps.discovery import jobs, services
from apps.discovery.models import DiscoveryRun
from apps.discovery.temporal import activities
from apps.discovery.temporal.client import run_workflow_id
from apps.discovery.temporal.workflows import (
    WORKER_DIED,
    DiscoveryRunWorkflow,
    RunInput,
    ScheduledSearchesWorkflow,
)
from conftest import wait_for

pytestmark = pytest.mark.temporal


def with_worker(temporal, workflows, activities, start):
    """Run `start(client)` while a worker with these stand-ins serves the test queue. The
    worker is built inside the server's event loop, as Temporal requires."""

    async def run():
        worker = Worker(
            temporal.client,
            task_queue=temporal.queue,
            workflows=workflows,
            activities=activities,
        )
        async with worker:
            return await start(temporal.client)

    return temporal.call(run())


# ---------------------------------------------------------------------------
# Workflows, with stand-in activities (no database)
# ---------------------------------------------------------------------------


def test_a_run_whose_worker_keeps_dying_is_failed_with_a_reason(temporal, temporal_queue):
    attempts, abandoned = [], []

    @activity.defn(name="discovery.execute_run")
    async def dies(run_id: str) -> str:
        attempts.append(activity.info().attempt)
        raise ApplicationError("worker lost")

    @activity.defn(name="discovery.abandon_run")
    async def abandon(run_id: str, error: str) -> bool:
        abandoned.append((run_id, error))
        return True

    result = with_worker(
        temporal,
        [DiscoveryRunWorkflow],
        [dies, abandon],
        lambda client: client.execute_workflow(
            DiscoveryRunWorkflow.run,
            RunInput(run_id="run1", wall_seconds=60, attempts=3),
            id=f"wf-{temporal_queue}",
            task_queue=temporal_queue,
        ),
    )
    assert result == "failed"
    assert attempts == [1, 2, 3]  # retried, then given up
    assert abandoned == [("run1", WORKER_DIED)]


def test_the_scheduler_tick_passes_its_time_to_the_activity(temporal, temporal_queue):
    ticks = []

    @activity.defn(name="discovery.start_due_searches")
    async def start(tick: str) -> int:
        ticks.append(tick)
        return 2

    result = with_worker(
        temporal,
        [ScheduledSearchesWorkflow],
        [start],
        lambda client: client.execute_workflow(
            ScheduledSearchesWorkflow.run, id=f"wf-{temporal_queue}", task_queue=temporal_queue
        ),
    )
    assert result == 2
    assert len(ticks) == 1 and "T" in ticks[0]  # an ISO time: the slot is derived from it


# ---------------------------------------------------------------------------
# End to end: Django hands a run to Temporal, the real worker runs it
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_run_goes_through_temporal_end_to_end(temporal, temporal_worker, organization, user):
    run = services.start_search(organization_id=organization.pk, actor=user)

    def finished():
        run.refresh_from_db()
        return not run.is_active

    assert wait_for(finished)
    assert run.status == "succeeded", run.error
    assert run.dispatched_at is not None and run.dispatch_attempts == 1
    assert run.events.exists()

    handle = temporal.client.get_workflow_handle(run_workflow_id(run.pk))
    assert temporal.call(handle.result()) == "succeeded"
    first = temporal.call(handle.describe()).run_id
    # Handing the same run over again (the sweep, a duplicate) starts nothing new.
    assert jobs.dispatch_run(run.pk) is True
    assert temporal.call(handle.describe()).run_id == first


@pytest.mark.django_db(transaction=True)
def test_a_retried_activity_resumes_the_run(organization, user, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "apps.discovery.tasks.run_discovery",
        lambda run_id, *, resume: calls.append((run_id, resume)) or "partial",
    )
    env = ActivityEnvironment()
    assert asyncio.run(env.run(activities.execute_run, "run1")) == "partial"
    env.info = dataclasses.replace(env.info, attempt=2)
    asyncio.run(env.run(activities.execute_run, "run1"))
    assert calls == [("run1", False), ("run1", True)]
    assert DiscoveryRun.objects.count() == 0
