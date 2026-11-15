"""OpenStreetMap: businesses by kind in a place, free and keyless.

Nominatim turns the place into a bounding box (cached; at most one request a second,
per its usage policy); Overpass returns the mapped businesses with their tags. Public
Overpass servers are often busy, so several are tried in order.

Data © OpenStreetMap contributors (ODbL). Phone numbers and websites are often not
mapped, so a listing here is never complete (Business.complete_listing is False).
"""

import re

import httpx
from django.core.cache import cache

from apps.ai.tools.http import get_http_client
from apps.ai.tools.ratelimit import MinuteRateLimiter, RateLimited

from .base import Business, BusinessQuery, SearchError

GEOCODE_CACHE_SECONDS = 7 * 24 * 3600
PHONE_TAGS = ("phone", "contact:phone", "contact:mobile", "mobile")
WEBSITE_TAGS = ("website", "contact:website", "url")
EMAIL_TAGS = ("email", "contact:email")
SOCIAL_TAGS = {
    "facebook": ("contact:facebook", "facebook"),
    "instagram": ("contact:instagram", "instagram"),
    "tiktok": ("contact:tiktok",),
    "whatsapp": ("contact:whatsapp",),
}
KIND_TAGS = (
    "tourism", "amenity", "shop", "office", "leisure", "healthcare", "craft", "man_made",
)  # fmt: skip
# Only letters, digits, spaces and a few marks go into an Overpass name filter.
UNSAFE_KEYWORD = re.compile(r"[^\w\s&'.-]", re.UNICODE)


class OverpassSource:
    domain = "OpenStreetMap"

    def __init__(self, name: str = "osm_places", *, options: dict | None = None):
        self.name = name
        self.options = options or {}
        self.tags: dict[str, list[str]] = self.options.get("tags", {})
        self.endpoints: list[str] = self.options.get("endpoints", [])
        self.geocoder: str = self.options.get("geocoder", "")
        self.timeout = float(self.options.get("timeout_seconds", 60))
        self.geocode_limiter = MinuteRateLimiter("nominatim", 50)

    def supports(self, query: BusinessQuery) -> bool:
        return bool(self.tags.get(query.category)) or bool(_keyword(query.keyword))

    def search(self, query: BusinessQuery, *, max_results: int) -> list[Business]:
        if not self.endpoints or not self.geocoder:
            raise SearchError("OpenStreetMap is not configured (endpoints and geocoder).")
        bbox = self._bbox(query.place, query.country)
        filters = self.tags.get(query.category) or [f'"name"~"{_keyword(query.keyword)}",i']
        statements = "".join(f'nwr[{tag}]["name"]({bbox});' for tag in filters)
        # Over-fetch, then keep the best-listed: those with a way to reach them first.
        limit = max(max_results * 3, 30)
        overpass_query = (
            f"[out:json][timeout:{int(self.timeout)}];({statements});out center tags {limit};"
        )
        elements = self._overpass(overpass_query)
        businesses = [
            business
            for element in elements
            if (business := self._business(element, query)) is not None
        ]
        businesses.sort(key=lambda b: (not (b.phone or b.website or b.email), b.name.lower()))
        return businesses[:max_results]

    # -- Nominatim -----------------------------------------------------------------

    def _bbox(self, place: str, country: str) -> str:
        key = f"osm:bbox:{country.lower()}:{place.strip().lower()}"
        cached = cache.get(key)
        if cached:
            return cached
        params = {"q": place, "format": "jsonv2", "limit": 1}
        if country:
            params["countrycodes"] = country.lower()
        try:
            self.geocode_limiter.acquire(max_wait=5)
            with get_http_client(timeout=30) as client:
                response = client.get(self.geocoder, params=params)
        except RateLimited as exc:
            raise SearchError("OpenStreetMap's place lookup is busy; try again shortly.") from exc
        except httpx.HTTPError as exc:
            raise SearchError(
                f"OpenStreetMap's place lookup could not be reached ({type(exc).__name__})."
            ) from exc
        if response.status_code >= 400:
            raise SearchError(f"OpenStreetMap's place lookup returned HTTP {response.status_code}.")
        results = response.json()
        if not results:
            raise SearchError(f"OpenStreetMap does not know the place {place!r}.")
        south, north, west, east = results[0]["boundingbox"]
        bbox = f"{south},{west},{north},{east}"
        cache.set(key, bbox, GEOCODE_CACHE_SECONDS)
        return bbox

    # -- Overpass ------------------------------------------------------------------

    def _overpass(self, query: str) -> list[dict]:
        problems = []
        for endpoint in self.endpoints:
            try:
                with get_http_client(timeout=self.timeout + 15) as client:
                    response = client.post(endpoint, data={"data": query})
            except httpx.HTTPError as exc:
                problems.append(type(exc).__name__)
                continue
            if response.status_code == 200 and "json" in response.headers.get("content-type", ""):
                return response.json().get("elements", [])
            problems.append(f"HTTP {response.status_code}")
        raise SearchError(
            f"OpenStreetMap's servers are busy right now ({', '.join(problems) or 'no server'})."
        )

    def _business(self, element: dict, query: BusinessQuery) -> Business | None:
        tags = element.get("tags") or {}
        name = (tags.get("name") or "").strip()
        if not name or tags.get("disused") or tags.get("abandoned"):
            return None
        kind = next((tags[tag] for tag in KIND_TAGS if tags.get(tag)), "")
        street = " ".join(
            part for part in (tags.get("addr:housenumber"), tags.get("addr:street")) if part
        )
        address = ", ".join(
            part for part in (street, tags.get("addr:suburb") or tags.get("addr:district")) if part
        )
        socials = {
            kind_name: value
            for kind_name, keys in SOCIAL_TAGS.items()
            if (value := _first(tags, keys))
        }
        return Business(
            name=name[:300],
            source=self.name,
            source_label="OpenStreetMap",
            source_url=f"https://www.openstreetmap.org/{element['type']}/{element['id']}",
            category=kind.replace("_", " ") if kind not in ("yes",) else "",
            address=address,
            city=tags.get("addr:city") or query.place,
            country=(tags.get("addr:country") or query.country or "").upper()[:2],
            phone=_first(tags, PHONE_TAGS).split(";")[0].strip(),
            email=_first(tags, EMAIL_TAGS),
            website=_first(tags, WEBSITE_TAGS),
            socials=socials,
            complete_listing=False,
        ).normalised()


def _first(tags: dict, keys) -> str:
    return next((tags[key].strip() for key in keys if tags.get(key)), "")


def _keyword(keyword: str) -> str:
    return UNSAFE_KEYWORD.sub("", keyword).strip()[:60]
