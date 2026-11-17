"""Kenya's Public Procurement Information Portal (tenders.go.ke): every open tender of
national and county government entities, through the JSON API its own pages use
(public, no key; robots.txt allows it).

The API can't filter open tenders by keyword, and there are only a few hundred, so a
run fetches them all once (`max_pages` pages of 100) and matches each query in code.
The portal allows 60 requests a minute: pages are spaced out, a 429 is waited out
once (Retry-After), a failure isn't retried for every search of the run, and the
list is shared across runs for `cache_minutes` through the Django cache.

Personal data: each record embeds the official who posted it (name, ID number,
phone). None of it is read or stored; only the tender and its procuring entity's
official contacts are.
"""

import re
import threading
import time
from datetime import datetime

import httpx
from django.core.cache import cache
from django.utils import timezone

from apps.ai.tools.fetch.base import FetchedPage
from apps.ai.tools.http import get_http_client
from apps.ai.tools.search.base import SearchError, SearchHit

from .base import html_to_text

SITE = "https://tenders.go.ke"
API = f"{SITE}/api/active-tenders"
PER_PAGE = 100
CACHE_KEY = "ai:ppip:open-tenders"
MAX_RETRY_AFTER = 30  # seconds; a longer wait fails the fetch instead
# Words that say nothing about what a tender buys.
STOPWORDS = {
    "the", "and", "for", "of", "in", "to", "a", "an", "on", "at", "by", "with", "or",
    "tender", "tenders", "kenya", "kenyan", "supply", "provision", "procurement",
    "services", "service", "open", "rfp", "rfq", "bid", "bids",
}  # fmt: skip


class PPIPSource:
    domain = "tenders.go.ke"

    def __init__(self, name: str = "kenya_ppip", *, options: dict | None = None):
        self.name = name
        self.options = options or {}
        self._tenders: list[dict] | None = None  # fetched once per run
        self._error: SearchError | None = None  # a failed fetch isn't retried per search
        self._lock = threading.Lock()  # searches run in parallel; fetch only once

    def search(self, query: str, *, max_results: int) -> list[SearchHit]:
        words = _words(query)
        if not words:
            return []
        scored = []
        for tender in self._open_tenders():
            text = _searchable(tender)
            matched = sum(1 for word in words if re.search(rf"\b{re.escape(word)}", text))
            if matched:
                scored.append((matched, tender))
        # Most query words matched first, then the soonest to close.
        scored.sort(key=lambda item: (-item[0], item[1].get("close_at") or ""))
        return [_hit(tender) for _, tender in scored[:max_results]]

    def read(self, hit: SearchHit) -> FetchedPage:
        return FetchedPage(url=hit.url, title=hit.title, text=hit.content)

    # -- internals ---------------------------------------------------------------------

    def _open_tenders(self) -> list[dict]:
        with self._lock:
            if self._error is not None:
                raise self._error
            if self._tenders is None:
                cached = cache.get(CACHE_KEY)
                if cached is None:
                    try:
                        cached = self._fetch_all()
                    except SearchError as exc:
                        self._error = exc
                        raise
                    minutes = float(self.options.get("cache_minutes", 30))
                    cache.set(CACHE_KEY, cached, timeout=int(minutes * 60))
                # Still filtered here: a cached tender may have closed since.
                self._tenders = [t for t in cached if _is_open(t)]
            return self._tenders

    def _fetch_all(self) -> list[dict]:
        tenders, page, last = [], 1, 1
        max_pages = int(self.options.get("max_pages", 10))
        pause = float(self.options.get("page_pause_seconds", 1))
        with get_http_client(timeout=float(self.options.get("timeout_seconds", 60))) as client:
            while page <= min(last, max_pages):
                if page > 1:
                    time.sleep(pause)
                try:
                    response = _get_page(client, page)
                except httpx.HTTPError as exc:
                    if tenders:
                        break  # keep what was read
                    raise SearchError(
                        f"tenders.go.ke could not be reached ({type(exc).__name__})."
                    ) from exc
                if response.status_code >= 400:
                    if tenders:
                        break
                    raise SearchError(f"tenders.go.ke returned HTTP {response.status_code}.")
                data = response.json()
                tenders += data.get("data") or []
                last = int(data.get("last_page") or 1)
                page += 1
        return tenders


def _get_page(client, page: int):
    """One page; a 429 is waited out once when the portal says how long."""
    params = {"perpage": PER_PAGE, "page": page}
    headers = {"Accept": "application/json"}
    response = client.get(API, params=params, headers=headers)
    if response.status_code == 429:
        try:
            wait = float(response.headers.get("retry-after", ""))
        except ValueError:
            wait = MAX_RETRY_AFTER + 1
        if wait <= MAX_RETRY_AFTER:
            time.sleep(wait)
            response = client.get(API, params=params, headers=headers)
    return response


