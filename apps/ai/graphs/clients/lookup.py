"""Lead lookup: how to reach each business and why it needs you, beyond its listing.

For the best-listed businesses of a client search (sources.yaml clients.lookup.leads,
by depth), in parallel:

    find     no website listed: one web search for its own site and social profiles
    check    its website read like a visitor would: down or a placeholder, HTTPS, set up
             for phones, copyright year, booking, chat, payments
    contact  phone, email, WhatsApp and social links from the page; the contact page too
             when the home page has no phone or email; Hunter.io for an email when the
             site shows none (a few per run, clients.lookup.hunter_per_run)
    speed    Google PageSpeed's mobile score, when PAGESPEED_API_KEY is set

Sites built by JavaScript, or that refuse simple clients, are read through a renderer
(Jina Reader, then Firecrawl); never a site whose robots.txt keeps us out.

Everything is read by code: no page text reaches a model. Searches and pages count
against the run's budget (client runs get one search and pages_per_lead pages per
lookup on top of the depth's limits).
"""

import contextvars
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from apps.ai.exceptions import AIConfigError
from apps.ai.graphs.budget import BudgetExhausted
from apps.ai.schemas.clients import Business
from apps.ai.tools.contacts.hunter import HunterError, domain_search, hunter_enabled
from apps.ai.tools.fetch.renderers import build_renderers
from apps.ai.tools.search.base import SearchError
from apps.ai.tools.websites.check import SiteChecker
from apps.ai.tools.websites.identify import find_presence
from apps.ai.tools.websites.pagespeed import PageSpeedError, mobile_speed, pagespeed_enabled

from .runtime import ClientsRuntime

logger = logging.getLogger(__name__)

STEP = "read"
SEARCH_RESULTS = 8


@dataclass
class LookupTools:
    """The lookup's tools, replaceable in tests. `search` is None when web search isn't
    set up (then only listed websites are checked)."""

    search: object | None
    checker: SiteChecker
    pagespeed: bool = False
    hunter_left: int = 0  # Hunter.io lookups this run may still make
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _warned: set = field(default_factory=set)

    def take_hunter(self) -> bool:
        with self._lock:
            if self.hunter_left <= 0:
                return False
            self.hunter_left -= 1
            return True

    def warn_once(
        self, runtime: ClientsRuntime, key: str, message: str, *, partial: bool = True
    ) -> None:
        with self._lock:
            if key in self._warned:
                return
            self._warned.add(key)
        runtime.warn(STEP, message, partial=partial)


def build_tools(runtime: ClientsRuntime) -> LookupTools:
    search = None
    try:
        from apps.ai.tools.search.tavily import TavilySearch

        search = TavilySearch()
    except AIConfigError:
        runtime.warn(
            STEP,
            "Web search isn't set up on the server (TAVILY_API_KEY), so only websites "
            "the directories list are checked.",
            partial=False,
        )
    lookup = runtime.settings.lookup
    return LookupTools(
        search=search,
        checker=SiteChecker(lookup, renderer=build_renderers(lookup.renderers)),
        pagespeed=pagespeed_enabled(),
        hunter_left=lookup.hunter_per_run if hunter_enabled() else 0,
    )


def look_up_leads(
    runtime: ClientsRuntime, leads: list[dict], *, tools: LookupTools | None = None
) -> list[dict]:
    """Updates the first leads' businesses and signals in place; returns `leads`."""
    limit = runtime.settings.lookup.leads_for(runtime.request.depth)
    chosen = leads[:limit]
    if not chosen:
        return leads
    tools = tools or build_tools(runtime)
    runtime.emit(
        STEP,
        f"Looking up {len(chosen)} businesses: their websites, contacts and signs they need you",
        tool="lookup",
    )
    for lead, business, error in _parallel(runtime, chosen, tools):
        if error is not None:
            logger.warning("Lookup of %s failed: %s", lead["business"].name, error)
            continue
        lead["business"] = business
        lead["signals"] = _signals(runtime, business)
    _summarise(runtime, chosen)
    return leads


def _signals(runtime: ClientsRuntime, business: Business) -> list[str]:
    wanted = frozenset((runtime.request.client_search or {}).get("signals") or [])
    return business.observed_signals(busy_reviews=runtime.settings.busy_reviews, wanted=wanted)


