"""Graph nodes. Each takes the shared runtime and the state, and returns updates.

Budgets: a node that runs out stores the reason in `stop`; later nodes then skip
their work, except `save`, which keeps what was already scored.
"""

import contextvars
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time
from decimal import Decimal
from urllib.parse import urljoin, urlsplit

from django.utils import timezone

from apps.ai.agents.judge import judge_fit, judge_role
from apps.ai.agents.researcher import select_leads
from apps.ai.agents.source_reader import read_source_page
from apps.ai.agents.supervisor import plan_searches
from apps.ai.agents.verifier import verify_listing
from apps.ai.exceptions import AIConfigError, StructuredOutputError
from apps.ai.graphs.budget import BudgetExhausted
from apps.ai.schemas.discovery import Candidate, VerifiedOpportunity
from apps.ai.tools.decisions import past_decisions
from apps.ai.tools.fetch.base import FetchError
from apps.ai.tools.knowledge import NO_DOCUMENTS, company_evidence
from apps.ai.tools.search.base import SearchError, SearchHit
from apps.discovery.choices import SkipReason
from apps.knowledge.scope import KnowledgeScope
from apps.opportunities.dedupe import normalize_text, normalize_url
from apps.organizations.choices import TenderEligibility

from .state import DiscoveryState, Lead

logger = logging.getLogger(__name__)
DONE = "done"  # `stop` value of a run that finished normally
STRUCTURED_SOURCES = frozenset(
    {"grants_gov", "world_bank", "eu_funding", "ted_europa", "kenya_ppip"}
)


def _stopped(state) -> bool:
    return bool(state.get("stop"))


def _progress(runtime, state, sources_searched=None):
    runtime.sink.progress(
        sources_scanned=len(sources_searched or state.get("sources_searched", [])),
        pages_read=runtime.budget.pages,
        usage=runtime.budget.usage(),
    )


