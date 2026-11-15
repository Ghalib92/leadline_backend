"""Client search: finds businesses that could buy what the organization sells.

    profile  read the clients brief and plan: each kind of customer and keyword, per place
    search   ask each business directory (sources.yaml category "businesses")
    read     merge duplicates across directories, drop ones already in the feed, and note
             the signs each listing shows (no website, social only…)
    match    the client judge scores them in batches against the brief
    save     hand each to the sink, which dedupes and saves it as a client lead

It runs for discovery runs whose goal is "clients" (both the "graph" runner and the
"fake" one hand them over). Budgets are the run's: each directory call is a search,
and model tokens and wall time are counted; running out ends the run as partial with
what it has.
"""

import logging

from apps.ai.config.loader import load_sources_file
from apps.ai.exceptions import AIConfigError, StructuredOutputError
from apps.ai.graphs.budget import Budget, BudgetExhausted
from apps.ai.graphs.discovery_runner import DiscoveryOutcome, DiscoveryRequest, DiscoverySink
from apps.ai.schemas.clients import Business
from apps.ai.schemas.discovery import Candidate
from apps.ai.tools.businesses.base import BusinessQuery, SearchError, SourceOff
from apps.ai.tools.businesses.registry import build_business_source
from apps.opportunities.dedupe import normalize_text, normalize_url

from .runtime import ClientsRuntime
from .scoring import lead_score

logger = logging.getLogger(__name__)


def run_clients_search(
    request: DiscoveryRequest, emitter, sink: DiscoverySink, *, sources: dict | None = None
) -> DiscoveryOutcome:
    """`sources` (name → BusinessSource) replaces the configured ones, for tests and the
    fake runner."""
    config = load_sources_file()
    runtime = ClientsRuntime(
        request=request,
        emitter=emitter,
        sink=sink,
        settings=config.clients,
        budget=Budget(request.budget or config.discovery.budget.model_dump()),
    )
    stop = ""
    try:
        queries = _plan(runtime)
        if sources is None:
            sources = _build_sources(runtime, config)
        businesses = _search(runtime, sources, queries)
        leads = _compare(runtime, businesses)
        scored = _judge(runtime, leads)
        _save(runtime, scored)
    except _Stop as exc:
        stop = str(exc)
    except BudgetExhausted as exc:  # anything not caught where it is spent
        stop = str(exc)
        runtime.warn("save", stop)
    reasons = list(dict.fromkeys([*([stop] if stop else []), *runtime.warnings]))
    return DiscoveryOutcome(
        status="partial" if reasons else "succeeded",
        warning=" ".join(reasons)[:500],
        prompt_versions=dict(runtime.prompt_versions),
        models_used=dict(runtime.models_used),
    )


class _Stop(Exception):
    """Nothing more can be done; the message says why (already shown on the timeline)."""


# ---------------------------------------------------------------------------
# profile: what to search for, and where
# ---------------------------------------------------------------------------


def _plan(runtime: ClientsRuntime) -> list[BusinessQuery]:
    request = runtime.request
    plan = request.client_search or {}
    brief = request.profile.get("client_brief") or {}
    runtime.emit("profile", f"Reading the clients brief of {request.organization_name}")
    if brief.get("services"):
        names = ", ".join(service.get("name", "") for service in brief["services"][:5])
        runtime.emit("profile", f"Selling: {names}", tool="brief")
    else:
        runtime.warn(
            "profile",
            "The clients brief doesn't say what you sell yet, so leads are scored on the "
            "kind of customer only.",
            partial=False,
        )
    kinds = list(plan.get("customer_types") or [])
    keywords = [keyword for keyword in request.keywords if keyword.strip()]
    places = (plan.get("places") or [])[: runtime.settings.max_places]
    country = plan.get("country", "")
    if not places:
        message = "Add the cities or towns you sell in to the clients brief, then run again."
        runtime.warn("profile", message)
        raise _Stop(message)
    if not kinds and not keywords:
        message = "Pick the kinds of customer you want in the clients brief, then run again."
        runtime.warn("profile", message)
        raise _Stop(message)
    labels = dict(zip(kinds, brief.get("customer_types") or [], strict=False))
    queries = [
        *(BusinessQuery(category=kind, keyword="", place=place, country=country)
          for place in places for kind in kinds),
        *(BusinessQuery(category="", keyword=keyword, place=place, country=country)
          for place in places for keyword in keywords),
    ]  # fmt: skip
    runtime.emit(
        "profile",
        f"Planned {len(queries)} search(es) in {', '.join(places)}",
        data={"queries": [query.describe(labels.get(query.category, "")) for query in queries]},
    )
    runtime.labels.update(labels)
    return queries


