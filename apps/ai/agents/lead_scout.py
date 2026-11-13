"""Lead scout: picks the right contacts from pages about an opportunity and its funder."""

from html import escape

from apps.ai.schemas.outreach import ContactList

ROLE = "lead_scout"


def extract_contacts(runtime, *, opportunity: str, funder: str, pages: list[dict]):
    """`pages`: [{n, url, text}] (text already cut to size)."""
    blocks = "\n\n".join(
        f'<page n="{page["n"]}" url="{escape(page["url"])}">\n'
        f"{escape(page['text'], quote=False)}\n</page>"
        for page in pages
    )
    return runtime.ask(
        ROLE,
        ContactList,
        opportunity=escape(opportunity, quote=False),
        funder=escape(funder or "unknown", quote=False),
        pages=blocks,
    )
