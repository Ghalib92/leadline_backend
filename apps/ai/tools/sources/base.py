"""Opportunity sources: web search providers and structured APIs behind one interface.

A source turns a query into SearchHits. Structured APIs fill `content` (the listing
text) and `extra` (deadline, amount, funder…) themselves; for plain web results the
discovery graph fetches the page when the provider returned no text.

apps/ai/config/sources.yaml lists the sources, what each covers and which adapter
implements it; registry.build_source() turns an entry into an adapter.
"""

from typing import Protocol

from apps.ai.tools.fetch.base import FetchedPage
from apps.ai.tools.search.base import SearchHit


class OpportunitySource(Protocol):
    name: str  # the sources.yaml key, e.g. "grants_gov"
    domain: str  # shown in the timeline: "Searching grants.gov for…"

    def search(self, query: str, *, max_results: int) -> list[SearchHit]: ...

    def read(self, hit: SearchHit) -> FetchedPage | None:
        """The full listing, or None to let the graph fetch hit.url itself."""


def html_to_text(html: str) -> str:
    """Plain text from an HTML fragment (API listings often embed HTML)."""
    if not html:
        return ""
    from lxml import html as lxml_html

    try:
        tree = lxml_html.fromstring(html)
    except Exception:  # lxml raises on empty or malformed fragments
        return html
    for br in tree.iter("br"):
        br.tail = "\n" + (br.tail or "")
    for block in tree.iter("p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
        block.tail = "\n" + (block.tail or "")
    lines = (line.strip() for line in tree.text_content().splitlines())
    return "\n".join(line for line in lines if line)
