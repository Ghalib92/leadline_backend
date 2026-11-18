"""Background jobs (apps.common.jobs): registered with @job, started after commit, run by
the Temporal worker; inline in tests (JOBS_RUN_INLINE)."""

from datetime import timedelta

import pytest
from django.db import transaction
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from apps.common.jobs import enqueue, job, registered_jobs
from apps.common.workflows import JobInput, JobWorkflow
from conftest import wait_for

calls = []


@job("tests.record", timeout=timedelta(seconds=30), attempts=2)
def record(value, extra=None):
    calls.append((value, extra))


@pytest.fixture(autouse=True)
def clear_calls():
    calls.clear()


def test_every_background_job_is_registered():
    jobs = registered_jobs()
    assert {
        "knowledge.process_document",
        "knowledge.refresh_dossier",
        "learning.refresh_suggestions",
        "opportunities.embed_review",
        "opportunities.analyze_opportunity",
        "proposals.run_proposal_job",
        "outreach.find_contacts",
        "outreach.write_email",
        "outreach.send_email",
        "outreach.send_deadline_reminders",
    } <= set(jobs)
    # An email must never go out twice, so its job is never retried.
    assert jobs["outreach.send_email"].attempts == 1
    assert record.job.input(["a"]) == JobInput(
        name="tests.record", args=["a"], timeout_seconds=30, attempts=2
    )


@pytest.mark.django_db(transaction=True)
def test_jobs_start_only_after_commit():
    with transaction.atomic():
        enqueue(record, "kept")
        assert calls == []  # not before the rows are committed
    assert calls == [("kept", None)]

    with pytest.raises(RuntimeError), transaction.atomic():
        enqueue(record, "rolled back")
        raise RuntimeError
    assert calls == [("kept", None)]  # a rolled-back request starts nothing


@pytest.mark.temporal
@pytest.mark.django_db(transaction=True)
def test_a_job_runs_on_the_temporal_worker(temporal_worker):
    enqueue(record, "abc", {"force": True})
    assert wait_for(lambda: calls)
    assert calls == [("abc", {"force": True})]


@pytest.mark.temporal
def test_a_failing_job_is_retried_then_fails(temporal, temporal_queue):
    attempts = []

    @activity.defn(name="tests.flaky")
    async def flaky(args: list) -> None:
        attempts.append(activity.info().attempt)
        raise ApplicationError("down")

    async def run():
        async with Worker(
            temporal.client, task_queue=temporal_queue, workflows=[JobWorkflow], activities=[flaky]
        ):
            await temporal.client.execute_workflow(
                JobWorkflow.run,
                JobInput(name="tests.flaky", attempts=3),
                id=f"job-{temporal_queue}",
                task_queue=temporal_queue,
            )

    with pytest.raises(WorkflowFailureError):
        temporal.call(run())
    assert attempts == [1, 2, 3]


@pytest.mark.temporal
def test_schedules_are_created_once_and_updated_after(temporal_dev, settings):
    from config.temporal import ensure_schedules, timers

    settings.TEMPORAL_TASK_QUEUE = "test-schedules"

    async def twice(client):
        first = await ensure_schedules(client)
        await ensure_schedules(client)  # a worker restart: updates, never duplicates
        described = [await client.get_schedule_handle(i).describe() for i in first]
        return first, described

    first, described = temporal_dev.call(twice(temporal_dev.client))
    assert first == list(timers())
    for schedule in described:
        assert schedule.schedule.action.task_queue == "test-schedules"
        assert not schedule.schedule.state.paused
    reminders = dict(zip(first, described, strict=True))["outreach-deadline-reminders"]
    spec = reminders.schedule.spec
    assert spec.time_zone_name == "Africa/Nairobi"
    assert spec.calendars[0].hour[0].start == 7
    digest = dict(zip(first, described, strict=True))["outreach-weekly-digest"].schedule.spec
    assert digest.time_zone_name == "Africa/Nairobi"
    assert digest.calendars[0].day_of_week[0].start == 1  # Monday (Temporal: Sunday is 0)
    assert digest.calendars[0].hour[0].start == 7
