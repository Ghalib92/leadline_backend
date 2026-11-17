"""Rendering services for pages a plain fetch can't read: sites built in the browser
(JavaScript apps) and sites that refuse simple clients. They return the page's HTML
after it has run, as a browser would see it.

Jina Reader first (free with a key, also works without one at a low rate), Firecrawl
as backup (FIRECRAWL_API_KEY; credits). Callers never use them for a page whose
robots.txt keeps us out.
"""

from dataclasses import dataclass

import httpx
from django.conf import settings

from apps.ai.tools.http import UnsafeURL, check_public_url, get_http_client

JINA_ENDPOINT = "https://r.jina.ai/"
FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v2/scrape"


class RenderError(Exception):
    pass


@dataclass(frozen=True)
class RenderedPage:
    url: str  # after redirects, when the service says
    html: str
    status: int | None  # the site's own HTTP status, when the service says
    by: str  # "Jina Reader", "Firecrawl"


class JinaRenderer:
    name = "Jina Reader"

    def __init__(self, *, api_key: str = "", timeout: float = 60):
        self.api_key, self.timeout = api_key, timeout

    def render(self, url: str) -> RenderedPage:
        headers = {"Accept": "application/json", "X-Return-Format": "html"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = _call("GET", JINA_ENDPOINT + url, headers=headers, timeout=self.timeout)
        page = data.get("data") or {}
        if not page.get("html"):
            raise RenderError("Jina Reader returned no page.")
        return RenderedPage(
            url=page.get("finalUrl") or page.get("url") or url,
            html=page["html"],
            status=_int(page.get("httpStatus")),
            by=self.name,
        )


class FirecrawlRenderer:
    name = "Firecrawl"

    def __init__(self, *, api_key: str, timeout: float = 90):
        self.api_key, self.timeout = api_key, timeout

    def render(self, url: str) -> RenderedPage:
        data = _call(
            "POST",
            FIRECRAWL_ENDPOINT,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"url": url, "formats": ["rawHtml"], "onlyMainContent": False},
            timeout=self.timeout,
        )
        page = data.get("data") or {}
        if not data.get("success") or not page.get("rawHtml"):
            raise RenderError("Firecrawl returned no page.")
        metadata = page.get("metadata") or {}
        return RenderedPage(
            url=metadata.get("url") or metadata.get("sourceURL") or url,
            html=page["rawHtml"],
            status=_int(metadata.get("statusCode")),
            by=self.name,
        )


class RendererChain:
    """Tries each renderer in order; None when none could render the page."""

    def __init__(self, renderers: list):
        self.renderers = renderers

    def render(self, url: str) -> RenderedPage | None:
        try:
            check_public_url(url)
        except UnsafeURL:
            return None
        for renderer in self.renderers:
            try:
                return renderer.render(url)
            except RenderError:
                continue
        return None


def build_renderers(names: list[str]) -> RendererChain | None:
    """The configured renderers that can run (sources.yaml clients.lookup.renderers)."""
    renderers = []
    for name in names:
        if name == "jina":
            renderers.append(JinaRenderer(api_key=settings.JINA_API_KEY))
        elif name == "firecrawl" and settings.FIRECRAWL_API_KEY:
            renderers.append(FirecrawlRenderer(api_key=settings.FIRECRAWL_API_KEY))
    return RendererChain(renderers) if renderers else None


def _call(method: str, url: str, *, timeout: float, **kwargs) -> dict:
    try:
        with get_http_client(timeout=timeout) as client:
            response = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise RenderError(f"The renderer could not be reached ({type(exc).__name__}).") from exc
    if response.status_code >= 400:
        raise RenderError(f"The renderer returned HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError as exc:
        raise RenderError("The renderer's answer was not JSON.") from exc


def _int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
