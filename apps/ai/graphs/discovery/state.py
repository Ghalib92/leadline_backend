from dataclasses import dataclass, field
from typing import TypedDict

from apps.ai.schemas.discovery import Candidate, VerifiedOpportunity
from apps.ai.tools.search.base import SearchHit


@dataclass
class Lead:
    """A search result on its way through the graph."""

    source: str  # sources.yaml name
    domain: str
    hit: SearchHit
    reason: str = ""  # why the researcher picked it
    verified: VerifiedOpportunity | None = None
    extra: dict = field(default_factory=dict)


class DiscoveryState(TypedDict, total=False):
    round: int
    done_queries: list[str]  # "source: query", never repeated
    planned: list[tuple[str, str]]  # (source, query) for this round
    seen_urls: list[str]  # normalised URLs already handled in this run
    leads: list[Lead]  # this round's search results
    picked: list[Lead]  # what the researcher sent to the verifier
    verified: list[Lead]  # open, relevant listings
    scored: list[Candidate]  # judged, ready to save
    added: int  # added to the feed so far, all rounds
    sources_searched: list[str]
    # Why model work stopped (tokens or time ran out), "" while going, "done" at the end.
    stop: str
    # Searches or pages ran out: this round is finished, but no new round starts.
    exhausted: bool
