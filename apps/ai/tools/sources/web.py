from apps.ai.tools.search.base import SearchHit, SearchProvider


class WebSearchSource:
    """A web search provider used as a source; `options` tune it (news topic, domains)."""

    def __init__(self, name: str, provider: SearchProvider, *, domain: str, options: dict):
        self.name = name
        self.provider = provider
        self.domain = domain
        self.options = options

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        return self.provider.search(query, max_results=max_results, options=self.options)

    def read(self, hit: SearchHit):
        return None  # the graph uses hit.content, or fetches the page