def _build_sources(runtime: ClientsRuntime, config) -> dict:
    sources = {}
    for name in config.sources_for(runtime.request.source_categories or ["businesses"]):
        spec = config.sources[name]
        try:
            sources[name] = build_business_source(name, spec)
        except SourceOff:
            logger.info("Business source %s is switched off", name)
        except (SearchError, AIConfigError) as exc:
            logger.warning("Business source %s is not configured: %s", name, exc)
            runtime.warn("profile", f"{spec.domain} is not set up on the server; skipping it.")
    if not sources:
        raise AIConfigError("No business directory is configured for client searches.")
    runtime.emit(
        "profile",
        "Searching " + ", ".join(source.domain for source in sources.values()),
        tool="sources",
    )
    return sources


# ---------------------------------------------------------------------------
# search: each query in each directory that supports it, within the search budget
# ---------------------------------------------------------------------------


def _search(runtime: ClientsRuntime, sources: dict, queries) -> list[Business]:
    found: list[Business] = []
    scanned: set[str] = set()
    per_search = runtime.settings.results_per_search
    labels = runtime.labels
    try:
        for query in queries:
            what = query.describe(labels.get(query.category, ""))
            for source in sources.values():
                if not source.supports(query):
                    continue
                runtime.budget.use_search()
                scanned.add(source.name)
                runtime.emit(
                    "search",
                    f"Searching {source.domain} for {what}",
                    tool="business_search",
                    source=source.domain,
                )
                try:
                    results = source.search(query, max_results=per_search)
                except SearchError as exc:
                    runtime.warn("search", f"{source.domain}: {exc}", source=source.domain)
                    continue
                runtime.emit(
                    "search",
                    f"{len(results)} businesses from {source.domain}",
                    source=source.domain,
                    data={"query": what, "found": len(results)},
                )
                found += results
    except BudgetExhausted as exc:
        runtime.warn("search", str(exc))
    runtime.sources_scanned = len(scanned)
    _progress(runtime)
    if not found:
        message = "No businesses were found for this brief."
        runtime.emit("search", message, level="warning")
        raise _Stop(message)
    return found


# ---------------------------------------------------------------------------
# read: one lead per business, the new ones, with the signs their listing shows
# ---------------------------------------------------------------------------


def _key(business: Business) -> str:
    return f"{normalize_text(business.name)}|{normalize_text(business.city)}"


def _phone_key(phone: str) -> str:
    """The last nine digits: 0723 226 629 and +254 723 226 629 are the same number."""
    digits = "".join(ch for ch in phone.split("/")[0] if ch.isdigit())
    return digits[-9:] if len(digits) >= 9 else ""


def _url(business: Business) -> str:
    """The lead's link: its own website when it has one, else its listing."""
    return business.website or business.source_url


def _compare(runtime: ClientsRuntime, businesses: list[Business]) -> list[dict]:
    known = runtime.request.known_urls
    merged: dict[str, Business] = {}
    by_phone: dict[str, str] = {}  # the same number twice is one business, typos or not
    for business in businesses:
        phone = _phone_key(business.phone)
        key = by_phone.get(phone, _key(business)) if phone else _key(business)
        current = merged.get(key)
        # The same business in two directories: keep the more complete listing.
        if current is None or (business.complete_listing and not current.complete_listing):
            merged[key] = business
        if phone:
            by_phone.setdefault(phone, key)
    fresh = [
        business
        for business in merged.values()
        if normalize_url(_url(business)) not in known
        and normalize_url(business.source_url) not in known
    ]
    skipped = len(merged) - len(fresh)
    # Best-listed first: a complete listing, a way to reach them, an active business.
    fresh.sort(
        key=lambda b: (
            not b.complete_listing,
            not (b.phone or b.email or b.website or b.socials),
            -(b.reviews or 0),
            b.name.lower(),
        )
    )
    kept = fresh[: runtime.settings.max_candidates]
    message = f"{len(merged)} distinct businesses"
    if skipped:
        message += f"; {skipped} already in your feed"
    if len(fresh) > len(kept):
        message += f"; scoring the {len(kept)} best listed"
    runtime.emit("read", message, data={"distinct": len(merged), "known": skipped})
    if not kept:
        raise _Stop("Every business found is already in your feed.")
    busy = runtime.settings.busy_reviews
    return [
        {"business": business, "signals": business.observed_signals(busy_reviews=busy)}
        for business in kept
    ]


