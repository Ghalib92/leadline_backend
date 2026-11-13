"""Researcher: screens search results and picks the ones worth reading in full."""

from html import escape
from urllib.parse import urlsplit

from apps.ai.exceptions import StructuredOutputError
from apps.ai.schemas.discovery import LeadSelection

ROLE = "researcher"
SNIPPET_CHARS = 300


def select_leads(runtime, leads: list, *, limit: int) -> tuple[list[tuple[object, str]], bool]:
    """(lead, reason) pairs to verify; the flag is True when screening failed and the
    first results were taken as they came."""
    if not leads:
        return [], False
    request = runtime.request
    lines = []
    for number, lead in enumerate(leads, start=1):
        hit = lead.hit
        domain = urlsplit(hit.url).netloc
        snippet = (hit.snippet or hit.content)[:SNIPPET_CHARS].replace("\n", " ")
        lines.append(f"{number}. [{domain}] {hit.title}\n   {snippet}")
    try:
        selection = runtime.ask(
            ROLE,
            LeadSelection,
            opportunity_types=", ".join(request.opportunity_types) or "any",
            max_picks=limit,
            organization=escape(runtime.organization_summary(), quote=False),
            results=escape("\n".join(lines), quote=False),
        )
    except StructuredOutputError:
        return fallback_selection(leads, limit=limit), True
    picked, seen = [], set()
    for pick in selection.picks:
        if 1 <= pick.index <= len(leads) and pick.index not in seen:
            seen.add(pick.index)
            picked.append((leads[pick.index - 1], pick.reason))
    return picked[:limit], False


def fallback_selection(leads: list, *, limit: int) -> list[tuple[object, str]]:
    """Unscreened, read only a few, taking each source's top results in turn."""
    by_source: dict[str, list] = {}
    for lead in leads:
        by_source.setdefault(lead.source, []).append(lead)
    cap = max(3, limit // 3)
    picked = []
    while len(picked) < cap and any(by_source.values()):
        for queue in by_source.values():
            if queue and len(picked) < cap:
                picked.append((queue.pop(0), ""))
    return picked
