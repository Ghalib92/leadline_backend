"""Google PageSpeed Insights: a website's mobile performance score (0–100).

Needs PAGESPEED_API_KEY (free; about 25,000 checks a day). Without a key Google's
shared quota is used up, so the check is skipped. A check takes 10–40 seconds.
"""

import httpx
from django.conf import settings

from apps.ai.tools.http import get_http_client

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


class PageSpeedError(Exception):
    """The check failed for this site. `quota` means no more checks will work today."""

    def __init__(self, message: str, *, quota: bool = False):
        super().__init__(message)
        self.quota = quota


def pagespeed_enabled() -> bool:
    return bool(settings.PAGESPEED_API_KEY)


def mobile_speed(url: str, *, timeout: float) -> int:
    params = {
        "url": url,
        "strategy": "mobile",
        "category": "performance",
        "key": settings.PAGESPEED_API_KEY,
    }
    try:
        with get_http_client(timeout=timeout) as client:
            response = client.get(ENDPOINT, params=params)
    except httpx.TimeoutException as exc:
        raise PageSpeedError("PageSpeed took too long for this site.") from exc
    except httpx.HTTPError as exc:
        raise PageSpeedError(f"PageSpeed could not be reached ({type(exc).__name__}).") from exc
    if response.status_code == 429:
        raise PageSpeedError("PageSpeed's daily quota is used up.", quota=True)
    if response.status_code in (400, 403) and "key" in response.text.lower():
        raise PageSpeedError("PAGESPEED_API_KEY was rejected.", quota=True)
    if response.status_code >= 400:
        raise PageSpeedError(f"PageSpeed returned HTTP {response.status_code}.")
    try:
        score = response.json()["lighthouseResult"]["categories"]["performance"]["score"]
    except (ValueError, KeyError, TypeError) as exc:
        raise PageSpeedError("PageSpeed gave no performance score.") from exc
    if score is None:
        raise PageSpeedError("PageSpeed could not measure this site.")
    return round(float(score) * 100)
