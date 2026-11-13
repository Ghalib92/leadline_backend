"""A local Temporal server for development, with its web UI. No Docker needed: the
Temporal SDK downloads its dev server binary on first use (needs internet once).

    python manage.py temporal_dev_server     # server on :7233, UI on http://localhost:8233

Workflow history is kept in .temporal/dev.db between restarts. Not for production: there
Temporal runs as its own service on Postgres.
"""

import asyncio
import signal

from django.conf import settings
from django.core.management.base import BaseCommand
from temporalio.testing import WorkflowEnvironment

DATA_DIR = settings.BASE_DIR / ".temporal"


class Command(BaseCommand):
    help = "Run a local Temporal dev server with its UI. Stop it with Ctrl+C."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=7233)
        parser.add_argument("--ui-port", type=int, default=8233)

    def handle(self, *args, port, ui_port, **options):
        DATA_DIR.mkdir(exist_ok=True)
        try:
            asyncio.run(self._serve(port, ui_port))
        except KeyboardInterrupt:
            pass

    async def _serve(self, port, ui_port):
        async with await WorkflowEnvironment.start_local(
            namespace=settings.TEMPORAL_NAMESPACE,
            port=port,
            ui=True,
            ui_port=ui_port,
            dev_server_database_filename=str(DATA_DIR / "dev.db"),
        ):
            self.stdout.write(
                self.style.SUCCESS(
                    f"Temporal on localhost:{port}, UI on http://localhost:{ui_port}. "
                    "Ctrl+C to stop."
                )
            )
            # Stop on Ctrl+C or SIGTERM alike, so the server binary is shut down with us.
            stop = asyncio.Event()
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
            await stop.wait()
