"""Source Reader: reads custom organization source pages and extracts open calls and links."""

from datetime import date
from html import escape
from urllib.parse import urljoin

from apps.ai.schemas.discovery import ExtractedCall, SourceExtraction
from apps.organizations.choices import OpportunityType

ROLE = "source_reader"
MAX_LINKS_COUNT = 100


def read_source_page(
    runtime,
    *,
    source_url: str,
    page_text: str,
    links: list | tuple = (),
    source_label: str = "",
) -> SourceExtraction:
    """Reads a custom source page, extracting open opportunity calls and their links."""
    formatted_links = _format_links(links)
    opportunity_types = (
        ", ".join(runtime.request.opportunity_types)
        if getattr(runtime.request, "opportunity_types", None)
        else ", ".join(OpportunityType.values)
    )
    max_chars = getattr(runtime.settings, "max_page_chars", 20000)

    extraction = runtime.ask(
        ROLE,
        SourceExtraction,
        today=date.today().isoformat(),
        source_url=escape(source_url),
        source_label=escape(source_label),
        opportunity_types=opportunity_types,
        page_text=escape(page_text[:max_chars], quote=False),
        links=escape(formatted_links, quote=False),
    )

    cleaned_calls = []
    allowed_types = set(OpportunityType.values)
    for call in extraction.calls:
        raw_url = (call.url or "").strip()
        if not raw_url:
            continue
        resolved_url = urljoin(source_url, raw_url)
        opp_type = (
            call.opportunity_type.lower()
            if call.opportunity_type.lower() in allowed_types
            else OpportunityType.OTHER
        )
        cleaned_calls.append(
            ExtractedCall(
                title=call.title.strip(),
                url=resolved_url,
                funder=call.funder.strip(),
                summary=call.summary.strip(),
                opportunity_type=opp_type,
            )
        )

    return SourceExtraction(calls=cleaned_calls)


def _format_links(links: list | tuple) -> str:
    lines = []
    for item in links[:MAX_LINKS_COUNT]:
        if hasattr(item, "url") and hasattr(item, "text"):
            text = (item.text or "").strip() or "Link"
            lines.append(f"- {text}: {item.url}")
        elif isinstance(item, tuple) and len(item) == 2:
            lines.append(f"- {item[0]}: {item[1]}")
        else:
            lines.append(f"- {str(item).strip()}")
    return "\n".join(lines) or "(no links found on page)"
