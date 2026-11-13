"""Starting a run's workflow from Django."""

from django.conf import settings
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from apps.common import temporal

from .workflows import DiscoveryRunWorkflow, RunInput


def run_workflow_id(run_id) -> str:
    return f"discovery-run-{run_id}"


def start_run_workflow(run_id, *, wall_seconds: int) -> None:
    temporal.run(start_run, run_id, wall_seconds=wall_seconds)


async def start_run(client: Client, run_id, *, wall_seconds: int) -> None:
    try:
        await client.start_workflow(
            DiscoveryRunWorkflow.run,
            RunInput(
                run_id=str(run_id),
                wall_seconds=wall_seconds,
                attempts=settings.DISCOVERY_RUN_ATTEMPTS,
            ),
            id=run_workflow_id(run_id),
            task_queue=settings.TEMPORAL_TASK_QUEUE,
            # One workflow per run, ever: a repeated hand-over is a no-op.
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        pass  # handed over before; the recovery sweep may resend
