"""Fetching pages behind one interface (httpx today; Firecrawl or Playwright later)."""

from dataclasses import dataclass
from typing import Protocol


class FetchError(Exception):
    """The page could not be read. The message is safe to show in the run timeline."""


@dataclass(frozen=True)
class PageLink:
    url: str
    text: str


@dataclass(frozen=True)
class FetchedPage:
    url: str  # after redirects
    title: str
    text: str  # the main text (boilerplate such as menus and footers removed)
    # From the whole page, including footers: links within the same site, and email
    # addresses (mailto links and addresses written in the text).
    links: tuple[PageLink, ...] = ()
    emails: tuple[str, ...] = ()


class PageFetcher(Protocol):
    def fetch(self, url: str) -> FetchedPage: ...
