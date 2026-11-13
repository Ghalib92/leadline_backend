"""Handing discovery runs to Temporal, and the timed jobs that keep runs moving.

- A run is created `queued` and handed over only after its transaction commits
  (`dispatch_run`): a `DiscoveryRun` workflow (apps.discovery.temporal). A failed
  hand-over (Temporal unreachable) is recorded on the run, not lost.
- `recover_runs` runs every few minutes on its own (a Temporal Schedule), so a lost run
  never waits for someone's next action: it fails runs whose worker died, and hands
  undelivered runs over again (a few times, then fails them with a clear message).
- `start_due_searches` is the hourly scheduler tick (a Temporal Schedule). Each scheduled
  run has a slot (selectors.schedule_slot), unique in the database.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import selectors, services

logger = logging.getLogger(__name__)

# How long a new run may wait for its on-commit hand-over before the sweep steps in.
DISPATCH_GRACE = timedelta(minutes=1)
NOT_STARTED = "The search could not be started. Please try again."


def dispatch_run(run_id) -> bool:
    """Hand a queued run to Temporal. Safe to repeat: a run is claimed once, and Temporal
    refuses a second workflow for the same run. With JOBS_RUN_INLINE (tests) it runs here."""
    if settings.JOBS_RUN_INLINE:
        from .tasks import run_discovery

        services.record_dispatch(run_id, delivered=True)
        run_discovery(run_id)
        return True
    try:
        from .temporal.client import start_run_workflow

        start_run_workflow(run_id, wall_seconds=selectors.run_wall_seconds(run_id))
    except Exception:
        logger.exception("Could not hand discovery run %s to Temporal", run_id)
        services.record_dispatch(run_id, delivered=False)
        return False
    services.record_dispatch(run_id, delivered=True)
    return True


def recover_runs(now=None) -> dict:
    """The recovery sweep. Returns what it did, for the logs."""
    now = now or timezone.now()
    expired = services.expire_stale_runs()
    redispatched = failed = 0
    for run_id, attempts in selectors.undelivered_runs(created_before=now - DISPATCH_GRACE):
        if attempts >= settings.DISCOVERY_DISPATCH_ATTEMPTS:
            failed += services.abandon_run(run_id, error=NOT_STARTED)
        else:
            redispatched += dispatch_run(run_id)
    result = {"expired": expired, "redispatched": redispatched, "failed": failed}
    if any(result.values()):
        logger.warning("Discovery run recovery: %s", result)
    return result


def start_due_searches(now=None) -> int:
    """The scheduler tick: start the daily and weekly agents that are due. Returns how
    many runs started."""
    if not settings.SCHEDULED_RUNS_ENABLED:
        return 0
    now = now or timezone.now()
    started = 0
    for agent_id in selectors.agents_due(now, limit=settings.DISCOVERY_SCHEDULE_BATCH):
        try:
            started += services.start_scheduled_run(agent_id, now=now) is not None
        except Exception:  # one organization's problem never blocks the others
            logger.exception("Scheduled run for agent %s failed to start", agent_id)
    return started
