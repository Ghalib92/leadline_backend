"""The Temporal worker: runs every background job, discovery run and Schedule.

    python manage.py temporal_worker                 # also creates/updates the Schedules
    python manage.py temporal_worker --no-schedules  # extra workers: just do the work
    python manage.py temporal_worker --wait 60       # server still starting (make dev-temporal)
"""

import asyncio
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.common.temporal import connect
from config.temporal import build_worker, ensure_schedules


class Command(BaseCommand):
    help = "Run the Temporal worker (all background jobs). Stop it with Ctrl+C."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-schedules",
            action="store_true",
            help="Don't create or update the Schedules (searches, recovery, reminders).",
        )
        parser.add_argument(
            "--wait",
            type=int,
            default=0,
            metavar="SECONDS",
            help="Keep trying to reach the server this long before giving up.",
        )

    def handle(self, *args, no_schedules=False, wait=0, **options):
        try:
            asyncio.run(self._run(schedules=not no_schedules, wait=wait))
        except KeyboardInterrupt:
            pass

    async def _run(self, *, schedules, wait):
        client = await self._connect(wait)
        if schedules:
            names = await ensure_schedules(client)
            self.stdout.write(f"Schedules ready: {', '.join(names)}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Worker on {settings.TEMPORAL_ADDRESS}, namespace "
                f"{settings.TEMPORAL_NAMESPACE!r}, task queue {settings.TEMPORAL_TASK_QUEUE!r}."
            )
        )
        await build_worker(client).run()

    async def _connect(self, wait):
        deadline = time.monotonic() + wait
        while True:
            try:
                return await connect()
            except (OSError, RuntimeError, TimeoutError) as exc:
                if time.monotonic() >= deadline:
                    raise CommandError(
                        f"Cannot reach Temporal at {settings.TEMPORAL_ADDRESS}: {exc}. "
                        "Locally, start it with `make temporal-dev`."
                    ) from exc
            self.stdout.write("Waiting for the Temporal server…")
            await asyncio.sleep(2)