# ---------------------------------------------------------------------------
# match: the client judge, in batches
# ---------------------------------------------------------------------------


def _facts(lead: dict) -> dict:
    """What the judge sees of a business: listed facts and observed signals only."""
    business: Business = lead["business"]
    facts = business.model_dump(
        include={
            "name", "category", "address", "city", "country", "phone", "email",
            "website", "socials", "rating", "reviews", "complete_listing",
        },
        exclude_defaults=True,
    )  # fmt: skip
    facts["complete_listing"] = business.complete_listing
    # Spelled out, so no model reads a gap into a directory that just doesn't list it.
    if business.website:
        facts["website_status"] = "has a website"
    elif business.complete_listing:
        facts["website_status"] = "none (the directory lists websites, and has none for it)"
    else:
        facts["website_status"] = (
            f"unknown: {business.source_label} doesn't list it; never say it has none"
        )
    facts["observed_signals"] = lead["signals"]
    facts["listed_on"] = business.source_label
    return facts


def _judge(runtime: ClientsRuntime, leads: list[dict]) -> list[dict]:
    from apps.ai.agents.client_judge import judge_businesses

    size = runtime.settings.judge_batch_size
    scored = []
    for start in range(0, len(leads), size):
        batch = leads[start : start + size]
        runtime.emit(
            "match",
            f"Scoring businesses {start + 1}–{start + len(batch)} against your brief",
            tool="client_judge",
        )
        try:
            verdicts = judge_businesses(runtime, [_facts(lead) for lead in batch])
        except BudgetExhausted as exc:
            runtime.warn("match", f"{exc} The businesses scored so far are kept.")
            break
        except StructuredOutputError as exc:
            if exc.rate_limited:
                runtime.warn(
                    "match",
                    "The AI provider's usage limit was reached, so the rest weren't scored. "
                    "Run the search again later.",
                )
                break
            runtime.warn("match", "The scorer gave an unreadable answer for one batch; skipped.")
            continue
        by_number = {verdict.index: verdict for verdict in verdicts.verdicts}
        wanted = set((runtime.request.client_search or {}).get("signals") or [])
        for number, lead in enumerate(batch, start=1):
            verdict = by_number.get(number)
            if verdict is not None:
                score = lead_score(
                    lead["business"],
                    lead["signals"],
                    verdict.fit,
                    wanted,
                    busy_reviews=runtime.settings.busy_reviews,
                )
                scored.append({**lead, "verdict": verdict, "score": score})
    if not scored:
        raise _Stop("No business could be scored.")
    return scored


# ---------------------------------------------------------------------------
# save: hand each to the sink (it dedupes and applies the match threshold)
# ---------------------------------------------------------------------------


def _summary(business: Business) -> str:
    parts = [business.category.capitalize() if business.category else "Business"]
    place = ", ".join(part for part in (business.address, business.city) if part)
    if place:
        parts.append(f"in {place}")
    text = " ".join(parts) + "."
    if business.rating is not None:
        text += f" Rated {business.rating:g}"
        text += f" from {business.reviews} reviews." if business.reviews else "."
    return text


def _save(runtime: ClientsRuntime, scored: list[dict]) -> None:
    added = 0
    for lead in sorted(scored, key=lambda item: -item["score"]):
        business: Business = lead["business"]
        verdict = lead["verdict"]
        try:
            candidate = _candidate(business, lead["signals"], verdict, lead["score"])
        except ValueError:  # a listing URL that isn't a valid web address
            logger.info("Skipping %s: invalid URL", business.name)
            continue
        decision = runtime.sink.candidate(candidate)
        if decision == "added":
            added += 1
            runtime.emit(
                "save",
                f"Added {business.name} ({lead['score']}%)",
                url=str(candidate.source_url),
            )
    runtime.emit("save", f"{added} new client lead(s) added to your feed")
    _progress(runtime)


def _candidate(business: Business, signals: list[str], verdict, score: int) -> Candidate:
    return Candidate(
        title=business.name,
        summary=_summary(business),
        opportunity_type="client",
        source_name=business.source_label,
        source_url=_url(business),
        location=", ".join(part for part in (business.address, business.city) if part)[:200],
        match_score=score,
        match_reasons=verdict.reasons[:4],
        business=business.as_details(signals=signals, pitch=verdict.pitch_angle),
    )


def _progress(runtime: ClientsRuntime) -> None:
    runtime.sink.progress(
        sources_scanned=runtime.sources_scanned,
        pages_read=0,
        usage={"searches": runtime.budget.searches, "tokens": runtime.budget.tokens},
    )
