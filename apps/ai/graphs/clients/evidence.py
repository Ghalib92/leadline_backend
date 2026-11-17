"""Why a business needs you, in sentences a salesperson can repeat: one per signal,
built from checked facts only (no model writes these)."""

from urllib.parse import urlsplit

from apps.ai.schemas.clients import Business

SOCIAL_NAMES = {
    "instagram": "Instagram",
    "facebook": "Facebook",
    "tiktok": "TikTok",
    "x": "X",
    "linktree": "a link page",
    "whatsapp": "WhatsApp",
}


def evidence(business: Business, signals: list[str]) -> list[dict]:
    """[{signal, detail}] for each signal that has something specific to say."""
    site = business.site
    host = _host(site.url if site else business.website)
    details = {
        "no_website": _no_website(business),
        "social_only": (
            f"Only on {_join([SOCIAL_NAMES.get(kind, kind) for kind in business.socials])}, "
            "with no website of its own."
            if business.socials
            else ""
        ),
        "broken_website": f"Its website {host} is down: {site.problem}." if site else "",
        "no_https": (
            f"{host}'s security certificate is invalid, so browsers warn visitors away."
            if site and site.certificate_error
            else f"{host} has no HTTPS, so browsers mark it “Not secure”."
        ),
        "not_mobile_friendly": f"{host} isn't set up for phones (no mobile layout).",
        "slow_website": (
            f"{host} scores {site.mobile_speed}/100 for speed on phones (Google PageSpeed)."
            if site and site.mobile_speed is not None
            else ""
        ),
        "outdated_website": (
            f"{host} still shows © {site.copyright_year}." if site and site.copyright_year else ""
        ),
        "no_online_booking": f"No online booking or ordering on {host}.",
        "no_chatbot": f"No chat on {host} to answer customers.",
        "no_online_payments": f"No online payments on {host}.",
        "many_reviews": (
            f"{business.reviews:,} reviews: a busy business." if business.reviews else ""
        ),
    }
    return [
        {"signal": signal, "detail": details[signal]} for signal in signals if details.get(signal)
    ]


def _no_website(business: Business) -> str:
    if business.complete_listing:
        return f"{business.source_label} lists no website for it."
    return f"No website on {business.source_label}, and none found in a web search."


def _host(url: str) -> str:
    return (urlsplit(url).hostname or url or "Its website").removeprefix("www.")


def _join(items: list[str]) -> str:
    items = list(dict.fromkeys(items))
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
