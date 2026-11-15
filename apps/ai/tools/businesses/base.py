"""Business directories behind one interface, so providers can be swapped or added
(OpenStreetMap, Google Maps through Apify, later Google Places).

A source turns a BusinessQuery (a kind of business, or a keyword, in a place) into
Businesses (apps.ai.schemas.clients.Business): listed facts only.
"""

from dataclasses import dataclass
from typing import Protocol

from apps.ai.schemas.clients import Business
from apps.ai.tools.search.base import SearchError

__all__ = ["Business", "BusinessQuery", "BusinessSource", "SearchError", "SourceOff"]


class SourceOff(Exception):
    """The source is switched off on purpose (not an error to warn about)."""


@dataclass(frozen=True)
class BusinessQuery:
    category: str  # apps.organizations.choices.BusinessCategory value, or ""
    keyword: str  # the brief's own words ("boutique hotels"), or ""
    place: str  # a city or town
    country: str = ""  # ISO alpha-2

    def describe(self, category_label: str = "") -> str:
        what = self.keyword or category_label or self.category
        where = ", ".join(part for part in (self.place, self.country) if part)
        return f"{what} in {where}" if where else what


class BusinessSource(Protocol):
    name: str  # sources.yaml key
    domain: str  # shown in the timeline: "Searching OpenStreetMap for …"

    def supports(self, query: BusinessQuery) -> bool:
        """Whether this source can search for it (e.g. a category it has no mapping for)."""

    def search(self, query: BusinessQuery, *, max_results: int) -> list[Business]:
        """Raises SearchError when the provider fails."""
