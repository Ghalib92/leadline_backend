"""Outbound HTTP for agent tools: one client factory, and a guard for fetched URLs.

Agents open URLs they found on the web, so every fetch is checked first: only http(s),
only public addresses (no localhost, private ranges or cloud metadata endpoints), and
redirects are followed by hand so each hop is checked too.

Tests replace `transport` (see conftest.py), so CI never reaches the network.
"""

import ipaddress
import socket
from urllib.parse import urlsplit

import httpx
from django.conf import settings

# None means httpx's real network transport; tests swap in httpx.MockTransport.
transport: httpx.BaseTransport | None = None


class UnsafeURL(Exception):
    """The URL points somewhere agents must not go."""


def get_http_client(*, timeout: float | None = None) -> httpx.Client:
    return httpx.Client(
        transport=transport,
        timeout=timeout or settings.AI_HTTP_TIMEOUT_SECONDS,
        headers={"User-Agent": settings.AI_HTTP_USER_AGENT},
        follow_redirects=False,
    )


def resolve_host(host: str) -> list[str]:
    """IP addresses for a host name (patched in tests)."""
    return [info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]


def check_public_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise UnsafeURL(f"Only http(s) URLs can be opened: {url[:200]}")
    try:
        addresses = resolve_host(parts.hostname)
    except OSError as exc:
        raise UnsafeURL(f"Could not resolve {parts.hostname}.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeURL(f"{parts.hostname} is not a public address.")


def safe_get(client: httpx.Client, url: str, *, max_redirects: int = 3, **kwargs) -> httpx.Response:
    """GET with every hop checked by check_public_url. The response is not read yet
    when `stream=True` is passed; callers then close it."""
    stream = kwargs.pop("stream", False)
    for _ in range(max_redirects + 1):
        check_public_url(url)
        request = client.build_request("GET", url, **kwargs)
        response = client.send(request, stream=stream)
        if not response.is_redirect:
            return response
        location = response.headers.get("location", "")
        response.close()
        url = str(response.url.join(location))
    raise UnsafeURL(f"Too many redirects from {url[:200]}")
