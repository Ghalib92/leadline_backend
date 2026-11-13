"""The emitter and sink a runner writes through; both persist via services."""

from apps.ai.events import RunEvent

from . import services


class RunEventRecorder:
    """apps.ai.events.RunEventEmitter that stores each event on the run's timeline."""

    def __init__(self, run_id):
        self.run_id = run_id

    def emit(self, event: RunEvent) -> None:
        services.append_event(self.run_id, event)


class RunResultsSink:
    """apps.ai.graphs.discovery_runner.DiscoverySink backed by the run's records."""

    def __init__(self, run_id):
        self.run_id = run_id

    def candidate(self, candidate) -> str:
        return services.record_candidate(self.run_id, candidate) or "below_threshold"

    def progress(self, *, sources_scanned, pages_read, usage) -> None:
        services.record_progress(
            self.run_id, sources_scanned=sources_scanned, pages_read=pages_read, usage=usage
        )
