"""Web search behind one interface, so the provider (Tavily, Exa…) can be swapped."""

from dataclasses import dataclass, field
from typing import Protocol


class SearchError(Exception):
    """The provider failed or refused the request."""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    # Full page text when the provider returns it, so the page need not be fetched.
    content: str = ""
    published: str = ""
    extra: dict = field(default_factory=dict)


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, *, max_results: int, options: dict) -> list[SearchHit]: ...
