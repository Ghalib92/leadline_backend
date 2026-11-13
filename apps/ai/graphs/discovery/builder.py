from functools import partial

from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import DiscoveryState

STEPS = ("load_context", "plan", "search", "screen", "read", "judge", "save", "review")


def build_graph(runtime):
    graph = StateGraph(DiscoveryState)
    for name in STEPS:
        graph.add_node(name, partial(getattr(nodes, name), runtime))
    graph.add_edge(START, "load_context")
    for current, following in zip(STEPS, STEPS[1:], strict=False):
        graph.add_edge(current, following)
    graph.add_conditional_edges("review", nodes.next_after_review, {"plan": "plan", "end": END})
    return graph.compile()
