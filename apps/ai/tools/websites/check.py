"""Reading a business's website the way a visitor would, to see what it shows.

Honors robots.txt, refuses non-public addresses (tools.http) and reads only the
opening of a page. A failure says why in words a person can repeat to the business
("its domain no longer resolves"); a site that refuses automated visitors is
"blocked", which is no evidence of anything, and so is one that is too slow to answer.
"""

from dataclasses import dataclass, field
from datetime import date

import httpx
from django.conf import settings

from apps.ai.config.loader import LookupSettings
from apps.ai.schemas.clients import SiteCheck
from apps.ai.tools.fetch.http import Robots
from apps.ai.tools.http import UnresolvedHost, UnsafeURL, get_http_client, safe_get

from .page import PageFacts, read_page

HTML_TYPES = ("text/html", "application/xhtml+xml")
# Answers that mean "not for robots" rather than "broken".
REFUSALS = {401, 403, 406, 429}
MAX_PAGE_BYTES = 1_000_000


@dataclass
class SiteVisit:
    check: SiteCheck
    page: PageFacts = field(default_factory=PageFacts)


class SiteChecker:
    def __init__(self, lookup: LookupSettings, *, today: date | None = None):
        self.lookup = lookup
        self.today = today or date.today()
        self.robots = Robots()

    def visit(self, url: str) -> SiteVisit | None:
        """The home page checked; None when the address is not a public web address."""
        result = self._get(url)
        if result is None:
            return None
        if isinstance(result, SiteCheck):
            if result.certificate_error and url.startswith("https://"):
                # Visitors see a warning, but the site may be there over plain HTTP.
                fallback = self._get("http://" + url.removeprefix("https://"))
                if isinstance(fallback, tuple):
                    visit = self._read(*fallback)
                    visit.check.certificate_error = True
                    visit.check.problem = result.problem
                    return visit
            return SiteVisit(check=result)
        return self._read(*result)

    def contacts(self, url: str) -> PageFacts | None:
        """Another page of the site (its contact page), for contacts and features."""
        result = self._get(url)
        if not isinstance(result, tuple):
            return None
        final_url, html = result
        return self._facts(final_url, html)

    # -- internals ---------------------------------------------------------------------

    def _get(self, url: str):
        """(final_url, html), or a SiteCheck saying why it could not be read, or None."""
        try:
            if not self.robots.allowed(url):
                return SiteCheck(url=url, blocked=True, checked_at=self._date())
        except UnsafeURL:
            return None
        with get_http_client(timeout=settings.AI_HTTP_TIMEOUT_SECONDS) as client:
            try:
                response = safe_get(client, url, stream=True)
            except UnresolvedHost:
                return self._down(url, "its domain no longer resolves")
            except UnsafeURL:
                return None
            except httpx.TimeoutException:
                # A slow answer may be a busy server or one that stalls robots: no
                # evidence either way, so never called down.
                seconds = round(settings.AI_HTTP_TIMEOUT_SECONDS)
                return SiteCheck(
                    url=url,
                    blocked=True,
                    problem=f"it didn't load within {seconds} seconds",
                    checked_at=self._date(),
                )
            except httpx.ConnectError as exc:
                if _is_certificate_error(exc):
                    return SiteCheck(
                        url=url,
                        reachable=False,
                        https=False,
                        certificate_error=True,
                        problem="its security certificate is invalid",
                        checked_at=self._date(),
                    )
                return self._down(url, "its server refuses connections")
            except httpx.HTTPError as exc:
                return self._down(url, f"it could not be loaded ({type(exc).__name__})")
            try:
                final_url = str(response.url)
                if response.status_code in REFUSALS:
                    return SiteCheck(url=final_url, blocked=True, checked_at=self._date())
                status = response.status_code
                if status >= 400:
                    return self._down(final_url, f"it returns an error (HTTP {status})")
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                if content_type and not content_type.startswith(HTML_TYPES):
                    return SiteCheck(url=final_url, blocked=True, checked_at=self._date())
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > MAX_PAGE_BYTES:
                        break
                encoding = response.encoding or "utf-8"
            except httpx.HTTPError as exc:
                return self._down(url, f"it stopped loading ({type(exc).__name__})")
            finally:
                response.close()
        return final_url, body[:MAX_PAGE_BYTES].decode(encoding, errors="replace")

    def _read(self, final_url: str, html: str) -> SiteVisit:
        facts = self._facts(final_url, html)
        if facts.placeholder:
            check = SiteCheck(
                url=final_url,
                reachable=False,
                problem=f'it shows a placeholder page ("{facts.placeholder}")',
                checked_at=self._date(),
            )
            return SiteVisit(check=check, page=facts)
        year = facts.copyright_year
        check = SiteCheck(
            url=final_url,
            https=final_url.startswith("https://"),
            mobile_ready=facts.viewport,
            copyright_year=year,
            stale=bool(year and self.today.year - year >= self.lookup.stale_after_years),
            checked_at=self._date(),
            **facts.features,
        )
        return SiteVisit(check=check, page=facts)

    def _facts(self, final_url: str, html: str) -> PageFacts:
        return read_page(
            final_url,
            html,
            features=self.lookup.features.model_dump(),
            contact_words=self.lookup.contact_page_words,
            placeholder_words=self.lookup.placeholder_words,
        )

    def _down(self, url: str, problem: str) -> SiteCheck:
        return SiteCheck(url=url, reachable=False, problem=problem, checked_at=self._date())

    def _date(self) -> str:
        return self.today.isoformat()


def _is_certificate_error(exc: Exception) -> bool:
    text = f"{exc} {exc.__cause__ or ''}".upper()
    return "CERTIFICATE" in text or "SSL" in text
