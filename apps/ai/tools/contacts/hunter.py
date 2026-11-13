"""Hunter.io domain search: published business emails for a domain, with verification.

Optional (HUNTER_API_KEY). Follow Hunter's terms; the people found are business
contacts and are stored with where they came from so they can be removed on request.
"""

import httpx
from django.conf import settings

from apps.ai.tools.http import get_http_client

ENDPOINT = "https://api.hunter.io/v2/domain-search"


class HunterError(Exception):
    pass


def hunter_enabled() -> bool:
    return bool(settings.HUNTER_API_KEY)


def domain_search(domain: str, *, limit: int = 10) -> list[dict]:
    params = {
        "domain": domain,
        "limit": limit,
        "type": "personal",
        "api_key": settings.HUNTER_API_KEY,
    }
    try:
        with get_http_client(timeout=20) as client:
            response = client.get(ENDPOINT, params=params)
    except httpx.HTTPError as exc:
        raise HunterError(f"Hunter could not be reached ({type(exc).__name__}).") from exc
    if response.status_code >= 400:
        raise HunterError(f"Hunter returned HTTP {response.status_code}.")
    data = response.json().get("data") or {}
    return [
        {
            "name": " ".join(p for p in (item.get("first_name"), item.get("last_name")) if p),
            "role": item.get("position") or item.get("department") or "",
            "email": item["value"],
            "phone": item.get("phone_number") or "",
            "confidence": int(item.get("confidence") or 0),
            "verified": (item.get("verification") or {}).get("status") == "valid",
        }
        for item in data.get("emails", [])
        if item.get("value")
    ]
