"""The contract between a discovery run and whatever executes it.

apps.discovery builds a DiscoveryRequest, passes an emitter (timeline events) and a
sink (candidates and progress), and records the Outcome. Runners never touch the
ORM: everything they produce goes through the emitter and the sink, which write
through apps.discovery.services.

Runners register by name; DISCOVERY_RUNNER picks one. "fake" lives in
fake_discovery.py; the LangGraph discovery graph registers itself as "graph".
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from apps.ai.events import RunEventEmitter
from apps.ai.exceptions import AIConfigError
from apps.ai.schemas.discovery import Candidate


@dataclass(frozen=True)
class DiscoveryRequest:
    run_id: str
    organization_id: str
    organization_name: str
    agent_name: str
    opportunity_types: list[str]
    keywords: list[str]
    source_categories: list[str]
    budget: dict  # max_searches, max_pages, max_tokens, max_wall_seconds
    match_threshold: int
    instructions: str = ""
    depth: str = ""  # quick | standard | deep (sources.yaml); "" means the default
    # What the run looks for (apps.organizations.choices.Goal); "clients" runs the
    # business search (apps.ai.graphs.clients), anything else the opportunity search.
    goal: str = ""
    profile: dict = field(default_factory=dict)  # profile and preference facts
    dossier: dict | None = None  # apps.ai.schemas.dossier.Dossier, when generated
    # Normalised URLs (apps.opportunities.dedupe.normalize_url) already in the feed,
    # so known listings are not read and scored again.
    known_urls: frozenset[str] = frozenset()
    # Custom organization sources (Settings > Sources) for source_reader.
    org_sources: list[dict] = field(default_factory=list)
    # Selected knowledge folders (Settings > Run settings or run overrides).
    folders: list[dict] = field(default_factory=list)
    # Which company documents the AI may read (KnowledgeScope.to_config()); None = all.
    knowledge: dict | None = None
    # Client searches: {customer_types, places, country} (selectors.client_search_plan).
    client_search: dict = field(default_factory=dict)


class DiscoverySink(Protocol):
    def candidate(self, candidate: Candidate) -> str:
        """Dedupe and save; returns the decision (added, duplicate, below_threshold)."""

    def progress(self, *, sources_scanned: int, pages_read: int, usage: dict) -> None: ...


@dataclass(frozen=True)
class DiscoveryOutcome:
    status: Literal["succeeded", "partial"]
    # Why a partial run stopped early (budget reached, a source failed…).
    warning: str = ""
    prompt_versions: dict = field(default_factory=dict)
    models_used: dict = field(default_factory=dict)


DiscoveryRunner = Callable[[DiscoveryRequest, RunEventEmitter, DiscoverySink], DiscoveryOutcome]

_RUNNERS: dict[str, DiscoveryRunner] = {}


def register_runner(name: str, runner: DiscoveryRunner) -> None:
    _RUNNERS[name] = runner


def registered_runners() -> frozenset[str]:
    _load_builtin_runners()
    return frozenset(_RUNNERS)


def get_runner(name: str) -> DiscoveryRunner:
    _load_builtin_runners()
    runner = _RUNNERS.get(name)
    if runner is None:
        known = ", ".join(sorted(_RUNNERS))
        raise AIConfigError(f"Unknown DISCOVERY_RUNNER {name!r}. Known runners: {known}.")
    return runner


def _load_builtin_runners():
    from apps.ai.graphs import fake_discovery  # noqa: F401 — registers "fake"
    from apps.ai.graphs.discovery import runner  # noqa: F401 — registers "graph"
