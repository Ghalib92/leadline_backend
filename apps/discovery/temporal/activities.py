"""Discovery activities: the real work behind the workflows, run by the Temporal worker.
They call the same plain functions the tests call (apps.discovery.tasks and .jobs)."""

from datetime import datetime

from temporalio import activity

from apps.common.temporal import heartbeating, in_thread
from apps.discovery import jobs, services, tasks


@activity.defn(name="discovery.execute_run")
async def execute_run(run_id: str) -> str:
    """Run the search, with a heartbeat while it works. A later attempt means an earlier
    worker died mid-run, so the run resumes rather than being claimed afresh."""
    resume = activity.info().attempt > 1
    return await heartbeating(tasks.run_discovery, run_id, resume=resume) or "skipped"


@activity.defn(name="discovery.abandon_run")
async def abandon_run(run_id: str, error: str) -> bool:
    return await in_thread(services.abandon_run, run_id, error=error)


@activity.defn(name="discovery.start_due_searches")
async def start_due_searches(tick: str) -> int:
    return await in_thread(jobs.start_due_searches, now=datetime.fromisoformat(tick))


@activity.defn(name="discovery.recover_runs")
async def recover_runs() -> dict:
    return await in_thread(jobs.recover_runs)


ACTIVITIES = [execute_run, abandon_run, start_due_searches, recover_runs]