def _parallel(runtime: ClientsRuntime, leads: list[dict], tools: LookupTools):
    """(lead, looked-up business, error) per lead. Lookups stop, and the rest keep their
    listing, when the search or page budget runs out or when only `reserve_seconds` of
    the run's time are left (kept for scoring and saving)."""
    reserve = runtime.settings.lookup.reserve_seconds
    stopped = threading.Event()
    time_up = "Lookups stopped early to leave time for scoring; the other businesses keep "
    time_up += "their listing."

    def work(lead):
        if stopped.is_set():
            return lead["business"]
        if runtime.budget.seconds_left() < reserve:
            stopped.set()
            tools.warn_once(runtime, "time", time_up)
            return lead["business"]
        try:
            return _look_up(runtime, lead["business"], tools)
        except BudgetExhausted as exc:
            stopped.set()
            tools.warn_once(runtime, "budget", f"{exc} The other businesses keep their listing.")
            return lead["business"]

    pool = ThreadPoolExecutor(max_workers=min(runtime.settings.lookup.max_concurrency, len(leads)))
    futures = {pool.submit(contextvars.copy_context().run, work, lead): lead for lead in leads}
    try:
        timeout = max(1, runtime.budget.seconds_left() - reserve / 2)
        for future in as_completed(futures, timeout=timeout):
            lead = futures[future]
            try:
                yield lead, future.result(), None
            except Exception as exc:  # one business failing never stops the run
                yield lead, None, exc
    except TimeoutError:
        stopped.set()
        tools.warn_once(runtime, "time", time_up)
    finally:
        # Pages still loading finish on their own (each has a timeout); nobody waits.
        pool.shutdown(wait=False, cancel_futures=True)


def _look_up(runtime: ClientsRuntime, business: Business, tools: LookupTools) -> Business:
    lookup = runtime.settings.lookup
    found_on = dict(business.found_on)
    socials = dict(business.socials)
    website = business.website
    looked_up = business.looked_up

    if not website and tools.search is not None:
        hits = _web_search(runtime, business, tools)
        if hits is not None:
            looked_up = True
            presence = find_presence(
                hits,
                business.name,
                not_own_site=lookup.not_own_site,
                generic=lookup.generic_name_words,
            )
            if presence.website:
                website = presence.website
                found_on["website"] = "web search"
            for kind, url in presence.socials.items():
                if kind not in socials:
                    socials[kind] = url
                    found_on[kind] = "web search"

    updates = {"website": website, "socials": socials, "looked_up": looked_up}
    if website:
        updates |= _check_site(runtime, business, website, tools, socials, found_on)
    updates["found_on"] = found_on
    return business.model_copy(update=updates)


def _web_search(runtime: ClientsRuntime, business: Business, tools: LookupTools):
    """The search's results, or None when it failed (the business stays "unknown")."""
    where = business.city or business.address or business.country
    query = f'"{business.name}" {where}'.strip()
    runtime.budget.use_search()
    runtime.emit(
        STEP,
        f"Searching the web for {business.name}'s website",
        tool="web_search",
        data={"query": query},
    )
    try:
        return tools.search.search(
            query,
            max_results=SEARCH_RESULTS,
            options={"include_raw_content": False, "max_wait": 30},
        )
    except SearchError as exc:
        tools.warn_once(runtime, "search", f"Web search failed: {exc}", partial=False)
        return None


