"""Which web search results are a business's own website and social profiles.

Plain rules, no model: a result is the business's site when its domain carries a
distinctive word of the business's name (not "hotel" or "Nairobi") and it is not a
directory, booking or review site. When unsure, nothing is picked: a lead with no
website found says so, which is better than pointing at someone else's site.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from apps.ai.schemas.clients import social_kind
from apps.ai.tools.search.base import SearchHit

from .page import PROFILE_KINDS, profile_url

MIN_WORD = 4  # shorter name words ("abc", "the") match too many domains


@dataclass
class WebPresence:
    website: str = ""
    socials: dict[str, str] = field(default_factory=dict)


def name_words(name: str, generic: set[str]) -> list[str]:
    """The distinctive words of a business name, lowercase and without accents."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", plain)
    return [word for word in words if len(word) >= MIN_WORD and word not in generic]


def site_label(host: str) -> str:
    """The name part of a host: savannahlodge.co.ke → savannahlodge."""
    parts = host.lower().removeprefix("www.").split(".")
    if len(parts) >= 3 and len(parts[-2]) <= 3:  # co.ke, or.ke, com.ng, co.uk
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else parts[0]


def find_presence(
    hits: list[SearchHit], name: str, *, not_own_site: list[str], generic: list[str]
) -> WebPresence:
    words = name_words(name, {word.lower() for word in generic})
    compact = "".join(name_words(name, set()))
    presence = WebPresence()
    if not words:
        return presence  # a name of common words only: no safe way to tell
    for hit in hits:
        url = hit.url
        host = (urlsplit(url).hostname or "").lower()
        if not host:
            continue
        kind = social_kind(url)
        if kind:
            if kind in PROFILE_KINDS and kind not in presence.socials:
                profile = profile_url(url)
                handle = profile.rsplit("/", 1)[-1].lower().lstrip("@")
                if profile and _mentions(handle, words):
                    presence.socials[kind] = profile
            continue
        if presence.website or any(listed(host, pattern) for pattern in not_own_site):
            continue
        label = site_label(host).replace("-", "")
        if _mentions(label, words) or (len(label) >= MIN_WORD + 2 and label in compact):
            parts = urlsplit(url)
            presence.website = f"{parts.scheme}://{parts.netloc}/"
    return presence


def listed(host: str, pattern: str) -> bool:
    """Whether `host` is one of not_own_site's sites, on domain boundaries:
    "booking.com" matches booking.com and m.booking.com, not mybooking.com;
    "tripadvisor." matches tripadvisor.com and tripadvisor.co.ke; a pattern without a
    dot ("yellowpages") matches anywhere in the host."""
    host = host.lower().removeprefix("www.")
    pattern = pattern.lower()
    if "." not in pattern:
        return pattern in host
    if pattern.endswith("."):
        return host.startswith(pattern) or f".{pattern}" in host
    return host == pattern or host.endswith(f".{pattern}")


def _mentions(text: str, words: list[str]) -> bool:
    """The name's leading distinctive word, or at least half of its distinctive words:
    "Aero Club of East Africa" is not africanspicesafaris.com."""
    text = text.replace("-", "").replace("_", "").replace(".", "")
    if words[0] in text:
        return True
    return sum(word in text for word in words) * 2 >= len(words) > 1
