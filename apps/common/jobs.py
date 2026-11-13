"""Background jobs, run by the Temporal worker.

A job is a plain function in an app's tasks.py, registered with `@job`:

    @job("proposals.run_proposal_job", timeout=timedelta(minutes=20))
    def run_proposal_job(proposal_id): ...

`enqueue(run_proposal_job, proposal.pk)` starts it once the current transaction commits
(so the worker sees the saved rows), as a `Job` workflow (apps.common.workflows) that runs
the function as an activity, with the job's timeout and retries. A job may run more than
once (a retry after a worker died), so each one checks or claims its own state first.

Tests set JOBS_RUN_INLINE: jobs then run inline on commit, with no Temporal server.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils.module_loading import autodiscover_modules
from temporalio import activity

from apps.common import temporal
from apps.common.workflows import JobInput, JobWorkflow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobSpec:
    name: str
    fn: object
    timeout: timedelta
    attempts: int

    def input(self, args) -> JobInput:
        return JobInput(
            name=self.name,
            args=list(args),
            timeout_seconds=int(self.timeout.total_seconds()),
            attempts=self.attempts,
        )


_JOBS: dict[str, JobSpec] = {}


def job(name, *, timeout=timedelta(minutes=5), attempts=3):
    """Register a function as a job. `attempts=1` for jobs that must never repeat."""

    def register(fn):
        fn.job = _JOBS[name] = JobSpec(name=name, fn=fn, timeout=timeout, attempts=attempts)
        return fn

    return register


def registered_jobs() -> dict[str, JobSpec]:
    autodiscover_modules("tasks")
    return dict(_JOBS)


def enqueue(fn, *args, delay: timedelta | None = None) -> None:
    """Run job `fn(*args)` in the background after commit (now, outside a transaction).
    Arguments must be JSON-serializable (ids, not objects)."""
    transaction.on_commit(lambda: _start(fn, args, delay), robust=True)


def _start(fn, args, delay):
    if settings.JOBS_RUN_INLINE:
        fn(*args)
        return
    temporal.run(start_job, fn.job, args, delay=delay)


async def start_job(client, spec: JobSpec, args, *, delay=None) -> None:
    # Readable in the Temporal UI: the job, its first argument, and a unique suffix.
    key = "-".join([spec.name, *(str(a) for a in args[:1]), uuid.uuid4().hex[:8]])
    await client.start_workflow(
        JobWorkflow.run,
        spec.input(args),
        id=key,
        task_queue=settings.TEMPORAL_TASK_QUEUE,
        start_delay=delay,
    )


def job_activities() -> list:
    """One Temporal activity per registered job, for the worker."""
    return [_activity(spec) for spec in registered_jobs().values()]


def _activity(spec: JobSpec):
    @activity.defn(name=spec.name)
    async def run_job(args: list) -> None:
        await temporal.heartbeating(spec.fn, *args)

    return run_job
