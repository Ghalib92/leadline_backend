"""A stand-in discovery runner: realistic progress, sample results, no external calls.

It walks the same five steps as the real graph and emits the same kind of events,
so the run timeline, counters, dedupe and the feed can be built and demoed before
the agents exist (and without API keys). Every result is plainly a sample: a
"[Sample]" title, fictional funders and example.org links.

Output is deterministic per run id. One candidate is derived from the organization
id instead, so it reappears in every run and shows up as a duplicate after the first.
"""

import random
import time
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

from apps.ai.events import RunEvent, RunEventEmitter
from apps.ai.graphs.discovery_runner import (
    DiscoveryOutcome,
    DiscoveryRequest,
    DiscoverySink,
    register_runner,
)
from apps.ai.schemas.discovery import Candidate

SITES = {
    "web": ["fundsforngos.org", "devex.com"],
    "grants": ["grants.gov", "ec.europa.eu"],
    "procurement": ["worldbank.org", "ungm.org", "ted.europa.eu"],
    "news": ["reliefweb.int", "devex.com"],
}
FUNDERS = [
    "Savanna Futures Fund",
    "Lakeside Development Trust",
    "Horizon Impact Foundation",
    "Pan-African Growth Facility",
    "Coastal Communities Fund",
    "Northbridge Foundation",
]
TITLES = {
    "grant": "{topic} Innovation Grant {year}",
    "tender": "Consultancy: {topic} assessment in {place}",
    "rfp": "Request for proposals: {topic} services",
    "partnership": "{topic} partnership call",
    "fellowship": "{topic} Leaders Fellowship {year}",
    "accelerator": "{topic} Accelerator, cohort {year}",
    "competition": "{topic} Challenge {year}",
    "award": "{topic} Impact Award {year}",
}
GRANT_LIKE = {"grant", "fellowship", "scholarship", "investment", "loan", "award", "competition"}


def _sleep():
    if settings.DISCOVERY_FAKE_STEP_SECONDS > 0:
        time.sleep(settings.DISCOVERY_FAKE_STEP_SECONDS)


def run_fake_discovery(
    request: DiscoveryRequest, emitter: RunEventEmitter, sink: DiscoverySink
) -> DiscoveryOutcome:
    rng = random.Random(request.run_id)
    budget = request.budget
    warnings = []

    def emit(step, message, **extra):
        emitter.emit(RunEvent(step=step, message=message, **extra))

    # profile: read the context and plan.
    emit("profile", f"Reading the profile of {request.organization_name}")
    _sleep()
    if request.dossier:
        emit("profile", "Using the company dossier", tool="dossier")
    else:
        emit(
            "profile",
            "No company dossier yet; planning from the profile only",
            level="warning",
        )
    if request.folders:
        folder_names = [f["name"] for f in request.folders if f.get("name")]
        emit(
            "profile",
            f"Using {len(folder_names)} knowledge folder(s): {', '.join(folder_names)}",
            tool="folders",
            data={"folders": request.folders},
        )
    topics = _topics(request)
    categories = request.source_categories or ["web"]
    plan = [
        (site, topic)
        for category in categories
        for site in SITES.get(category, SITES["web"])
        for topic in topics[:2]
    ]
    emit(
        "profile",
        f"Planned {len(plan)} searches across {len(categories)} source type(s)",
        data={"queries": [topic for _, topic in plan]},
    )

    # search: one event per query, within the search budget.
    searches, sites_seen = 0, set()
    if request.org_sources:
        for org_source in request.org_sources:
            s_url = org_source.get("url", "https://example.org/custom")
            s_label = org_source.get("label") or s_url
            emit("search", f"Reading custom source {s_label}", tool="source_reader", url=s_url)
            _sleep()
            emit("search", f"1 open call(s) found from {s_label}", tool="source_reader", url=s_url)
    for site, topic in plan:
        if searches >= budget["max_searches"]:
            warnings.append(f"Search budget reached ({budget['max_searches']} searches).")
            emit("search", warnings[-1], level="warning")
            break
        searches += 1
        sites_seen.add(site)
        emit("search", f"Searching {site} for “{topic}”", tool="search", source=site)
        _sleep()
        emit("search", f"{rng.randint(3, 14)} results from {site}", source=site)
    sink.progress(sources_scanned=len(sites_seen), pages_read=0, usage={"searches": searches})

    # read: open a page per candidate, within the page budget.
    stable = _candidate(random.Random(request.organization_id), request, topics, 0)
    drafts = [stable] + [
        _candidate(rng, request, topics, index) for index in range(1, rng.randint(6, 9))
    ]
    pages, read = 0, []
    for draft in drafts:
        if pages >= budget["max_pages"]:
            warnings.append(f"Page budget reached ({budget['max_pages']} pages).")
            emit("read", warnings[-1], level="warning")
            break
        pages += 1
        url = str(draft.source_url)
        emit(
            "read",
            f"Reading {draft.source_name}: {draft.title}",
            tool="fetch",
            url=url,
            source=draft.source_name,
        )
        _sleep()
        if draft is not stable and rng.random() < 0.12:  # the repeating one always stays
            emit("read", f"Skipped “{draft.title}”: the deadline has passed", level="warning")
            continue
        read.append(draft)
    usage = {"searches": searches, "pages": pages}
    sink.progress(sources_scanned=len(sites_seen), pages_read=pages, usage=usage)

    # match: score against the organization.
    emit("match", f"Scoring {len(read)} candidate(s) against your profile", tool="judge")
    _sleep()
    for draft in read:
        emit("match", f"{draft.title}: {draft.match_score}/100", data={"score": draft.match_score})

    # save: dedupe and keep the good ones.
    decisions = {"added": 0, "duplicate": 0, "below_threshold": 0}
    for draft in read:
        decisions[sink.candidate(draft)] += 1
    emit(
        "save",
        f"Added {decisions['added']} new; {decisions['duplicate']} already in your feed; "
        f"{decisions['below_threshold']} below the {request.match_threshold} threshold",
        data=decisions,
    )
    return DiscoveryOutcome(
        status="partial" if warnings else "succeeded",
        warning=" ".join(warnings),
        prompt_versions={},
        models_used={},
    )


