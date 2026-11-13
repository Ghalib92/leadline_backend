"""Deduplication keys: the same listing found twice (or on another run) is one opportunity."""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that only track where a click came from.
_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|mc_cid|mc_eid|ref|source)$", re.IGNORECASE)
_NON_WORD = re.compile(r"\W+")


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)))
    path = parts.path.rstrip("/") or "/"
    # Scheme and fragment never identify a different listing.
    return urlunsplit(("", host, path, query, ""))


def normalize_text(text: str) -> str:
    return _NON_WORD.sub(" ", text.lower()).strip()


def url_key(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()


def title_key(title: str, funder: str) -> str:
    """The same call posted on several sites usually keeps its title and funder."""
    return hashlib.sha256(f"{normalize_text(title)}|{normalize_text(funder)}".encode()).hexdigest()
