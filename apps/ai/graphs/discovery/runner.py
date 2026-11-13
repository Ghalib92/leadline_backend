"""The "graph" discovery runner: builds the run's tools and models, runs the graph."""

import logging

from apps.ai.config.loader import load_sources_file
from apps.ai.exceptions import AIConfigError
from apps.ai.graphs.budget import Budget
from apps.ai.graphs.discovery_runner import (
    DiscoveryOutcome,
    DiscoveryRequest,
    DiscoverySink,
    register_runner,
)
from apps.ai.tools.fetch.http import HttpFetcher
from apps.ai.tools.sources.registry import build_source
from apps.ai.tracing import run_config

from .builder import build_graph
from .nodes import DONE
from .runtime import DiscoveryRuntime

logger = logging.getLogger(__name__)
RECURSION_LIMIT = 60  # 8 nodes per round, max 5 rounds, with room to spare


def run_discovery_graph(request: DiscoveryRequest, emitter, sink: DiscoverySink):
    config = load_sources_file()
    runtime = DiscoveryRuntime(
        request=request,
        emitter=emitter,
        sink=sink,
        settings=config.discovery,
        budget=Budget(request.budget or config.discovery.budget.model_dump()),
        sources={},
        source_specs={},
        fetcher=HttpFetcher(),
    )
    for name in config.sources_for(request.source_categories or ["web"]):
        spec = config.sources[name]
        try:
            runtime.sources[name] = build_source(name, spec)
            runtime.source_specs[name] = spec
        except AIConfigError as exc:
            logger.warning("Discovery source %s is not configured: %s", name, exc)
            runtime.warn("profile", f"{spec.domain} is not set up on the server; skipping it.")
    if not runtime.sources:
        raise AIConfigError("None of the agent's sources are configured.")

    graph = build_graph(runtime)
    state = graph.invoke(
        {},
        config={
            **run_config(
                "discovery",
                metadata={"run_id": request.run_id, "organization_id": request.organization_id},
                tags=[f"agent:{request.agent_name}"],
            ),
            "recursion_limit": RECURSION_LIMIT,
        },
    )
    stop = state.get("stop", "")
    reasons = list(dict.fromkeys([*([stop] if stop and stop != DONE else []), *runtime.warnings]))
    return DiscoveryOutcome(
        status="partial" if reasons else "succeeded",
        warning=" ".join(reasons)[:500],
        prompt_versions=dict(runtime.prompt_versions),
        models_used=dict(runtime.models_used),
    )


register_runner("graph", run_discovery_graph)