def _topics(request: DiscoveryRequest) -> list[str]:
    topics = [keyword for keyword in request.keywords if keyword.strip()]
    topics += [str(sector).replace("_", " ") for sector in request.profile.get("sectors", [])]
    return [topic.strip().capitalize() for topic in topics] or ["Community development"]


def _candidate(rng, request, topics, index) -> Candidate:
    kinds = request.opportunity_types or ["grant"]
    kind = kinds[index % len(kinds)]
    topic = topics[index % len(topics)]
    countries = request.profile.get("operating_countries") or [request.profile.get("country")]
    place = next((country for country in countries if country), "East Africa")
    year = timezone.now().year + (1 if index % 3 == 0 else 0)
    title = "[Sample] " + TITLES.get(kind, "{topic} opportunity {year}").format(
        topic=topic, place=place, year=year
    )
    categories = request.source_categories or ["web"]
    site = rng.choice(SITES.get(categories[index % len(categories)], SITES["web"]))
    amount = Decimal(rng.choice([10_000, 25_000, 50_000, 100_000, 250_000]))
    score = rng.randint(30, 96)
    funder = rng.choice(FUNDERS)
    return Candidate(
        title=title,
        funder=funder,
        summary=(
            "Sample opportunity from the test runner, not a real listing. "
            f"Supports {topic.lower()} work in {place}."
        ),
        opportunity_type=kind,
        source_name=site,
        source_url=f"https://example.org/sample/{slugify(title)[:80]}",
        eligibility="Registered organizations operating in the region (sample).",
        amount_min=amount / 2 if kind in GRANT_LIKE else None,
        amount_max=amount if kind in GRANT_LIKE else None,
        currency="USD" if kind in GRANT_LIKE else "",
        deadline=timezone.now() + timedelta(days=rng.randint(10, 90)),
        location=place,
        match_score=score,
        match_reasons=[
            f"Matches your focus on {topic.lower()}",
            f"Open to organizations in {place}",
        ][: 2 if score >= 60 else 1],
        requirements={
            "brief": {
                "what_it_is": (
                    f"A {kind} call from {funder} supporting {topic.lower()} work in {place}."
                ),
                "why_it_fits": (
                    f"Directly matches the organization's focus on {topic.lower()} "
                    f"and track record in {place}."
                ),
                "eligibility_checklist": [
                    {
                        "criterion": f"Operations in {place}",
                        "meets": True,
                        "notes": f"Active operations established in {place}.",
                    },
                    {
                        "criterion": "Legally registered entity",
                        "meets": True,
                        "notes": "Entity holds appropriate legal registration.",
                    },
                ],
                "documents_needed": [
                    "Technical Proposal / Narrative",
                    "Detailed Activity Budget",
                    "Certificate of Registration",
                ],
                "deadline_plan": (
                    f"Deadline is {year}-12-31; recommended to start drafting 3 weeks before."
                ),
                "effort_estimate": "medium (standard narrative and budget required)",
                "next_step": "Review eligibility checklist and assign proposal drafting lead.",
            }
        },
        verification={"sample": True},
    )


register_runner("fake", run_fake_discovery)
