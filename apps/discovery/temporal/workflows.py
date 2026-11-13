"""Discovery workflows. Deterministic code only: no Django, no I/O, no clock but
workflow.now(). Activities are called by name, so this module never imports them."""

from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

# The activity sends a heartbeat every few seconds while a run works. Missing them for
# this long means its worker died, and Temporal retries the run on another worker.
HEARTBEAT_TIMEOUT = timedelta(minutes=1)
# On top of the run's wall-time budget: saving what was scored after the limit is hit.
FINISH_MARGIN = timedelta(minutes=2)
QUICK = timedelta(seconds=60)

WORKER_DIED = "The search stopped because the server running it kept failing."


@dataclass
class RunInput:
    run_id: str
    wall_seconds: int  # the run's wall-time budget
    attempts: int  # DISCOVERY_RUN_ATTEMPTS


@workflow.defn(name="DiscoveryRun")
class DiscoveryRunWorkflow:
    """One discovery run. Its workflow id is the run's (discovery-run-<id>), so the same
    run can never be started twice."""

    @workflow.run
    async def run(self, data: RunInput) -> str:
        try:
            return await workflow.execute_activity(
                "discovery.execute_run",
                data.run_id,
                start_to_close_timeout=timedelta(seconds=data.wall_seconds) + FINISH_MARGIN,
                heartbeat_timeout=HEARTBEAT_TIMEOUT,
                retry_policy=RetryPolicy(
                    maximum_attempts=data.attempts, initial_interval=timedelta(seconds=10)
                ),
            )
        except ActivityError:
            # Every attempt died or timed out: say so on the run instead of leaving it
            # "running" until the stale-run sweep gives up on it.
            await workflow.execute_activity(
                "discovery.abandon_run",
                args=[data.run_id, WORKER_DIED],
                start_to_close_timeout=QUICK,
                retry_policy=RetryPolicy(maximum_attempts=10),
            )
            return "failed"


@workflow.defn(name="DiscoveryScheduledSearches")
class ScheduledSearchesWorkflow:
    """The hourly scheduler tick, started by a Temporal Schedule."""

    @workflow.run
    async def run(self) -> int:
        # The tick's time decides its slot, so a retried activity starts nothing twice.
        return await workflow.execute_activity(
            "discovery.start_due_searches",
            workflow.now().isoformat(),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


@workflow.defn(name="DiscoveryRecoverRuns")
class RecoverRunsWorkflow:
    """The recovery sweep, started by a Temporal Schedule every few minutes."""

    @workflow.run
    async def run(self) -> dict:
        return await workflow.execute_activity(
            "discovery.recover_runs",
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )


WORKFLOWS = [DiscoveryRunWorkflow, ScheduledSearchesWorkflow, RecoverRunsWorkflow]
