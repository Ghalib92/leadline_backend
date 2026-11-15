"""Everything a client search needs besides its data: the request, settings, budget,
emitter and the agents' model calls (AgentRuntimeMixin)."""

import threading
from dataclasses import dataclass, field

from apps.ai.config.loader import ClientsSettings
from apps.ai.events import RunEventEmitter
from apps.ai.graphs.budget import Budget
from apps.ai.graphs.discovery_runner import DiscoveryRequest, DiscoverySink
from apps.ai.graphs.runtime import AgentRuntimeMixin, organization_brief


@dataclass
class ClientsRuntime(AgentRuntimeMixin):
    request: DiscoveryRequest
    emitter: RunEventEmitter
    sink: DiscoverySink
    settings: ClientsSettings
    budget: Budget
    # BusinessCategory code → the brief's label for it, for timeline messages.
    labels: dict = field(default_factory=dict)
    sources_scanned: int = 0
    warnings: list[str] = field(default_factory=list)
    prompt_versions: dict = field(default_factory=dict)
    models_used: dict = field(default_factory=dict)
    _models: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def organization_brief(self) -> str:
        request = self.request
        return organization_brief(request.organization_name, request.profile, request.dossier)
