"""TED (Tenders Electronic Daily) - EU public procurement and tenders portal.

Public API v3 for European procurement notices:
https://api.ted.europa.eu/v3/notices/search
"""

from datetime import date, datetime

import httpx

from apps.ai.tools.fetch.base import FetchedPage
from apps.ai.tools.http import get_http_client
from apps.ai.tools.search.base import SearchError, SearchHit

from .base import html_to_text

API = "https://api.ted.europa.eu/v3/notices/search"
DETAIL_URL = "https://ted.europa.eu/en/notice/-/detail/{id}"


class TedEuropaSource:
    domain = "ted.europa.eu"

    def __init__(self, name: str = "ted_europa", *, options: dict | None = None):
        self.name = name
        self.options = options or {}

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        body = {
            "query": query[:200],
            "fields": [
                "ND",
                "TI",
                "PD",
                "DT",
                "CONTENT",
                "CA_NAME",
                "VALUE",
                "CURRENCY",
                "OJ_TYPE",
            ],
            "page": 1,
            "limit": max(1, min(max_results, 50)),
        }
        try:
            with get_http_client(timeout=30) as client:
                response = client.post(API, json=body)
        except httpx.HTTPError as exc:
            raise SearchError(
                f"ted.europa.eu could not be reached ({type(exc).__name__})."
            ) from exc
        if response.status_code >= 400:
            raise SearchError(f"ted.europa.eu returned HTTP {response.status_code}.")

        data = response.json()
        today = date.today().isoformat()
        notices = data.get("notices") or data.get("results") or []
        hits = []

        for notice in notices:
            notice_id = (
                notice.get("ND")
                or notice.get("publication-number")
                or notice.get("id")
                or notice.get("noticeId")
                or ""
            )
            raw_title = _text_value(notice.get("TI") or notice.get("title") or "")
            title = html_to_text(raw_title).strip()
            if not title:
                continue

            deadline = _extract_date(
                notice.get("DT") or notice.get("deadline") or notice.get("submissionDeadline") or ""
            )
            # Filter out closed tenders if past deadline is given
            if deadline and deadline < today:
                continue

            buyer = (
                _text_value(
                    notice.get("CA_NAME")
                    or notice.get("buyer")
                    or notice.get("contractingAuthority")
                    or ""
                )
                or "European Public Buyer"
            )

            url = DETAIL_URL.format(id=notice_id) if notice_id else (notice.get("url") or "")
            if not url:
                continue

            summary = html_to_text(
                _text_value(notice.get("CONTENT") or notice.get("summary") or "")
            ).strip()

            snippet = f"{buyer}. Closes {deadline or 'not stated'}."
            content = _listing_text(title, str(notice_id), buyer, deadline, summary, notice)

            hits.append(
                SearchHit(
                    title=title,
                    url=url,
                    snippet=snippet,
                    content=content,
                    extra={
                        "funder": buyer,
                        "notice_id": str(notice_id),
                        "deadline": deadline,
                        "source": "TED",
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


def _text_value(val) -> str:
    """Extract string value from potentially multilingual or nested dict/list responses."""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        for lang in ("ENG", "eng", "EN", "en"):
            if lang in val and isinstance(val[lang], str):
                return val[lang]
        for v in val.values():
            if isinstance(v, str):
                return v
    if isinstance(val, list) and val:
        return _text_value(val[0])
    return ""


def _listing_text(
    title: str,
    notice_id: str,
    buyer: str,
    deadline: str,
    summary: str,
    notice: dict,
) -> str:
    val = notice.get("VALUE") or ""
    curr = notice.get("CURRENCY") or ""
    val_str = f"{val} {curr}".strip() if val else ""
    lines = [
        f"Title: {title}",
        f"Publication number: {notice_id}" if notice_id else "",
        f"Contracting authority: {buyer}",
        f"Estimated value: {val_str}" if val_str else "",
        f"Submission deadline: {deadline or 'not stated'}",
    ]
    header = "\n".join(line for line in lines if line)
    return f"{header}\n\nDescription:\n{summary}".strip() if summary else header


def _extract_date(value: str) -> str:
    if not value or not isinstance(value, str):
        return ""
    value = value.strip()
    if len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value[:10], fmt).date().isoformat()
        except (ValueError, TypeError):
            continue
    return ""
