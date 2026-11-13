"""Grants.gov (US federal grants), public API without a key.

search2 lists posted opportunities; fetchOpportunity returns the synopsis with the
description, award range and eligibility. https://www.grants.gov/api
"""

from datetime import datetime

import httpx

from apps.ai.tools.fetch.base import FetchedPage, FetchError
from apps.ai.tools.http import get_http_client
from apps.ai.tools.search.base import SearchError, SearchHit

from .base import html_to_text

API = "https://api.grants.gov/v1/api"
DETAIL_URL = "https://www.grants.gov/search-results-detail/{id}"


class GrantsGovSource:
    domain = "grants.gov"

    def __init__(self, name: str = "grants_gov", *, options: dict | None = None):
        self.name = name
        self.options = options or {}

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        body = {
            "keyword": query[:200],
            "oppStatuses": self.options.get("statuses", "posted|forecasted"),
            "rows": max(1, min(max_results, 25)),
        }
        data = _post(f"{API}/search2", body, SearchError)
        hits = []
        for item in (data.get("data") or {}).get("oppHits", []):
            title = html_to_text(item.get("title") or "").strip()
            if not title or not item.get("id"):
                continue
            close = item.get("closeDate") or ""
            hits.append(
                SearchHit(
                    title=title,
                    url=DETAIL_URL.format(id=item["id"]),
                    snippet=f"{item.get('agency', '')}. Closes {close or 'not stated'}.",
                    extra={
                        "grants_gov_id": str(item["id"]),
                        "funder": (item.get("agency") or "").strip(),
                        "deadline": _date(close),
                        "status": item.get("oppStatus", ""),
                    },
                )
            )
        return hits

    def read(self, hit: SearchHit) -> FetchedPage:
        opportunity_id = hit.extra.get("grants_gov_id")
        data = _post(f"{API}/fetchOpportunity", {"opportunityId": int(opportunity_id)}, FetchError)
        detail = data.get("data") or {}
        synopsis = detail.get("synopsis") or {}
        parts = [
            f"Title: {detail.get('opportunityTitle', hit.title)}",
            f"Opportunity number: {detail.get('opportunityNumber', '')}",
            f"Agency: {synopsis.get('agencyName', '')}",
            f"Posted: {synopsis.get('postingDate', '')}",
            f"Closing date: {synopsis.get('responseDate', '') or 'not stated'}",
            f"Award ceiling (USD): {synopsis.get('awardCeiling', '')}",
            f"Award floor (USD): {synopsis.get('awardFloor', '')}",
            "Eligible applicants: "
            + ", ".join(t.get("description", "") for t in synopsis.get("applicantTypes") or []),
            f"Eligibility notes: {html_to_text(synopsis.get('applicantEligibilityDesc', ''))}",
            "Description:",
            html_to_text(synopsis.get("synopsisDesc", "")),
        ]
        return FetchedPage(url=hit.url, title=hit.title, text="\n".join(parts))


def _post(url, body, error):
    try:
        with get_http_client(timeout=30) as client:
            response = client.post(url, json=body)
    except httpx.HTTPError as exc:
        raise error(f"grants.gov could not be reached ({type(exc).__name__}).") from exc
    if response.status_code >= 400:
        raise error(f"grants.gov returned HTTP {response.status_code}.")
    data = response.json()
    if data.get("errorcode") not in (0, None):
        raise error(f"grants.gov error: {data.get('msg', 'unknown')}")
    return data


def _date(value: str) -> str:
    """MM/DD/YYYY → ISO date, or empty."""
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except (TypeError, ValueError):
        return ""
