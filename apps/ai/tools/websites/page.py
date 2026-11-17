"""What a business's web page shows, from its HTML: contacts, features and the signs of
an old or placeholder site. Pure code (no network, no model), so it is cheap and the
page's text never reaches a prompt.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

from apps.ai.schemas.clients import social_kind

PHONE_TEXT = re.compile(r"(?:\+|00)\d[\d\s().-]{7,18}\d|\b0\d{2,3}[\s.-]?\d{3}[\s.-]?\d{3,4}\b")
EMAIL_TEXT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
COPYRIGHT = re.compile(
    r"(?:©|\(c\)|copyright)\D{0,40}?((?:19|20)\d{2})(?:\s*[-–—]\s*((?:19|20)\d{2}))?",
    re.IGNORECASE,
)
# Not addresses: image names (logo@2x.png), error trackers, site builders' own.
JUNK_EMAIL = re.compile(
    r"\.(png|jpe?g|gif|svg|webp)$|@(sentry|wixpress|example|domain|email)\.|^(name|your|user)@",
    re.IGNORECASE,
)
# Social URLs that are posts, searches or share buttons, not a profile.
NOT_PROFILE = {
    "p", "reel", "reels", "explore", "stories", "posts", "watch", "groups", "events",
    "hashtag", "share", "sharer", "sharer.php", "photo", "photos", "video", "videos",
    "tag", "search", "intent", "home", "login", "dialog", "plugins", "tr",
}  # fmt: skip
PROFILE_KINDS = ("instagram", "facebook", "tiktok", "x", "linktree")


@dataclass
class PageFacts:
    viewport: bool = False  # a viewport set up for phones
    copyright_year: int | None = None
    placeholder: str = ""  # the placeholder text found ("coming soon"), if any
    features: dict[str, bool] = field(default_factory=dict)  # booking/chat/payments
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)  # kind → profile URL
    contact_links: list[str] = field(default_factory=list)  # same-site contact pages


def read_page(
    url: str,
    html: str,
    *,
    features: dict[str, list[str]],
    contact_words: list[str],
    placeholder_words: list[str],
) -> PageFacts:
    from lxml import html as lxml_html

    lowered = html.lower()
    facts = PageFacts(
        features={
            name: any(fragment.lower() in lowered for fragment in fragments)
            for name, fragments in features.items()
            if fragments
        }
    )
    try:
        tree = lxml_html.fromstring(html)
    except Exception:  # an unparsable page still had its HTML searched for features
        return facts
    for bad in tree.xpath("//script|//style|//noscript"):
        bad.text = ""  # their code is not the page's text (the HTML was searched above)
    text = " ".join(tree.text_content().split())
    facts.viewport = any(
        "width=device-width" in (meta.get("content") or "").lower()
        for meta in tree.xpath("//meta[@name]")
        if (meta.get("name") or "").lower() == "viewport"
    )
    facts.copyright_year = _copyright_year(text)
    text_lower = text.lower()
    facts.placeholder = next(
        (words for words in placeholder_words if words.lower() in text_lower), ""
    )
    _read_links(facts, url, tree, contact_words)
    phones = [*facts.phones, *(match.strip() for match in PHONE_TEXT.findall(text))]
    facts.phones = _unique([clean_phone(phone) for phone in phones if 9 <= _digits(phone) <= 15])[
        :3
    ]
    emails = [*facts.emails, *EMAIL_TEXT.findall(text)]
    facts.emails = _unique(
        [email.lower().rstrip(".") for email in emails if not JUNK_EMAIL.search(email)]
    )[:3]
    return facts


def _read_links(facts: PageFacts, url: str, tree, contact_words: list[str]) -> None:
    site = (urlsplit(url).hostname or "").removeprefix("www.")
    words = [word.lower() for word in contact_words]
    for anchor in tree.iter("a"):
        href = (anchor.get("href") or "").strip()
        lowered = href.lower()
        if lowered.startswith("tel:"):
            facts.phones.append(unquote(href[4:]).strip())
            continue
        if lowered.startswith("mailto:"):
            facts.emails.append(unquote(href[7:].split("?")[0]).strip())
            continue
        absolute = urljoin(url, href).split("#")[0]
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            continue
        kind = social_kind(absolute)
        if kind == "whatsapp":
            facts.socials.setdefault("whatsapp", _whatsapp(absolute))
        elif kind in PROFILE_KINDS:
            profile = profile_url(absolute)
            if profile:
                facts.socials.setdefault(kind, profile)
        elif (parts.hostname or "").removeprefix("www.") == site:
            label = f"{anchor.text_content()} {parts.path}".lower()
            if any(word in label for word in words) and absolute.rstrip("/") != url.rstrip("/"):
                facts.contact_links.append(absolute)
    facts.contact_links = _unique(facts.contact_links)[:3]


def profile_url(url: str) -> str:
    """The profile a social link points to (https://instagram.com/handle), or "" for a
    post, search or share link."""
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.").removeprefix("m.")
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments or segments[0].lower() in NOT_PROFILE:
        return ""
    if segments[0].lower() == "pages" and len(segments) > 1:  # facebook.com/pages/Name/id
        return f"https://{host}/{'/'.join(segments[:3])}"
    return f"https://{host}/{segments[0]}"


def clean_phone(phone: str) -> str:
    """'(+254) (0) 20 722 9000' → '+254 20 722 9000': no trunk "(0)" after a country
    code, no brackets, single spaces."""
    phone = re.sub(r"(\+\d{1,3}\)?)\s*\(0\)", r"\1 ", phone)
    phone = re.sub(r"[()]", " ", phone)
    return " ".join(phone.split())


def _whatsapp(url: str) -> str:
    """wa.me/2547…, api.whatsapp.com/send?phone=2547… → https://wa.me/2547…"""
    parts = urlsplit(url)
    number = parse_qs(parts.query).get("phone", [""])[0] or parts.path.strip("/")
    digits = "".join(ch for ch in number if ch.isdigit())
    return f"https://wa.me/{digits}" if len(digits) >= 9 else url


def _copyright_year(text: str) -> int | None:
    years = [int(end or start) for start, end in COPYRIGHT.findall(text)]
    return max(years) if years else None


def _digits(phone: str) -> int:
    return sum(ch.isdigit() for ch in phone)


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))