def _words(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", query.lower())
    return list(dict.fromkeys(w for w in words if len(w) > 2 and w not in STOPWORDS))


def _searchable(tender: dict) -> str:
    parts = [
        tender.get("title"),
        tender.get("description"),
        (tender.get("pe") or {}).get("name"),
        (tender.get("procurement_category") or {}).get("title"),
    ]
    return " ".join(str(part) for part in parts if part).lower()


def _closes(tender: dict) -> datetime | None:
    value = tender.get("close_at")
    if not value:
        return None
    try:
        naive = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None
    return timezone.make_aware(naive) if timezone.is_naive(naive) else naive  # Nairobi time


def _is_open(tender: dict) -> bool:
    closes = _closes(tender)
    return (
        bool(tender.get("published"))
        and not tender.get("terminated")
        and closes is not None
        and closes > timezone.now()
    )


def tender_details(tender: dict) -> dict:
    """What is stored on the opportunity (Opportunity.tender): the tender's facts and
    the procuring entity's official contacts. Never the official who posted it."""
    pe = tender.get("pe") or {}
    closes = _closes(tender)
    security = tender.get("bid_security_value")
    percent = tender.get("bid_security_percent")
    groups = [g.get("name", "") for g in tender.get("agpo_groups") or [] if g.get("name")]
    details = {
        "reference": tender.get("tender_ref") or "",
        "buyer": pe.get("name") or "",
        "category": (tender.get("procurement_category") or {}).get("title") or "",
        "method": (tender.get("procurement_method") or {}).get("title") or "",
        "published_at": (tender.get("published_at") or "")[:10],
        "closes_at": closes.isoformat() if closes else "",
        "venue": tender.get("venue") or "",
        "tender_fee": tender.get("tender_fee"),
        "bid_security": (
            f"{float(security):,.0f} KES"
            if security
            else (f"{percent}% of the bid" if percent else "")
        ),
        "submission": [
            m.get("title", "") for m in tender.get("submission_methods") or [] if m.get("title")
        ],
        "reserved_for": groups if tender.get("is_reservation") else [],
        "documents": [
            {
                "name": _document_name(doc.get("description") or ""),
                "url": SITE + doc["url"] if str(doc.get("url", "")).startswith("/") else "",
            }
            for doc in tender.get("documents") or []
            if doc.get("url")
        ][:10],
        "addenda": len(tender.get("addenda") or []),
        "buyer_contact": {
            key: value
            for key, value in {
                "email": pe.get("email") or "",
                "phone": pe.get("telephone") or "",
                "website": pe.get("org_url") or "",
                "address": ", ".join(
                    part for part in (pe.get("physical_address"), pe.get("city")) if part
                ),
            }.items()
            if value
        },
        "source": "tenders.go.ke",
    }
    return {key: value for key, value in details.items() if value not in ("", None, [], {})}


def _document_name(description: str) -> str:
    """'1794218923037-TENDER DOCUMENT' → 'Tender document'."""
    name = re.sub(r"^\d{8,}-", "", description).strip()
    return name.capitalize() if name.isupper() else name or "Document"


def _hit(tender: dict) -> SearchHit:
    details = tender_details(tender)
    title = " ".join(str(tender.get("title") or "").split())
    if title.isupper():
        title = title.capitalize()
    location = ", ".join(part for part in ((tender.get("pe") or {}).get("city"), "Kenya") if part)
    return SearchHit(
        title=title,
        url=f"{SITE}/tenders/{tender['id']}",
        snippet=(
            f"{details.get('category', 'Tender')} · {details.get('buyer', '')} · "
            f"closes {details.get('closes_at', '')[:10]}"
        ),
        content=_listing_text(tender, details, title),
        published=details.get("published_at", ""),
        extra={
            "funder": details.get("buyer", ""),
            "deadline": details.get("closes_at", "")[:10],
            "location": location,
            "tender": details,
        },
    )


def _listing_text(tender: dict, details: dict, title: str) -> str:
    """The listing as the verifier reads it: facts, then the description."""
    fields = [
        ("Title", title),
        ("Reference", details.get("reference")),
        ("Procuring entity", details.get("buyer")),
        ("Category", details.get("category")),
        ("Procurement method", details.get("method")),
        ("Published", details.get("published_at")),
        ("Closing date", details.get("closes_at")),
        ("Tender fee (KES)", details.get("tender_fee")),
        ("Bid security", details.get("bid_security")),
        ("Submission", ", ".join(details.get("submission", []))),
        ("Reserved for (AGPO)", ", ".join(details.get("reserved_for", []))),
        ("Location", (tender.get("pe") or {}).get("city")),
        ("Opportunity type", "tender"),
        ("Eligible bidders", "Firms registered in Kenya, per the tender document"),
    ]
    head = "\n".join(f"{label}: {value}" for label, value in fields if value not in ("", None))
    description = html_to_text(tender.get("description") or "")
    return f"{head}\n\n{description}".strip()
