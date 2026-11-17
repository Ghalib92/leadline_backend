"""Tavily web search (https://docs.tavily.com). One request per search, rate limited."""

from django.conf import settings

from apps.ai.exceptions import AIConfigError
from apps.ai.tools.http import get_http_client
from apps.ai.tools.ratelimit import MinuteRateLimiter, RateLimited

from .base import SearchError, SearchHit

ENDPOINT = "https://api.tavily.com/search"


class TavilySearch:
    name = "tavily"

    def __init__(self, *, api_key: str | None = None, limiter: MinuteRateLimiter | None = None):
        self.api_key = api_key if api_key is not None else settings.TAVILY_API_KEY
        if not self.api_key:
            raise AIConfigError("TAVILY_API_KEY is not set.")
        self.limiter = limiter or MinuteRateLimiter("tavily", settings.TAVILY_REQUESTS_PER_MINUTE)

    def search(self, query: str, *, max_results: int, options: dict) -> list[SearchHit]:
        body = {
            "query": query[:400],
            "max_results": max(1, min(max_results, 20)),
            "search_depth": "basic",  # 1 credit; "advanced" costs 2
            "topic": options.get("topic", "general"),
            # Page text comes back with the results, so most pages need no extra fetch.
            # Callers that only need titles and links pass include_raw_content=False.
            "include_raw_content": "text" if options.get("include_raw_content", True) else False,
        }
        if body["topic"] == "news" and options.get("days"):
            body["days"] = int(options["days"])
        for key in ("include_domains", "exclude_domains"):
            if options.get(key):
                body[key] = list(options[key])
        try:
            self.limiter.acquire(max_wait=options.get("max_wait", 60))
        except RateLimited as exc:
            raise SearchError(str(exc)) from exc
        with get_http_client(timeout=30) as client:
            try:
                response = client.post(
                    ENDPOINT, json=body, headers={"Authorization": f"Bearer {self.api_key}"}
                )
            except Exception as exc:
                raise SearchError(f"Tavily could not be reached: {type(exc).__name__}") from exc
        if response.status_code == 429:
            raise SearchError("Tavily rate limit reached.")
        if response.status_code in (401, 403):
            raise AIConfigError("TAVILY_API_KEY was rejected.")
        if response.status_code >= 400:
            raise SearchError(f"Tavily returned HTTP {response.status_code}.")
        return [
            SearchHit(
                title=(item.get("title") or "").strip(),
                url=item.get("url") or "",
                snippet=(item.get("content") or "").strip(),
                content=(item.get("raw_content") or "").strip(),
                published=item.get("published_date") or "",
                extra={"score": item.get("score")},
            )
            for item in response.json().get("results", [])
            if item.get("url")
        ]