def _check_site(runtime, business, website, tools, socials, found_on) -> dict:
    lookup = runtime.settings.lookup
    runtime.budget.use_page()
    host = _host(website)
    runtime.emit(STEP, f"Checking {host}", tool="website_check", url=website)

    def render(url):
        """A renderer reads the page: a page of the budget, and a timeline event."""
        try:
            runtime.budget.use_page()
        except BudgetExhausted:
            return False
        runtime.emit(STEP, f"Reading {host} as a browser would", tool="renderer", url=url)
        return True

    visit = tools.checker.visit(website, before_render=render)
    if visit is None:
        return {}
    site, page = visit.check, visit.page
    updates = {}
    contacts = {"phone": business.phone, "email": business.email}

    def take(source_page, where):
        for key, values in (("phone", source_page.phones), ("email", source_page.emails)):
            if not contacts[key] and values:
                contacts[key] = updates[key] = values[0]
                found_on[key] = where
        for kind, url in source_page.socials.items():
            if kind not in socials:
                socials[kind] = url
                found_on[kind] = where

    take(page, "website")
    pages_left = lookup.pages_per_lead - 1
    if site.readable and not (contacts["phone"] and contacts["email"]):
        for link in page.contact_links[:pages_left]:
            runtime.budget.use_page()
            runtime.emit(STEP, f"Reading {host}'s contact page", tool="website_check", url=link)
            more = tools.checker.contacts(link)
            if more is None:
                continue
            take(more, "website")
            for feature, present in more.features.items():
                if present:
                    site = site.model_copy(update={feature: True})
    if not contacts["email"] and site.reachable:
        email = _hunter_email(runtime, website, tools)
        if email:
            contacts["email"] = updates["email"] = email
            found_on["email"] = "Hunter"
    if site.readable and tools.pagespeed:
        site = _speed(runtime, site, tools)
    runtime.emit(
        STEP,
        _check_message(host, site),
        tool="website_check",
        url=site.url,
        data={"signals": site.signals()},
    )
    return {**updates, "site": site}


def _hunter_email(runtime, website: str, tools) -> str:
    """The business's best published email for its domain, from Hunter.io."""
    domain = _host(website)
    if not domain or not tools.take_hunter():
        return ""
    runtime.emit(STEP, f"Looking up an email for {domain} on Hunter", tool="hunter")
    try:
        found = domain_search(domain, limit=5, kind=None)
    except HunterError as exc:
        tools.warn_once(runtime, "hunter", f"Hunter: {exc}", partial=False)
        return ""
    # A team inbox (info@, bookings@) first: the business's own front door.
    found.sort(key=lambda item: (bool(item["name"]), -item["confidence"]))
    return found[0]["email"] if found else ""


def _speed(runtime, site, tools):
    lookup = runtime.settings.lookup
    runtime.budget.check_time()
    try:
        score = mobile_speed(site.url, timeout=lookup.pagespeed_timeout_seconds)
    except PageSpeedError as exc:
        if exc.quota:
            tools.pagespeed = False
            tools.warn_once(runtime, "pagespeed", f"{exc} Speed checks are skipped.", partial=False)
        else:
            logger.info("PageSpeed failed for %s: %s", site.url, exc)
        return site
    runtime.emit(
        STEP,
        f"PageSpeed mobile score for {_host(site.url)}: {score}/100",
        tool="pagespeed",
        url=site.url,
    )
    return site.model_copy(update={"mobile_speed": score, "slow": score < lookup.slow_below})


def _check_message(host: str, site) -> str:
    if not site.reachable:
        return f"{host} is down: {site.problem}"
    if site.blocked:
        return f"{host} couldn't be read ({site.problem or 'blocked'}); not checked"
    notes = []
    if site.https is False:
        notes.append("no HTTPS")
    if site.mobile_ready is False:
        notes.append("not set up for phones")
    if site.stale:
        notes.append(f"© {site.copyright_year}")
    if site.slow:
        notes.append("slow on phones")
    via = f" (read through {site.read_by})" if site.read_by else ""
    return f"{host}{via}: " + (", ".join(notes) if notes else "looks up to date")


def _summarise(runtime: ClientsRuntime, leads: list[dict]) -> None:
    businesses = [lead["business"] for lead in leads]
    found = sum("website" in b.found_on for b in businesses)
    down = sum(b.website_status() == "down" for b in businesses)
    contacts = sum(any(key in b.found_on for key in ("phone", "email")) for b in businesses)
    none = sum(b.website_status() == "not_found" for b in businesses)
    parts = [f"{found} website(s) found", f"{none} with none found"]
    if down:
        parts.append(f"{down} down")
    parts.append(f"contacts added for {contacts}")
    runtime.emit(
        STEP,
        "Lookup done: " + ", ".join(parts),
        data={"found": found, "not_found": none, "down": down, "contacts": contacts},
    )


def _host(url: str) -> str:
    return (urlsplit(url).hostname or url).removeprefix("www.")
