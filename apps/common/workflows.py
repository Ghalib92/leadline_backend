"""The generic `Job` workflow: run one registered job (apps.common.jobs) as an activity.

Deterministic code only (Temporal replays it): no Django, no I/O. The activity is called
by the job's name, so this module never imports it.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

HEARTBEAT_TIMEOUT = timedelta(minutes=1)


@dataclass
class JobInput:
    name: str  # the job's name, e.g. "proposals.run_proposal_job"
    args: list = field(default_factory=list)
    timeout_seconds: int = 300
    attempts: int = 3


@workflow.defn(name="Job")
class JobWorkflow:
    @workflow.run
    async def run(self, job: JobInput) -> None:
        await workflow.execute_activity(
            job.name,
            job.args,
            start_to_close_timeout=timedelta(seconds=job.timeout_seconds),
            heartbeat_timeout=HEARTBEAT_TIMEOUT,
            retry_policy=RetryPolicy(
                maximum_attempts=job.attempts, initial_interval=timedelta(seconds=5)
            ),
        )
