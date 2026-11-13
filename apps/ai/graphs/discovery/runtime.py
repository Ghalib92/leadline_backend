"""Everything a discovery run needs that is not graph state: the request, tools,
models, budget and the emitter. Built once per run and shared by the nodes."""

import json
import threading
from dataclasses import dataclass, field

from apps.ai.config.loader import DiscoverySettings
from apps.ai.events import RunEventEmitter
from apps.ai.graphs.budget import Budget
from apps.ai.graphs.discovery_runner import DiscoveryRequest, DiscoverySink
from apps.ai.graphs.runtime import AgentRuntimeMixin, organization_brief
from apps.ai.tools.fetch.base import PageFetcher


@dataclass
class DiscoveryRuntime(AgentRuntimeMixin):
    request: DiscoveryRequest
    emitter: RunEventEmitter
    sink: DiscoverySink
    settings: DiscoverySettings
    budget: Budget
    sources: dict  # name → OpportunitySource, enabled and configured for this run
    source_specs: dict  # name → SourceSpec
    fetcher: PageFetcher
    warnings: list[str] = field(default_factory=list)
    prompt_versions: dict = field(default_factory=dict)
    models_used: dict = field(default_factory=dict)
    _models: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def organization_brief(self) -> str:
        """The organization as the agents see it: profile facts plus the dossier."""
        request = self.request
        brief_json = organization_brief(request.organization_name, request.profile, request.dossier)
        if request.org_sources or request.folders:
            try:
                data = json.loads(brief_json)
                if request.org_sources:
                    data["custom_sources"] = [
                        {"url": s["url"], "label": s.get("label", ""), "notes": s.get("notes", "")}
                        for s in request.org_sources
                    ]
                if request.folders:
                    data["knowledge_folders"] = [
                        {"name": f["name"], "purpose": f.get("purpose", "")}
                        for f in request.folders
                        if f.get("name")
                    ]
                return json.dumps(data, ensure_ascii=False, indent=1)
            except Exception:
                pass
        return brief_json

    def organization_summary(self) -> str:
        """A one-paragraph version for cheap calls."""
        dossier = self.request.dossier or {}
        profile = self.request.profile
        parts = [
            dossier.get("summary") or profile.get("description") or self.request.organization_name,
            "Focus: " + ", ".join(dossier.get("focus_areas") or profile.get("sectors") or []),
            "Countries: "
            + ", ".join(profile.get("operating_countries") or [profile.get("country", "")]),
        ]
        return " ".join(part for part in parts if part.strip(" :"))
