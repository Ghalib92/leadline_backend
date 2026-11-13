from functools import partial

from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import ProposalState

STEPS = ("requirements", "outline", "evidence", "draft", "review")


def build_graph(runtime):
    graph = StateGraph(ProposalState)
    for name in STEPS:
        graph.add_node(name, partial(getattr(nodes, name), runtime))
    graph.add_edge(START, STEPS[0])
    for current, following in zip(STEPS, STEPS[1:], strict=False):
        graph.add_edge(current, following)
    graph.add_edge(STEPS[-1], END)
    return graph.compile()
