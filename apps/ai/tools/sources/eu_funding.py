"""European Commission Funding & Tenders portal adapter.

Public corporate search API (SEDIA) for grants, calls for proposals, and tenders:
https://api.tech.ec.europa.eu/search-api/prod/rest/search
"""

from datetime import date, datetime

import httpx

from apps.ai.tools.fetch.base import FetchedPage
from apps.ai.tools.http import get_http_client
from apps.ai.tools.search.base import SearchError, SearchHit

from .base import html_to_text

API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
DETAIL_URL = "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/topic-details/{id}"


class EuFundingSource:
    domain = "ec.europa.eu"

    def __init__(self, name: str = "eu_funding", *, options: dict | None = None):
        self.name = name
        self.options = options or {}

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        params = {
            "apiKey": self.options.get("api_key", "SEDIA"),
            "text": query[:200],
            "pageSize": max(1, min(max_results, 50)),
            "pageNumber": 1,
        }
        body = {
            "query": {
                "bool": {
                    "must": [
                        {"terms": {"type": self.options.get("types", ["1", "2"])}},
                    ]
                }
            }
        }
        try:
            with get_http_client(timeout=30) as client:
                response = client.post(API, params=params, json=body)
                if response.status_code == 405:  # fallback if endpoint expects GET
                    response = client.get(API, params=params)
        except httpx.HTTPError as exc:
            raise SearchError(f"ec.europa.eu could not be reached ({type(exc).__name__}).") from exc
        if response.status_code >= 400:
            raise SearchError(f"ec.europa.eu returned HTTP {response.status_code}.")

        data = response.json()
        today = date.today().isoformat()
        items = data.get("results") or data.get("items") or []
        hits = []

        for item in items:
            identifier = (
                item.get("identifier")
                or item.get("id")
                or item.get("topicId")
                or item.get("callIdentifier")
                or ""
            )
            title = html_to_text(item.get("title") or item.get("content") or "").strip()
            if not title:
                continue

            deadline = _extract_date(
                item.get("deadlineDate")
                or item.get("callDeadlineDate")
                or item.get("deadline")
                or ""
            )
            # Filter out closed calls if a past deadline is stated
            if deadline and deadline < today:
                continue

            programme = (
                item.get("frameworkProgramme")
                or item.get("programme")
                or item.get("programmePeriod")
                or "European Commission"
            )
            if isinstance(programme, dict):
                programme = programme.get("description") or programme.get("abbreviation") or "EU"
            if isinstance(programme, list) and programme:
                programme = str(programme[0])

            url = item.get("url")
            if not url and identifier:
                url = DETAIL_URL.format(id=identifier)
            if not url:
                continue

            summary = html_to_text(
                item.get("summary") or item.get("description") or item.get("snippet") or ""
            ).strip()

            snippet = f"{programme}. Closes {deadline or 'not stated'}."
            content = _listing_text(title, identifier, str(programme), deadline, summary)

            hits.append(
                SearchHit(
                    title=title,
                    url=url,
                    snippet=snippet,
                    content=content,
                    extra={
                        "funder": "European Commission",
                        "call_id": str(identifier),
                        "deadline": deadline,
                        "programme": str(programme),
                        "status": item.get("status", ""),
                    },
                )
            )
            if len(hits) >= max_results:
                break

        return hits

    def read(self, hit: SearchHit) -> FetchedPage | None:
        if hit.content:
            return FetchedPage(url=hit.url, title=hit.title, text=hit.content)
        return None


def _listing_text(title: str, call_id: str, programme: str, deadline: str, summary: str) -> str:
    lines = [
        f"Title: {title}",
        f"Call identifier: {call_id}" if call_id else "",
        f"Programme: {programme}" if programme else "",
        "Funder: European Commission",
        f"Closing date: {deadline or 'not stated'}",
    ]
    header = "\n".join(line for line in lines if line)
    return f"{header}\n\nDescription:\n{summary}".strip() if summary else header


def _extract_date(value: str) -> str:
    if not value or not isinstance(value, str):
        return ""
    value = value.strip()
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(value[:10], fmt).date().isoformat()
        except (ValueError, TypeError):
            continue
    return ""
