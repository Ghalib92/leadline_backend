import threading
from dataclasses import dataclass, field

from apps.ai.config.loader import ProposalSettings
from apps.ai.events import RunEventEmitter
from apps.ai.graphs.budget import Budget
from apps.ai.graphs.proposal_runner import ProposalRequest
from apps.ai.graphs.runtime import AgentRuntimeMixin, organization_brief
from apps.ai.tools.fetch.base import PageFetcher


@dataclass
class ProposalRuntime(AgentRuntimeMixin):
    request: ProposalRequest
    emitter: RunEventEmitter
    settings: ProposalSettings
    budget: Budget
    fetcher: PageFetcher
    warnings: list[str] = field(default_factory=list)
    prompt_versions: dict = field(default_factory=dict)
    models_used: dict = field(default_factory=dict)
    _models: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def organization_brief(self) -> str:
        request = self.request
        return organization_brief(request.organization_name, request.profile, request.dossier)
