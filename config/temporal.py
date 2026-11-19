"""The Temporal worker and Schedules: every workflow, activity and timer in one place.

Used by `python manage.py temporal_worker`. Apps register background jobs with
apps.common.jobs.job (found in their tasks.py); discovery has its own workflows.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleCalendarSpec,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleRange,
    ScheduleSpec,
    ScheduleUpdate,
)
from temporalio.worker import Worker

from apps.common.jobs import job_activities, registered_jobs
from apps.common.workflows import JobWorkflow
from apps.discovery.temporal import activities as discovery_activities
from apps.discovery.temporal import workflows as discovery_workflows

WORKFLOWS = [JobWorkflow, *discovery_workflows.WORKFLOWS]
# On shutdown (a deploy, Ctrl+C), running jobs get this long to finish. Longer ones are
# cancelled; their heartbeats stop and Temporal retries them on the next worker.
GRACEFUL_SHUTDOWN = timedelta(seconds=30)


def build_worker(client: Client) -> Worker:
    return Worker(
        client,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
        workflows=WORKFLOWS,
        activities=[*job_activities(), *discovery_activities.ACTIVITIES],
        max_concurrent_activities=settings.TEMPORAL_MAX_CONCURRENT_ACTIVITIES,
        graceful_shutdown_timeout=GRACEFUL_SHUTDOWN,
    )


@dataclass(frozen=True)
class Timer:
    workflow: object  # a workflow's run method
    spec: ScheduleSpec
    arg: object = None


def every(interval: timedelta) -> ScheduleSpec:
    return ScheduleSpec(intervals=[ScheduleIntervalSpec(every=interval)])


def weekly_at(weekday: int, hour: int) -> ScheduleSpec:
    """Every week on `weekday` (0 = Monday) at `hour`:00, Nairobi time. Temporal counts
    days of the week from Sunday = 0."""
    return ScheduleSpec(
        calendars=[
            ScheduleCalendarSpec(
                day_of_week=[ScheduleRange((weekday + 1) % 7)],
                hour=[ScheduleRange(hour)],
                minute=[ScheduleRange(0)],
                second=[ScheduleRange(0)],
            )
        ],
        time_zone_name=settings.TIME_ZONE,
    )


def daily_at(hour: int) -> ScheduleSpec:
    """Every day at `hour`:00 in the server's time zone (TIME_ZONE, Nairobi)."""
    return ScheduleSpec(
        calendars=[
            ScheduleCalendarSpec(
                hour=[ScheduleRange(hour)], minute=[ScheduleRange(0)], second=[ScheduleRange(0)]
            )
        ],
        time_zone_name=settings.TIME_ZONE,
    )


def timers() -> dict[str, Timer]:
    """Schedule id → what it starts and when. Each start's workflow id is the schedule id
    plus its time, so Temporal never starts the same tick twice."""
    reminders = registered_jobs()["outreach.send_deadline_reminders"]
    digests = registered_jobs()["outreach.send_weekly_digests"]
    model_checks = registered_jobs()["ai.check_models"]
    return {
        # Daily and weekly saved searches that are due.
        "discovery-scheduled-searches": Timer(
            discovery_workflows.ScheduledSearchesWorkflow.run, every(timedelta(hours=1))
        ),
        # Re-dispatch runs that never reached a worker; fail ones whose worker died.
        "discovery-recover-runs": Timer(
            discovery_workflows.RecoverRunsWorkflow.run, every(timedelta(minutes=5))
        ),
        # Deadline reminder emails.
        "outreach-deadline-reminders": Timer(JobWorkflow.run, daily_at(7), reminders.input([])),
        # The weekly digest email, Monday morning.
        "outreach-weekly-digest": Timer(JobWorkflow.run, weekly_at(0, 7), digests.input([])),
        # One short call per AI provider, tier and key; emails when one is broken.
        "ai-model-check": Timer(JobWorkflow.run, daily_at(6), model_checks.input([])),
    }


async def ensure_schedules(client: Client) -> list[str]:
    """Create the Schedules, or bring existing ones up to date. Safe on every worker start."""
    current = timers()
    for schedule_id, timer in current.items():
        args = [] if timer.arg is None else [timer.arg]
        schedule = Schedule(
            action=ScheduleActionStartWorkflow(
                timer.workflow, args=args, id=schedule_id, task_queue=settings.TEMPORAL_TASK_QUEUE
            ),
            spec=timer.spec,
            # A tick still running when the next is due: skip the new one.
            policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
        )
        try:
            await client.create_schedule(schedule_id, schedule)
        except ScheduleAlreadyRunningError:
            await client.get_schedule_handle(schedule_id).update(
                lambda _, schedule=schedule: ScheduleUpdate(schedule=schedule)
            )
    return list(current)
