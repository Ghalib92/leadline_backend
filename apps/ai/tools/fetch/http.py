"""httpx + trafilatura: fetch a public HTML page and keep its main text.

Honors robots.txt for our user agent, refuses non-public addresses (tools.http),
caps the download size, and reads HTML and plain text only.
"""

import re
from urllib import robotparser
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from django.conf import settings

from apps.ai.tools.http import UnsafeURL, get_http_client, safe_get

from .base import FetchedPage, FetchError, PageLink

READABLE_TYPES = ("text/html", "application/xhtml+xml", "text/plain")


class Robots:
    """robots.txt rules per site for our user agent, read once per site."""

    def __init__(self):
        self._parsers: dict[str, robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._parsers:
            self._parsers[origin] = self._load(origin)
        parser = self._parsers[origin]
        return parser is None or parser.can_fetch(settings.AI_HTTP_USER_AGENT, url)

    def _load(self, origin):
        """None (allow everything) when robots.txt is missing or unreadable."""
        try:
            with get_http_client(timeout=10) as client:
                response = safe_get(client, f"{origin}/robots.txt")
        except (UnsafeURL, httpx.HTTPError):
            return None
        if response.status_code != 200:
            return None
        parser = robotparser.RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser


class HttpFetcher:
    def __init__(self, *, max_bytes: int | None = None):
        self.max_bytes = max_bytes or settings.AI_FETCH_MAX_BYTES
        self.robots = Robots()

    def fetch(self, url: str) -> FetchedPage:
        if not self.robots.allowed(url):
            raise FetchError("The site's robots.txt does not allow automated reading.")
        with get_http_client() as client:
            try:
                response = safe_get(client, url, stream=True)
            except UnsafeURL as exc:
                raise FetchError(str(exc)) from exc
            except httpx.HTTPError as exc:
                raise FetchError(f"The page could not be reached ({type(exc).__name__}).") from exc
            try:
                if response.status_code >= 400:
                    raise FetchError(f"The page returned HTTP {response.status_code}.")
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                if content_type and not content_type.startswith(READABLE_TYPES):
                    raise FetchError(f"Not a web page ({content_type}).")
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > self.max_bytes:
                        break  # the opening of a page is enough to judge a listing
                final_url = str(response.url)
                encoding = response.encoding or "utf-8"
            finally:
                response.close()
        html = body[: self.max_bytes].decode(encoding, errors="replace")
        if content_type == "text/plain":
            return FetchedPage(url=final_url, title="", text=html.strip())
        return _extract(final_url, html)


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
MAX_LINKS = 200


def _extract(url: str, html: str) -> FetchedPage:
    import trafilatura

    text = trafilatura.extract(
        html, url=url, include_comments=False, include_tables=True, favor_recall=True
    )
    metadata = trafilatura.extract_metadata(html)
    title = (metadata.title if metadata and metadata.title else "").strip()
    if not text:
        raise FetchError("No readable text on the page.")
    links, emails = _links_and_emails(url, html)
    return FetchedPage(url=url, title=title, text=text.strip(), links=links, emails=emails)


def _links_and_emails(url: str, html: str):
    from lxml import html as lxml_html

    try:
        tree = lxml_html.fromstring(html)
    except Exception:  # unparsable pages still have their main text
        return (), ()
    host = urlsplit(url).hostname or ""
    site = host.removeprefix("www.")
    links, seen, emails = [], set(), []
    for anchor in tree.iter("a"):
        href = (anchor.get("href") or "").strip()
        if href.lower().startswith("mailto:"):
            emails.append(unquote(href[7:].split("?")[0]))
            continue
        absolute = urljoin(url, href).split("#")[0]
        target = urlsplit(absolute)
        same_site = (target.hostname or "").removeprefix("www.") == site
        if target.scheme in ("http", "https") and same_site and absolute not in seen:
            seen.add(absolute)
            label = " ".join(anchor.text_content().split())[:100]
            links.append(PageLink(url=absolute, text=label))
    emails += _EMAIL.findall(tree.text_content())
    unique = tuple(dict.fromkeys(email.strip().lower().rstrip(".") for email in emails if email))
    return tuple(links[:MAX_LINKS]), unique
