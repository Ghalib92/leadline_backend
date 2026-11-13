from apps.ai.events import RunEvent

from . import services


class ProposalEventRecorder:
    """apps.ai.events.RunEventEmitter that stores events on the proposal's timeline."""

    def __init__(self, proposal_id, started_at):
        self.proposal_id = proposal_id
        self.started_at = started_at

    def emit(self, event: RunEvent) -> None:
        services.append_event(self.proposal_id, self.started_at, event)
