"""The visitor's address, for throttles and logs.

Every page is rendered by the UI server, which calls the API for the visitor, so the
connection always comes from the UI server. It passes the visitor's address in
X-Leadline-Client-IP, with UI_PROXY_SECRET in X-Leadline-Proxy to show the header is
its own: the API is public, and anyone else could claim any address. Otherwise the
address is the connection's, as our edge proxy reports it (TRUSTED_CLIENT_IP_HEADER).

The result goes in REMOTE_ADDR, and X-Forwarded-For (which a client can write) is
dropped, so DRF's throttles key on it.
"""

from ipaddress import ip_address

from django.conf import settings
from django.utils.crypto import constant_time_compare


def _valid(address: str | None) -> str | None:
    try:
        return str(ip_address((address or "").strip()))
    except ValueError:
        return None


class ClientIPMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.META["REMOTE_ADDR"] = self.client_ip(request.META)
        request.META.pop("HTTP_X_FORWARDED_FOR", None)
        return self.get_response(request)

    @staticmethod
    def client_ip(meta) -> str:
        address = meta.get("REMOTE_ADDR", "")
        header = settings.TRUSTED_CLIENT_IP_HEADER
        if header:
            key = "HTTP_" + header.upper().replace("-", "_")
            address = _valid(meta.get(key)) or address
        secret = settings.UI_PROXY_SECRET
        if secret and constant_time_compare(meta.get("HTTP_X_LEADLINE_PROXY", ""), secret):
            address = _valid(meta.get("HTTP_X_LEADLINE_CLIENT_IP")) or address
        return address