def _parallel(runtime, items, work):
    """Run `work(item)` in a small thread pool; yields (item, result, error)."""
    workers = min(runtime.settings.max_concurrency, max(1, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            # Each thread gets a copy of the context, so tracing spans nest correctly.
            pool.submit(contextvars.copy_context().run, work, item): item
            for item in items
        }
        try:
            for future in as_completed(futures, timeout=max(1, runtime.budget.seconds_left())):
                item = futures[future]
                try:
                    yield item, future.result(), None
                except (AIConfigError, BudgetExhausted):
                    raise
                except Exception as exc:  # one listing failing never stops the run
                    yield item, None, exc
        except TimeoutError as exc:
            for future in futures:
                future.cancel()
            raise BudgetExhausted(
                f"Time limit reached ({runtime.budget.limits['max_wall_seconds']} seconds).",
                hard=True,
            ) from exc


# ---------------------------------------------------------------------------


def load_context(runtime, state: DiscoveryState) -> DiscoveryState:
    request = runtime.request
    runtime.emit("profile", f"Reading the profile of {request.organization_name}")
    if request.dossier:
        runtime.emit("profile", "Using the company dossier", tool="dossier")
    else:
        runtime.warn(
            "profile",
            "No company dossier yet; planning from the profile only. Results improve once "
            "the profile or documents are filled in.",
            partial=False,
        )
    names = ", ".join(runtime.source_specs[name].domain for name in runtime.sources)
    runtime.emit("profile", f"Sources available: {names}", data={"sources": list(runtime.sources)})
    if request.org_sources:
        runtime.emit(
            "profile",
            f"Using {len(request.org_sources)} custom source(s) from Settings",
            tool="org_sources",
            data={"sources": [s["url"] for s in request.org_sources]},
        )
    if request.folders:
        folder_names = [f["name"] for f in request.folders if f.get("name")]
        runtime.emit(
            "profile",
            f"Using {len(folder_names)} knowledge folder(s): {', '.join(folder_names)}",
            tool="folders",
            data={"folders": request.folders},
        )
    return {
        "round": 1,
        "done_queries": [],
        "seen_urls": [],
        "added": 0,
        "sources_searched": [],
        "stop": "",
        "exhausted": False,
    }


def plan(runtime, state: DiscoveryState) -> DiscoveryState:
    if _stopped(state):
        return {"planned": []}
    limit = min(runtime.budget.searches_left(), _per_round_searches(runtime, state))
    try:
        planned, fallback = plan_searches(runtime, done=state["done_queries"], limit=limit)
    except BudgetExhausted as exc:
        return {"planned": [], "stop": str(exc)}
    if fallback:
        runtime.warn(
            "profile",
            "The planner's answer was unusable; searching your keywords directly instead.",
            partial=False,
        )
    runtime.emit(
        "profile",
        f"Planned {len(planned)} search(es)"
        + (f" (round {state['round']})" if state["round"] > 1 else ""),
        tool="supervisor",
        data={"searches": [{"source": s, "query": q} for s, q in planned]},
    )
    return {"planned": planned}


def _max_rounds(runtime) -> int:
    """The run's depth sets its rounds; runs queued before depths existed use the default."""
    return runtime.budget.limits.get("max_rounds", runtime.settings.max_rounds)


def _per_round_searches(runtime, state):
    """Spread the search budget over the rounds, keeping some for later rounds."""
    rounds_left = _max_rounds(runtime) - state["round"] + 1
    return max(1, -(-runtime.budget.searches_left() // rounds_left))


def search(runtime, state: DiscoveryState) -> DiscoveryState:
    if _stopped(state):
        return {"leads": []}
    seen = set(state["seen_urls"]) | runtime.request.known_urls
    done = list(state["done_queries"])
    searched = list(state["sources_searched"])
    leads, stop, exhausted, skipped_known = [], "", False, 0
    for source_name, query in state["planned"]:
        source = runtime.sources[source_name]
        try:
            runtime.budget.use_search()
        except BudgetExhausted as exc:
            runtime.warn("search", str(exc))
            stop, exhausted = (str(exc), False) if exc.hard else ("", True)
            break
        done.append(f"{source_name}: {query}")
        runtime.emit(
            "search",
            f"Searching {source.domain} for “{query}”",
            tool="search",
            source=source.domain,
            data={"source": source_name, "query": query},
        )
        try:
            hits = source.search(query, max_results=runtime.settings.results_per_search)
        except SearchError as exc:
            runtime.warn("search", f"{source.domain} search failed: {exc}", source=source.domain)
            continue
        if source_name not in searched:
            searched.append(source_name)
        spec = runtime.source_specs.get(source_name)
        if spec is not None and spec.options.get("list_pages"):
            # Job boards answer with pages of many adverts: read the best ones and
            # take each advert, instead of asking the researcher about list pages.
            try:
                found = _adverts_from_lists(runtime, source, spec.options, hits, seen)
            except BudgetExhausted as exc:
                runtime.warn("search", str(exc))
                stop, exhausted = (str(exc), False) if exc.hard else ("", True)
                break
            leads += found
            continue
        fresh = 0
        for hit in hits:
            key = normalize_url(hit.url)
            if key in seen:
                skipped_known += key in runtime.request.known_urls
                continue
            seen.add(key)
            fresh += 1
            leads.append(Lead(source=source_name, domain=source.domain, hit=hit))
        runtime.emit(
            "search",
            f"{len(hits)} result(s) from {source.domain}"
            + (f", {fresh} new" if fresh != len(hits) else ""),
            source=source.domain,
        )

    # Custom organization sources: read via source_reader in round 1
    if state.get("round", 1) == 1 and runtime.request.org_sources:
        for org_source in runtime.request.org_sources:
            source_url = org_source.get("url")
            if not source_url:
                continue
            source_label = org_source.get("label", "")
            runtime.emit(
                "search",
                f"Reading custom source {source_label or source_url}",
                tool="source_reader",
                url=source_url,
            )
            try:
                page = runtime.fetcher.fetch(source_url)
            except Exception as exc:
                runtime.warn(
                    "search",
                    f"Could not read custom source {source_url}: {exc}",
                    partial=False,
                    url=source_url,
                )
                continue
            try:
                extraction = read_source_page(
                    runtime,
                    source_url=source_url,
                    page_text=page.text,
                    links=page.links,
                    source_label=source_label,
                )
            except BudgetExhausted as exc:
                runtime.warn("search", str(exc))
                stop, exhausted = (str(exc), False) if exc.hard else ("", True)
                break
            except Exception as exc:
                logger.warning("source_reader failed for %s: %s", source_url, exc)
                continue

            calls_found = 0
            for call in extraction.calls:
                key = normalize_url(call.url)
                if key in seen:
                    skipped_known += key in runtime.request.known_urls
                    continue
                seen.add(key)
                calls_found += 1
                domain = urlsplit(call.url).netloc.removeprefix("www.")
                leads.append(
                    Lead(
                        source="org_source",
                        domain=domain,
                        hit=SearchHit(
                            title=call.title,
                            url=call.url,
                            snippet=call.summary,
                        ),
                        reason=f"Custom source: {source_label or source_url}",
                    )
                )
            runtime.emit(
                "search",
                f"{calls_found} open call(s) found from {source_label or source_url}",
                tool="source_reader",
                url=source_url,
            )

    if skipped_known:
        runtime.emit("search", f"Skipped {skipped_known} result(s) already in your feed")
    _progress(runtime, state, searched)
    return {
        "leads": leads,
        "done_queries": done,
        "seen_urls": sorted(seen - runtime.request.known_urls),
        "sources_searched": searched,
        "stop": stop,
        "exhausted": state.get("exhausted", False) or exhausted,
    }


def _adverts_from_lists(runtime, source, options, hits, seen) -> list:
    """The adverts on a job board's list pages, each as a lead of its own (picked, like
    an organization's own source's calls). Raises BudgetExhausted."""
    pages = int(options.get("list_pages", 2))
    per_page = int(options.get("adverts_per_page", 6))
    leads = []
    for hit in hits[:pages]:
        runtime.budget.use_page()
        runtime.emit(
            "search",
            f"Reading the adverts on {hit.title[:120]}",
            tool="source_reader",
            url=hit.url,
            source=source.domain,
        )
        try:
            page = runtime.fetcher.fetch(hit.url)
            extraction = read_source_page(
                runtime,
                source_url=hit.url,
                page_text=page.text,
                links=page.links,
                source_label=source.domain,
            )
        except (FetchError, StructuredOutputError) as exc:
            runtime.warn(
                "search", f"Could not read {hit.url[:120]}: {exc}", partial=False, url=hit.url
            )
            continue
        found = 0
        for call in extraction.calls[:per_page]:
            url = urljoin(hit.url, call.url)
            key = normalize_url(url)
            if key in seen:
                continue
            seen.add(key)
            found += 1
            leads.append(
                Lead(
                    source="org_source",
                    domain=urlsplit(url).netloc.removeprefix("www."),
                    hit=SearchHit(title=call.title, url=url, snippet=call.summary),
                    reason=f"Advertised on {source.domain}",
                )
            )
        runtime.emit("search", f"{found} advert(s) found", source=source.domain)
    return leads


def screen(runtime, state: DiscoveryState) -> DiscoveryState:
    leads = state.get("leads", [])
    if _stopped(state) or not leads:
        return {"picked": []}
    org_leads = [lead for lead in leads if lead.source == "org_source"]
    # Official portals return whole records (tenders.go.ke): read them all, up to a cap,
    # rather than spending the researcher's picks on them. Reading them fetches nothing.
    portal_leads = [
        lead
        for lead in leads
        if lead.source != "org_source"
        and getattr(runtime.sources.get(lead.source), "complete_listings", False)
    ][: runtime.settings.max_portal_reads_per_round]
    other_leads = [
        lead for lead in leads if lead.source != "org_source" and lead not in portal_leads
    ]
    picks = []
    if portal_leads:
        for lead in portal_leads:
            lead.reason = lead.reason or "Listed on an official tender portal"
        picks.extend(portal_leads)
        runtime.emit(
            "search",
            f"Reading all {len(portal_leads)} result(s) from official portals",
            data={"sources": sorted({lead.source for lead in portal_leads})},
        )
    if other_leads:
        try:
            other_picks, fallback = select_leads(
                runtime, other_leads, limit=runtime.settings.max_leads_per_round
            )
        except BudgetExhausted as exc:
            runtime.warn("search", str(exc))
            return {"picked": org_leads, "stop": str(exc)}
        for lead, reason in other_picks:
            lead.reason = reason
        picks.extend(lead for lead, _ in other_picks)
        message = f"Picked {len(other_picks)} of {len(other_leads)} result(s) to read"
        if fallback:
            message += " (screening failed; reading the top results)"
        runtime.emit("search", message, tool="researcher")
    for lead in org_leads:
        if not lead.reason:
            lead.reason = "Custom organization source"
        picks.append(lead)
    return {"picked": picks}


def read(runtime, state: DiscoveryState) -> DiscoveryState:
    picked = state.get("picked", [])
    if _stopped(state) or not picked:
        return {"verified": []}
    batch, stop, exhausted = [], "", False
    for lead in picked:
        try:
            runtime.budget.use_page()
        except BudgetExhausted as exc:
            runtime.warn("read", str(exc))
            stop, exhausted = (str(exc), False) if exc.hard else ("", True)
            break
        batch.append(lead)
        runtime.emit(
            "read",
            f"Reading {_site(lead)}: {lead.hit.title[:150]}",
            tool="fetch",
            source=_site(lead),
            url=lead.hit.url,
        )

    def work(lead):
        source = runtime.sources.get(lead.source)
        if source is not None and hasattr(source, "verified_listing"):
            # A whole posting from its own system (career pages): its facts are read in
            # code, not by the verifier model.
            return VerifiedOpportunity.model_validate(source.verified_listing(lead.hit))
        page = source.read(lead.hit) if source else None
        text = page.text if page else lead.hit.content
        if len(text or "") < 200:  # a snippet is not enough to verify anything
            text = runtime.fetcher.fetch(lead.hit.url).text
        return verify_listing(runtime, url=lead.hit.url, source=_site(lead), page=text)

    verified = []
    try:
        for lead, result, error in _parallel(runtime, batch, work):
            if error is not None:
                _read_failed(runtime, lead, error)
                continue
            lead.verified = result
            skip, rejection = _rejection(runtime.request, lead)
            if rejection:
                runtime.emit(
                    "read",
                    f"Skipped “{_title(lead)}”: {rejection}",
                    source=_site(lead),
                    data={"skip": skip},
                )
                continue
            verified.append(lead)
    except BudgetExhausted as exc:
        stop = stop or str(exc)
        runtime.warn("read", str(exc))
    runtime.emit("read", f"{len(verified)} of {len(batch)} listing(s) are open and relevant")
    _progress(runtime, state)
    return {
        "verified": verified,
        "stop": stop,
        "exhausted": state.get("exhausted", False) or exhausted,
    }


def _read_failed(runtime, lead, error):
    if isinstance(error, (FetchError, SearchError)):
        reason = str(error)
    elif isinstance(error, StructuredOutputError):
        logger.warning("Verifier output invalid for %s: %s", lead.hit.url, error)
        reason = "the listing could not be understood"
    else:
        logger.warning("Reading %s failed", lead.hit.url, exc_info=error)
        reason = "an unexpected error"
    runtime.warn(
        "read",
        f"Could not read {_site(lead)}: {reason}",
        partial=False,
        source=lead.domain,
        url=lead.hit.url,
        data={"skip": SkipReason.UNREADABLE},
    )


def _title(lead):
    return (lead.verified.title if lead.verified and lead.verified.title else lead.hit.title)[:150]


def _deadline(lead):
    """The verifier's deadline, else the one a structured source gave."""
    for value in (lead.verified.deadline, lead.hit.extra.get("deadline", "")):
        try:
            return date.fromisoformat((value or "")[:10])
        except ValueError:
            continue
    return None


_FOUR_DIGIT_YEAR = re.compile(r"\b(19\d\d|20\d\d)\b")


def _past_year_in_listing(verified, hit) -> int | None:
    """If the title or URL mentions years in the past and none in the present or future,
    returns the latest past year, else None."""
    current_year = date.today().year
    texts = [
        getattr(verified, "title", "") or "",
        getattr(hit, "title", "") or "",
        getattr(hit, "url", "") or "",
    ]
    years = [int(m.group(1)) for text in texts for m in _FOUR_DIGIT_YEAR.finditer(text)]
    if not years:
        return None
    if any(y >= current_year for y in years):
        return None
    past_years = [y for y in years if 2000 <= y < current_year]
    return max(past_years) if past_years else None


def _rejection(request, lead) -> tuple[str, str]:
    """Code checks after reading: why a listing is dropped (a SkipReason and a message),
    or ("", "") to keep it."""
    verified = lead.verified
    if not verified.is_opportunity:
        return SkipReason.NOT_AN_OPPORTUNITY, (
            verified.rejection_reason or "not a single opportunity you can apply to"
        )
    deadline = _deadline(lead)
    if not verified.is_open or (deadline and deadline < date.today()):
        return SkipReason.CLOSED, "it is closed" + (
            f" (deadline {deadline.isoformat()})" if deadline else ""
        )
    if deadline is None:
        past_year = _past_year_in_listing(verified, lead.hit)
        if past_year:
            return SkipReason.CLOSED, (
                f"it is closed (past year {past_year} in the title or link with no stated deadline)"
            )
    if request.opportunity_types and verified.opportunity_type not in request.opportunity_types:
        kind = verified.opportunity_type.replace("_", " ")
        return SkipReason.WRONG_TYPE, f"it is a {kind}, not a type you asked for"
    funder = normalize_text(verified.funder or lead.hit.extra.get("funder", ""))
    for excluded in request.profile.get("excluded_funders") or []:
        if excluded.strip() and normalize_text(excluded) in funder:
            return SkipReason.EXCLUDED, f"{verified.funder} is on your excluded funders"
    text = normalize_text(f"{verified.title} {verified.summary}")
    for keyword in request.profile.get("excluded_keywords") or []:
        if keyword.strip() and f" {normalize_text(keyword)} " in f" {text} ":
            return SkipReason.EXCLUDED, f"it mentions “{keyword}”, which you excluded"
    return "", ""


def judge(runtime, state: DiscoveryState) -> DiscoveryState:
    verified = state.get("verified", [])
    if _stopped(state) or not verified:
        return {"scored": []}
    runtime.emit(
        "match", f"Scoring {len(verified)} opportunity(ies) against your profile", tool="judge"
    )
    scored, stop = [], ""
    # Decision memory, looked up here: the scoring threads never touch the database.
    memory = {id(lead): _memory(runtime, lead) for lead in verified}
    remembered = sum(1 for decisions in memory.values() if decisions)
    if remembered:
        runtime.emit(
            "match",
            f"Comparing with your past decisions on similar opportunities "
            f"({remembered} of {len(verified)} have some)",
            tool="decision_memory",
        )

    evidence = _evidence(runtime, verified)
    role = judge_role(runtime.request.goal)

    def work(lead):
        verdict = judge_fit(
            runtime, _facts(lead), memory[id(lead)], evidence.get(id(lead), ""), role=role
        )
        return _agpo_cap(runtime, lead, verdict)

    try:
        for lead, verdict, error in _parallel(runtime, verified, work):
            if error is not None:
                runtime.warn("match", f"Could not score “{_title(lead)}”", partial=False)
                continue
            runtime.emit(
                "match",
                f"{_title(lead)}: {verdict.score}/100",
                source=_site(lead),
                data={"score": verdict.score, "concerns": verdict.concerns},
            )
            scored.append(_candidate(lead, verdict))
    except BudgetExhausted as exc:
        stop = str(exc)
        runtime.warn("match", stop)
    return {"scored": scored, "stop": stop}


def _evidence(runtime, verified) -> dict:
    """Passages from the company's documents for each lead, within the run's knowledge
    scope. Looked up here, before the scoring threads, which never touch the database."""
    if "company_evidence" not in runtime.prompt_for(judge_role(runtime.request.goal)).variables:
        return {}
    scope = KnowledgeScope.from_config(runtime.request.knowledge)
    if scope.reads_nothing:
        runtime.emit("match", "Not using company documents for this search", tool="knowledge")
        return {id(lead): NO_DOCUMENTS for lead in verified}
    evidence = {}
    for lead in verified:
        try:
            evidence[id(lead)] = company_evidence(
                runtime.request.organization_id, _facts(lead), scope
            )
        except Exception:  # evidence improves scores; it must never stop a run
            logger.warning("Company evidence lookup failed", exc_info=True)
    found = sum(1 for text in evidence.values() if text.startswith("<passage"))
    runtime.emit(
        "match",
        f"Checked {len(verified)} opportunity(ies) against your documents "
        f"({found} with matching passages)",
        tool="knowledge",
        data={"scope": scope.to_config()},
    )
    return evidence


def _memory(runtime, lead) -> list[dict]:
    if "past_decisions" not in runtime.prompt_for(judge_role(runtime.request.goal)).variables:
        return []
    try:
        return past_decisions(
            runtime.request.organization_id,
            _facts(lead),
            limit=runtime.settings.decision_memory_size,
        )
    except Exception:  # memory improves scores; it must never stop a run
        logger.warning("Decision memory lookup failed", exc_info=True)
        return []


def _facts(lead) -> dict:
    verified = lead.verified
    facts = verified.model_dump(exclude={"is_opportunity", "is_open", "rejection_reason"})
    deadline = _deadline(lead)
    facts["deadline"] = deadline.isoformat() if deadline else ""
    facts["source"] = _site(lead)
    tender = lead.hit.extra.get("tender") or {}
    if tender:
        # The portal's own facts; documents and the buyer's contacts are for people.
        facts["tender"] = {
            key: value
            for key, value in tender.items()
            if key not in ("documents", "buyer_contact", "source")
        }
    job = lead.hit.extra.get("job") or {}
    if job:
        # The posting's facts; links are for people.
        facts["job"] = {k: v for k, v in job.items() if k not in ("url", "apply_url", "system")}
        facts["description"] = lead.hit.content[:4000]
    if deadline:
        facts["days_to_close"] = (deadline - timezone.localdate()).days
    return {key: value for key, value in facts.items() if value not in ("", None, [])}


# AGPO groups as tenders.go.ke names them → the tenders brief's eligibility labels.
AGPO_LABELS = {
    "youth": TenderEligibility.AGPO_YOUTH.label,
    "women": TenderEligibility.AGPO_WOMEN.label,
    "disab": TenderEligibility.AGPO_PWD.label,
    "pwd": TenderEligibility.AGPO_PWD.label,
}
AGPO_CAP = 20


def _agpo_cap(runtime, lead, verdict):
    """A tender reserved for AGPO groups the organization isn't in can't be won, however
    the model scored it: capped, with the reason as a concern. Decided in code."""
    groups = (lead.hit.extra.get("tender") or {}).get("reserved_for") or []
    if not groups or verdict.score <= AGPO_CAP:
        return verdict
    labels = set((runtime.request.profile.get("tender_brief") or {}).get("eligibility") or [])
    needed = {
        label for group in groups for key, label in AGPO_LABELS.items() if key in group.lower()
    }
    if not needed or needed & labels:
        return verdict
    concern = (
        f"Reserved for AGPO {', '.join(groups).lower()} firms; your tenders brief doesn't "
        "say you qualify."
    )
    return verdict.model_copy(update={"score": AGPO_CAP, "concerns": [concern, *verdict.concerns]})


def _candidate(lead, verdict) -> Candidate:
    verified = lead.verified
    deadline = _deadline(lead)
    amounts = [verified.amount_min, verified.amount_max]
    low, high = (Decimal(str(a)) if a is not None else None for a in amounts)
    if low is not None and high is not None and low > high:
        low, high = high, low
    return Candidate(
        title=(verified.title or lead.hit.title)[:300],
        funder=(verified.funder or lead.hit.extra.get("funder", ""))[:200],
        summary=verified.summary,
        opportunity_type=verified.opportunity_type,
        source_name=_site(lead),
        source_url=lead.hit.url,
        eligibility=verified.eligibility,
        amount_min=low,
        amount_max=high,
        currency=verified.currency.upper()[:3] if (low or high) else "",
        deadline=_end_of_day(deadline),
        location=(verified.location or lead.hit.extra.get("location", ""))[:200],
        match_score=verdict.score,
        match_reasons=verdict.reasons[:4],
        requirements={"items": verified.requirements[:20], "concerns": verdict.concerns[:6]},
        verification={
            "source": lead.source,
            "picked_because": lead.reason,
            "checked_on": date.today().isoformat(),
        },
        tender=lead.hit.extra.get("tender") or {},
        job=lead.hit.extra.get("job") or {},
    )


def _site(lead):
    """Where a listing lives: the source for structured APIs, the site for web results."""
    if lead.source in STRUCTURED_SOURCES:
        return lead.domain
    return urlsplit(lead.hit.url).netloc.removeprefix("www.")[:120]


def _end_of_day(day):
    return None if day is None else timezone.make_aware(datetime.combine(day, time(23, 59)))


def save(runtime, state: DiscoveryState) -> DiscoveryState:
    scored = state.get("scored", [])
    decisions = {"added": 0, "duplicate": 0, "below_threshold": 0}
    for candidate in scored:
        decisions[runtime.sink.candidate(candidate)] += 1
    if scored:
        runtime.emit(
            "save",
            f"Added {decisions['added']} new; {decisions['duplicate']} already in your feed; "
            f"{decisions['below_threshold']} below the {runtime.request.match_threshold} "
            "threshold",
            data=decisions,
        )
    return {"added": state.get("added", 0) + decisions["added"], "scored": []}


def review(runtime, state: DiscoveryState) -> DiscoveryState:
    """The supervisor's "enough?" check, as code: go another round only if too few
    good matches were added and budget and rounds are left."""
    if _stopped(state):
        return {}
    if state.get("exhausted"):  # the warning was already recorded
        return {"stop": DONE}
    settings = runtime.settings
    if state["added"] >= settings.target_results:
        return {"stop": DONE}
    if state["round"] >= _max_rounds(runtime) or runtime.budget.searches_left() == 0:
        return {"stop": DONE}
    runtime.emit(
        "search",
        f"{state['added']} good match(es) so far; searching again from new angles",
        tool="supervisor",
    )
    return {"round": state["round"] + 1}


def next_after_review(state: DiscoveryState) -> str:
    return "end" if state.get("stop") else "plan"
