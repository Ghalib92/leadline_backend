"""TED (Tenders Electronic Daily): the EU's public procurement notices.

Search API v3 (public, no key): https://api.ted.europa.eu/v3/notices/search. It takes
an "expert query", not plain text: a plain "software development" is a syntax error
(HTTP 400). Queries are built here from the search's words: full text (`FT~`) with
all words, then without the last word when that finds nothing (never fewer than two:
one word, or any word, matches thousands of unrelated notices), open notices only,
newest first.

Options (sources.yaml): `places`, ISO 3166 alpha-3 codes for where the work is done
(e.g. [KEN, UGA]); EU-funded work abroad is listed with the place it is done.
"""

import re

import httpx

from apps.ai.tools.fetch.base import FetchedPage
from apps.ai.tools.http import get_http_client
from apps.ai.tools.search.base import SearchError, SearchHit

from .base import html_to_text

API = "https://api.ted.europa.eu/v3/notices/search"
DETAIL_URL = "https://ted.europa.eu/en/notice/-/detail/{id}"
FIELDS = [
    "publication-number",
    "publication-date",
    "notice-title",
    "buyer-name",
    "buyer-country",
    "deadline-receipt-tender-date-lot",
    "description-lot",
    "estimated-value-lot",
    "estimated-value-cur-lot",
    "place-of-performance",
]
MAX_WORDS = 5
_WORD = re.compile(r"[a-z0-9]{3,}")
_PLACE = re.compile(r"^[A-Z]{3}$")
STOPWORDS = {
    "the", "and", "for", "with", "from", "tender", "tenders", "procurement", "contract",
    "contracts", "notice", "notices", "open", "call", "calls", "services", "service",
    "supply", "provision", "europe", "european", "ted",
}  # fmt: skip


MAX_TRIES = 3


def expert_query(words: list[str], *, places=()) -> str:
    joined = " AND ".join(f"FT~{word}" for word in words)
    query = f"({joined}) AND deadline-receipt-tender-date-lot>=today()"
    if places:
        query += f" AND place-of-performance IN ({' '.join(places)})"
    return f"{query} SORT BY publication-date DESC"


def query_words(query: str) -> list[str]:
    words = [w for w in _WORD.findall(query.lower()) if w not in STOPWORDS]
    return list(dict.fromkeys(words))[:MAX_WORDS]


class TedEuropaSource:
    domain = "ted.europa.eu"

    def __init__(self, name: str = "ted_europa", *, options: dict | None = None):
        self.name = name
        self.options = options or {}

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        words = query_words(query)
        if not words:
            return []
        places = [p for p in self.options.get("places") or [] if _PLACE.match(str(p))]
        limit = max(1, min(max_results, 50))
        notices = []
        with get_http_client(timeout=float(self.options.get("timeout_seconds", 30))) as client:
            for _ in range(MAX_TRIES):
                notices = self._search(client, expert_query(words, places=places), limit)
                if notices or len(words) <= 2:
                    break
                words = words[:-1]
        hits = [hit for hit in (_hit(notice) for notice in notices) if hit]
        return hits[:max_results]

    def _search(self, client, query: str, limit: int) -> list[dict]:
        body = {"query": query, "fields": FIELDS, "page": 1, "limit": limit}
        try:
            response = client.post(API, json=body)
        except httpx.HTTPError as exc:
            raise SearchError(
                f"ted.europa.eu could not be reached ({type(exc).__name__})."
            ) from exc
        if response.status_code >= 400:
            raise SearchError(f"ted.europa.eu returned HTTP {response.status_code}.")
        return response.json().get("notices") or []

    def read(self, hit: SearchHit) -> FetchedPage | None:
        if hit.content:
            return FetchedPage(url=hit.url, title=hit.title, text=hit.content)
        return None


def _hit(notice: dict) -> SearchHit | None:
    number = str(notice.get("publication-number") or "")
    title = html_to_text(_text(notice.get("notice-title"))).strip()
    if not number or not title:
        return None
    buyer = _text(notice.get("buyer-name")) or "European public buyer"
    deadline = _first(notice.get("deadline-receipt-tender-date-lot"))[:10]
    summary = html_to_text(_text(notice.get("description-lot"))).strip()
    value = _first(notice.get("estimated-value-lot"))
    currency = _first(notice.get("estimated-value-cur-lot"))
    places = [p for p in dict.fromkeys(notice.get("place-of-performance") or []) if len(p) == 3]
    lines = [
        f"Title: {title}",
        f"Publication number: {number}",
        f"Contracting authority: {buyer}",
        f"Buyer country: {', '.join(notice.get('buyer-country') or [])}",
        f"Place of performance: {', '.join(places)}" if places else "",
        f"Estimated value: {value} {currency}".strip() if value else "",
        f"Submission deadline: {deadline or 'not stated'}",
        f"Published: {str(notice.get('publication-date') or '')[:10]}",
        "Opportunity type: tender",
    ]
    header = "\n".join(line for line in lines if line and not line.endswith(": "))
    return SearchHit(
        title=title,
        url=DETAIL_URL.format(id=number),
        snippet=f"{buyer}. Closes {deadline or 'not stated'}.",
        content=f"{header}\n\nDescription:\n{summary}".strip() if summary else header,
        published=str(notice.get("publication-date") or "")[:10],
        extra={"funder": buyer, "notice_id": number, "deadline": deadline, "source": "TED"},
    )


def _first(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _text(value) -> str:
    """English when the notice has it, else its first language; lists are joined."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(str(v) for v in value if v)
    if isinstance(value, dict):
        for lang in ("eng", "ENG", "en", "EN"):
            if value.get(lang):
                return _text(value[lang])
        for item in value.values():
            if item:
                return _text(item)
    return ""
