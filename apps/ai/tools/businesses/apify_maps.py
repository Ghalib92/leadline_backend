"""Google Maps through an Apify actor: businesses with phone, website, rating and reviews.

It scrapes Google Maps, which Google's terms do not allow, and spends the account's
Apify credit, so it runs only when APIFY_GOOGLE_MAPS_ENABLED is on. One search is one
synchronous actor run (https://docs.apify.com/api/v2#/reference/actors/run-actor-
synchronously-and-get-dataset-items).
"""

import httpx
from django.conf import settings

from apps.ai.tools.http import get_http_client

from .base import Business, BusinessQuery, SearchError, SourceOff

API = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"


class ApifyGoogleMapsSource:
    domain = "Google Maps"

    def __init__(self, name: str = "google_maps", *, options: dict | None = None):
        if not settings.APIFY_GOOGLE_MAPS_ENABLED:
            raise SourceOff("Google Maps through Apify is switched off.")
        if not settings.APIFY_API_TOKEN:
            raise SearchError("APIFY_API_TOKEN is not set.")
        self.name = name
        self.options = options or {}
        self.terms: dict[str, str] = self.options.get("search_terms", {})
        self.timeout = int(self.options.get("timeout_seconds", 240))

    def supports(self, query: BusinessQuery) -> bool:
        return bool(query.keyword or self.terms.get(query.category))

    def search(self, query: BusinessQuery, *, max_results: int) -> list[Business]:
        term = query.keyword or self.terms.get(query.category, "")
        body = {
            "searchStringsArray": [term],
            "locationQuery": ", ".join(p for p in (query.place, query.country) if p),
            "maxCrawledPlacesPerSearch": max_results,
            "language": self.options.get("language", "en"),
            "skipClosedPlaces": True,
        }
        url = API.format(actor=self.options.get("actor", "compass~crawler-google-places"))
        try:
            with get_http_client(timeout=self.timeout + 30) as client:
                response = client.post(
                    url,
                    params={"timeout": self.timeout},
                    headers={"Authorization": f"Bearer {settings.APIFY_API_TOKEN}"},
                    json=body,
                )
        except httpx.HTTPError as exc:
            raise SearchError(f"Apify could not be reached ({type(exc).__name__}).") from exc
        if response.status_code == 402:
            raise SearchError("The Apify account has no credit left this month.")
        if response.status_code >= 400:
            raise SearchError(f"Apify returned HTTP {response.status_code}.")
        items = response.json()
        if not isinstance(items, list):
            raise SearchError("Apify returned an unexpected answer.")
        return [
            business
            for item in items[:max_results]
            if (business := self._business(item, query)) is not None
        ]

    def _business(self, item: dict, query: BusinessQuery) -> Business | None:
        name = (item.get("title") or "").strip()
        if not name or item.get("permanentlyClosed") or item.get("temporarilyClosed"):
            return None
        socials = {
            kind: links[0]
            for kind in ("instagram", "facebook", "tiktok", "twitter")
            if (links := item.get(f"{kind}s")) and isinstance(links, list)
        }
        emails = item.get("emails") or []
        rating = item.get("totalScore")
        reviews = item.get("reviewsCount")
        return Business(
            name=name[:300],
            source=self.name,
            source_label="Google Maps",
            source_url=item.get("url") or "https://www.google.com/maps",
            category=item.get("categoryName") or "",
            address=item.get("address") or "",
            city=item.get("city") or query.place,
            country=(item.get("countryCode") or query.country or "").upper()[:2],
            phone=item.get("phone") or item.get("phoneUnformatted") or "",
            email=emails[0] if emails else "",
            website=item.get("website") or "",
            socials=socials,
            rating=float(rating) if rating is not None else None,
            reviews=int(reviews) if reviews is not None else None,
            complete_listing=True,
        ).normalised()
