"""World Bank procurement notices, public API without a key.

Contract awards are left out: only open notices (invitations for bids, requests for
expressions of interest, general and specific procurement notices) with a deadline
in the future. https://search.worldbank.org/api/v2/procnotices
"""

from datetime import date

import httpx

from apps.ai.tools.fetch.base import FetchedPage
from apps.ai.tools.http import get_http_client
from apps.ai.tools.search.base import SearchError, SearchHit

from .base import html_to_text

API = "https://search.worldbank.org/api/v2/procnotices"
DETAIL_URL = "https://projects.worldbank.org/en/projects-operations/procurement-detail/{id}"
OPEN_NOTICE_TYPES = (
    "Invitation for Bids",
    "Request for Expression of Interest",
    "Specific Procurement Notice",
    "General Procurement Notice",
)


class WorldBankSource:
    domain = "worldbank.org"

    def __init__(self, name: str = "world_bank", *, options: dict | None = None):
        self.name = name
        self.options = options or {}

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        params = {
            "format": "json",
            "qterm": query[:200],
            # Over-fetch: awards and closed notices are dropped below.
            "rows": max(1, min(max_results * 3, 50)),
            "srt": "submission_date",
            "order": "desc",
        }
        try:
            with get_http_client(timeout=30) as client:
                response = client.get(API, params=params)
        except httpx.HTTPError as exc:
            raise SearchError(
                f"worldbank.org could not be reached ({type(exc).__name__})."
            ) from exc
        if response.status_code >= 400:
            raise SearchError(f"worldbank.org returned HTTP {response.status_code}.")
        today = date.today().isoformat()
        excluded_methods = set(self.options.get("exclude_methods", []))
        hits = []
        for notice in response.json().get("procnotices", []):
            deadline = (notice.get("submission_deadline_date") or "")[:10]
            if notice.get("notice_type") not in OPEN_NOTICE_TYPES:
                continue
            if notice.get("notice_status") not in (None, "Published"):
                continue
            if not deadline or deadline < today:
                continue
            if notice.get("procurement_method_name") in excluded_methods:
                continue
            title = (notice.get("bid_description") or notice.get("project_name") or "").strip()
            if not title:
                continue
            country = notice.get("project_ctry_name", "")
            hits.append(
                SearchHit(
                    title=title,
                    url=DETAIL_URL.format(id=notice["id"]),
                    snippet=(
                        f"{notice.get('notice_type')} · {notice.get('project_name', '')} · "
                        f"{country} · deadline {deadline}"
                    ),
                    content=_listing_text(notice),
                    extra={
                        "funder": "World Bank",
                        "deadline": deadline,
                        "location": country,
                        "notice_type": notice.get("notice_type"),
                    },
                )
            )
            if len(hits) >= max_results:
                break
        return hits

    def read(self, hit: SearchHit) -> FetchedPage:
        return FetchedPage(url=hit.url, title=hit.title, text=hit.content)


def _listing_text(notice: dict) -> str:
    fields = [
        ("Notice type", notice.get("notice_type")),
        ("Project", notice.get("project_name")),
        ("Country", notice.get("project_ctry_name")),
        ("Procurement method", notice.get("procurement_method_name")),
        ("Submission deadline", notice.get("submission_deadline_date")),
        ("Contact organization", notice.get("contact_organization")),
    ]
    head = "\n".join(f"{label}: {value}" for label, value in fields if value)
    return f"{head}\n\n{html_to_text(notice.get('notice_text', ''))}".strip()
