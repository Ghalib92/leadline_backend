"""Finding contacts for an approved opportunity.

1. Read the call's page, then the funder's contact/team pages linked from it.
2. If the funder is known and web search is configured, one web search for its
   contact page, using the page text the search returns.
3. The lead scout picks contacts from those pages. Code then keeps only emails and
   phone numbers that are written on the page they came from, so an address the
   model guessed or constructed is never saved.
4. With HUNTER_API_KEY, published emails for the funder's domain are added.

Limits come from apps/ai/config/outreach.yaml (`lead_scout`). Returns plain dicts;
apps.outreach saves them.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from apps.ai.config.loader import load_outreach_file
from apps.ai.events import NullEmitter
from apps.ai.exceptions import StructuredOutputError
from apps.ai.graphs.budget import Budget, BudgetExhausted
from apps.ai.graphs.runtime import TaskRuntime
from apps.ai.tools.contacts.hunter import HunterError, domain_search, hunter_enabled
from apps.ai.tools.fetch.base import FetchError
from apps.ai.tools.fetch.http import HttpFetcher
from apps.ai.tools.search.base import SearchError

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_DIGITS = re.compile(r"\D+")
# Aggregators and social sites: their domain (and contact page) is not the funder's.
NOT_FUNDER_DOMAINS = (
    "facebook.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "devex.com",
    "fundsforngos.org",
    "grants.gov",
    "worldbank.org",
    "example.org",
)


@dataclass
class LeadResult:
    contacts: list[dict]
    warnings: list[str] = field(default_factory=list)
    prompt_versions: dict = field(default_factory=dict)
    models_used: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)


def find_contacts(
    *, title: str, funder: str, source_url: str, search=None, fetcher=None, emitter=None
) -> LeadResult:
    """`search`: a SearchProvider, or None to skip web search."""
    config = load_outreach_file().lead_scout
    runtime = TaskRuntime(
        budget=Budget(config.budget.model_dump()), emitter=emitter or NullEmitter()
    )
    fetcher = fetcher or HttpFetcher()
    warnings, pages = [], []

    runtime.emit("read", f"Starting contact search for {funder or title}", tool="lead_scout")

    def read(url, text="", emails=()):
        """Add a page (fetched unless its text is given). Returns the fetched page."""
        if any(page["url"] == url for page in pages):
            return None
        if not _spend(runtime.budget.use_page):
            return None
        fetched = None
        if not text:
            runtime.emit("read", f"Reading {_host(url)}", tool="fetch", url=url)
            try:
                fetched = fetcher.fetch(url)
            except FetchError as exc:
                warnings.append(f"Could not read {_host(url)}: {exc}")
                return None
            text, emails = fetched.text, fetched.emails
        text = text[: config.max_page_chars]
        if emails:  # addresses in footers and mailto links, which the main text drops
            text += "\n\nEmail addresses on this page: " + ", ".join(emails)
        pages.append({"n": len(pages) + 1, "url": url, "text": text})
        return fetched

    page = read(source_url) if source_url else None
    if page is not None and not _host(page.url).endswith(NOT_FUNDER_DOMAINS):
        links = _contact_links(page, config.contact_page_words)[: config.max_linked_pages]
        if links:
            runtime.emit(
                "read",
                f"Found {len(links)} contact/team page(s) linked on {page.url}",
                tool="fetch",
            )
        for link in links:
            read(link)
    if funder and search is not None and _spend(runtime.budget.use_search):
        query = f"{funder} contact grants partnerships email"
        runtime.emit("search", f"Searching the web for {funder}'s contacts", tool="search")
        try:
            hits = search.search(query, max_results=3, options={})
        except SearchError as exc:
            warnings.append(f"Web search failed: {exc}")
            hits = []
        if hits:
            runtime.emit(
                "search",
                f"Found {len(hits)} contact candidate page(s) via web search",
                tool="search",
            )
        for hit in hits:
            read(hit.url, hit.content)

    contacts = []
    if pages:
        runtime.emit("read", f"Extracting contacts from {len(pages)} page(s)", tool="lead_scout")
        try:
            extracted = _extract(runtime, title, funder, pages)
            runtime.emit(
                "read",
                f"Extracted {len(extracted)} candidate contact(s) from pages",
                tool="lead_scout",
            )
            contacts.extend(extracted)
        except (StructuredOutputError, BudgetExhausted):
            warnings.append("The contacts on the pages could not be read.")
    if hunter_enabled():
        domain = _funder_domain(contacts, pages)
        if domain and _spend(runtime.budget.use_search):
            runtime.emit("search", f"Looking up published emails for {domain}", tool="hunter")
            try:
                from_hunter = _from_hunter(domain)
                runtime.emit(
                    "search",
                    f"Found {len(from_hunter)} published contact(s) for {domain} via Hunter",
                    tool="hunter",
                )
                contacts.extend(from_hunter)
            except HunterError as exc:
                warnings.append(str(exc))
    final_contacts = _dedupe(contacts)[: config.max_contacts]
    runtime.emit(
        "read",
        f"Contact search complete: {len(final_contacts)} contact(s) saved",
        tool="lead_scout",
    )
    return LeadResult(
        contacts=final_contacts,
        warnings=warnings,
        prompt_versions=dict(runtime.prompt_versions),
        models_used=dict(runtime.models_used),
        usage=runtime.budget.usage(),
    )


def _spend(use) -> bool:
    """Take one search or page from the budget; False when none are left."""
    try:
        use()
    except BudgetExhausted:
        return False
    return True


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def _contact_links(page, words) -> list[str]:
    """Same-site links whose text or address mentions contacts, team, staff…"""
    return [
        link.url
        for link in page.links
        if link.url != page.url and any(w in f"{link.text} {link.url}".lower() for w in words)
    ]


def _extract(runtime, title, funder, pages) -> list[dict]:
    from apps.ai.agents.lead_scout import extract_contacts

    result = extract_contacts(runtime, opportunity=title, funder=funder, pages=pages)
    kept = []
    for item in result.contacts:
        page = next((p for p in pages if p["n"] == item.page), None)
        if page is None:
            continue
        text = page["text"].lower()
        email = item.email.strip()
        if email and (not _EMAIL.match(email) or email.lower() not in text):
            email = ""  # not written on the page: guessed or constructed
        digits = _DIGITS.sub("", item.phone)
        phone = item.phone.strip() if len(digits) >= 7 and digits in _DIGITS.sub("", text) else ""
        if not email and not phone and not (item.name and item.role):
            continue
        kept.append(
            {
                "name": item.name.strip(),
                "role": item.role.strip(),
                "email": email,
                "phone": phone,
                "kind": "inbox" if item.kind == "inbox" else "person",
                "relevance": item.relevance.strip(),
                "confidence": item.confidence,
                "source": "page",
                "source_url": page["url"],
                "verified": False,
            }
        )
    return kept


def _funder_domain(contacts, pages) -> str:
    for contact in contacts:
        if contact["email"]:
            domain = contact["email"].rsplit("@", 1)[1].lower()
            if not domain.endswith(NOT_FUNDER_DOMAINS):
                return domain
    for page in pages:
        host = _host(page["url"])
        if host and not host.endswith(NOT_FUNDER_DOMAINS):
            return host
    return ""


def _from_hunter(domain) -> list[dict]:
    return [
        {
            **item,
            "kind": "person",
            "relevance": "Published email for the funder's domain.",
            "source": "hunter",
            "source_url": f"https://{domain}",
        }
        for item in domain_search(domain)
    ]


def _dedupe(contacts) -> list[dict]:
    seen, unique = set(), []
    for contact in sorted(contacts, key=lambda c: -c["confidence"]):
        key = contact["email"].lower() or f"{contact['name']}|{contact['phone']}".lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(contact)
    return unique
