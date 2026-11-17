"""The lead lookup inside a client search: websites found and checked, contacts read
from them, evidence saved on each lead. A stub web search; sites on the fake web."""

import httpx
import pytest

from apps.ai.graphs.clients.lookup import LookupTools
from apps.ai.testing import use_scripted_models
from apps.ai.tools.search.base import SearchError, SearchHit
from apps.ai.tools.websites.check import SiteChecker

from .test_clients_search import StubDirectory, biz, request, run, scores
from .test_websites import LOOKUP, MODERN_SITE, OLD_SITE, TODAY, serve


@pytest.fixture
def judge(monkeypatch, settings):
    return use_scripted_models(monkeypatch, settings, {"client_judge": scores})


class StubSearch:
    """Answers by the business name in the query; records every query."""

    def __init__(self, results=None, *, fail=False):
        self.results, self.fail = results or {}, fail
        self.queries = []

    def search(self, query, *, max_results, options):
        self.queries.append(query)
        if self.fail:
            raise SearchError("Tavily rate limit reached.")
        for name, hits in self.results.items():
            if name in query:
                return hits
        return []


def tools(search=None, *, pagespeed=False):
    return LookupTools(search=search, checker=SiteChecker(LOOKUP, today=TODAY), pagespeed=pagespeed)


def osm(*businesses):
    return StubDirectory("osm_places", "OpenStreetMap", list(businesses))


def by_title(sink):
    return {candidate.title: candidate for candidate in sink.saved}


KILIMA_HITS = [
    SearchHit(title="Kilima Guest House - Tripadvisor", url="https://www.tripadvisor.com/kilima"),
    SearchHit(title="Kilima Guest House", url="https://kilima-guesthouse.co.ke/"),
    SearchHit(
        title="Kilima (@kilimaguesthouse)", url="https://www.instagram.com/kilimaguesthouse/"
    ),
]


def test_websites_are_found_checked_and_turned_into_evidence(judge, web):
    serve(web, "kilima-guesthouse.co.ke", OLD_SITE)
    serve(web, "baobab.co.ke", "error", status=500)
    serve(web, "savannah.co.ke", MODERN_SITE)
    search = StubSearch({"Kilima Guest House": KILIMA_HITS})
    directory = osm(
        biz("Kilima Guest House"),
        biz("Baobab Inn", website="https://baobab.co.ke"),
        biz("Jacaranda Suites"),
        biz("Savannah Lodge", website="https://savannah.co.ke"),
    )
    outcome, emitter, sink = run(request(), [directory], tools=tools(search))

    assert outcome.status == "succeeded", outcome.warning
    # Only businesses with no website listed are searched for.
    assert sorted(search.queries) == ['"Jacaranda Suites" Nairobi', '"Kilima Guest House" Nairobi']
    leads = by_title(sink)

    kilima = leads["Kilima Guest House"]
    details = kilima.business
    assert str(kilima.source_url) == "https://kilima-guesthouse.co.ke/"  # its own site now
    assert details["website"] == "https://kilima-guesthouse.co.ke/"
    assert details["website_status"] == "found"
    assert details["phone"] == "0722 123 456"
    assert details["email"] == "info@kilima.co.ke"
    assert details["socials"] == {
        "instagram": "https://instagram.com/kilimaguesthouse",
        "whatsapp": "https://wa.me/254722123456",
    }
    assert details["found_on"] == {
        "website": "web search",
        "instagram": "web search",
        "phone": "website",
        "email": "website",
        "whatsapp": "website",
    }
    assert details["site"]["copyright_year"] == 2017
    assert {"not_mobile_friendly", "outdated_website"} <= set(details["signals"])
    assert {
        "signal": "outdated_website",
        "detail": "kilima-guesthouse.co.ke still shows © 2017.",
    } in details["evidence"]

    baobab = leads["Baobab Inn"].business
    assert baobab["website_status"] == "down"
    assert baobab["signals"] == ["broken_website"]
    assert baobab["evidence"] == [
        {
            "signal": "broken_website",
            "detail": "Its website baobab.co.ke is down: it returns an error (HTTP 500).",
        }
    ]

    jacaranda = leads["Jacaranda Suites"].business
    assert jacaranda["website_status"] == "not_found"
    assert jacaranda["signals"] == ["no_website"]
    assert jacaranda["evidence"][0]["detail"] == (
        "No website on OpenStreetMap, and none found in a web search."
    )

    savannah = leads["Savannah Lodge"].business
    assert savannah["site"]["booking"] is True and savannah["site"]["chat"] is True

    # The judge sees the evidence and website status, never the pages' text.
    prompt = judge["client_judge"].calls[0]
    assert "still shows © 2017" in prompt
    assert "none listed, and none found in a web search" in prompt
    assert "has a website, but it is down" in prompt
    assert "IGNORE PREVIOUS INSTRUCTIONS" not in prompt

    tools_used = {event.tool for event in emitter.events}
    assert {"lookup", "web_search", "website_check"} <= tools_used
    summary = [e for e in emitter.events if e.message.startswith("Lookup done")][0]
    assert summary.data == {"found": 1, "not_found": 1, "down": 1, "contacts": 2}


def test_the_contact_page_is_read_when_the_home_page_has_no_contacts(judge, web):
    home = '<html><body><a href="/contact-us">Contact us</a><p>Rooms.</p></body></html>'
    contact = '<html><body><a href="tel:+254711000111">Call</a></body></html>'
    web.route("kilima.co.ke", "/robots.txt", lambda r: httpx.Response(404))
    web.route(
        "kilima.co.ke",
        "/contact-us",
        lambda r: httpx.Response(200, text=contact, headers={"content-type": "text/html"}),
    )
    web.route(
        "kilima.co.ke",
        "",
        lambda r: httpx.Response(200, text=home, headers={"content-type": "text/html"}),
    )
    directory = osm(biz("Kilima Guest House", website="https://kilima.co.ke"))
    outcome, _, sink = run(request(), [directory], tools=tools())

    details = sink.saved[0].business
    assert details["phone"] == "+254711000111"
    assert details["found_on"] == {"phone": "website"}


def test_only_the_depth_s_number_of_leads_is_looked_up(judge, web):
    search = StubSearch()
    names = [f"Kilima Number{n} Guest House" for n in range(10)]
    directory = osm(*(biz(name) for name in names))
    outcome, _, sink = run(request(depth="quick"), [directory], tools=tools(search))

    assert outcome.status == "succeeded", outcome.warning
    assert len(search.queries) == LOOKUP.leads["quick"] == 8
    statuses = [lead.business["website_status"] for lead in sink.saved]
    assert statuses.count("not_found") == 8
    assert statuses.count("unknown") == 2


def test_running_out_of_pages_ends_lookups_as_partial(judge, web):
    serve(web, "kilima.co.ke", OLD_SITE)
    serve(web, "baobab.co.ke", OLD_SITE)
    budget = {"max_searches": 10, "max_pages": 1, "max_tokens": 100_000, "max_wall_seconds": 120}
    directory = osm(
        biz("Kilima Guest House", website="https://kilima.co.ke"),
        biz("Baobab Inn", website="https://baobab.co.ke"),
    )
    # One page: the first home page, then the budget is spent.
    outcome, emitter, sink = run(request(budget=budget), [directory], tools=tools())

    assert outcome.status == "partial"
    assert "Page limit reached (1 pages)" in outcome.warning
    checked = [lead for lead in sink.saved if "site" in lead.business]
    assert len(checked) == 1
    assert len(sink.saved) == 2  # the other still scored and saved, with its listing


def test_a_failed_web_search_leaves_the_business_unknown(judge, web):
    directory = osm(biz("Kilima Guest House"))
    outcome, emitter, sink = run(request(), [directory], tools=tools(StubSearch(fail=True)))

    assert outcome.status == "succeeded"  # a warning, not a partial result
    assert sink.saved[0].business["website_status"] == "unknown"
    assert any("Web search failed" in e.message for e in emitter.events)


def test_pagespeed_marks_a_slow_site(judge, web, settings):
    settings.PAGESPEED_API_KEY = "key"
    serve(web, "savannah.co.ke", MODERN_SITE)
    score = {"lighthouseResult": {"categories": {"performance": {"score": 0.31}}}}
    web.route("www.googleapis.com", "", lambda r: httpx.Response(200, json=score))
    directory = osm(biz("Savannah Lodge", website="https://savannah.co.ke"))
    outcome, emitter, sink = run(request(), [directory], tools=tools(pagespeed=True))

    details = sink.saved[0].business
    assert details["site"]["mobile_speed"] == 31
    assert "slow_website" in details["signals"]
    assert {
        "signal": "slow_website",
        "detail": "savannah.co.ke scores 31/100 for speed on phones (Google PageSpeed).",
    } in details["evidence"]


def test_pagespeed_quota_turns_speed_checks_off_once(judge, web, settings):
    settings.PAGESPEED_API_KEY = "key"
    serve(web, "savannah.co.ke", MODERN_SITE)
    serve(web, "kilima.co.ke", MODERN_SITE)
    web.route("www.googleapis.com", "", lambda r: httpx.Response(429, json={}))
    directory = osm(
        biz("Savannah Lodge", website="https://savannah.co.ke"),
        biz("Kilima Guest House", website="https://kilima.co.ke"),
    )
    outcome, emitter, sink = run(request(), [directory], tools=tools(pagespeed=True))

    assert outcome.status == "succeeded"
    warnings = [e for e in emitter.events if "quota" in e.message]
    assert len(warnings) == 1
    assert all(lead.business["site"].get("mobile_speed") is None for lead in sink.saved)


@pytest.mark.parametrize("depth", ["quick", "standard", "deep"])
def test_client_runs_get_the_lookups_in_their_budget(depth):
    from apps.ai.config.loader import load_sources_file

    config = load_sources_file()
    base = config.discovery.budget_for(depth)
    budget = config.clients.budget_for(depth, config.discovery)
    leads = config.clients.lookup.leads[depth]
    assert budget["max_searches"] == base["max_searches"] + leads
    assert budget["max_pages"] == base["max_pages"] + leads * config.clients.lookup.pages_per_lead
    assert budget["max_tokens"] == base["max_tokens"]


def test_hunter_finds_an_email_the_site_doesnt_show(judge, web, settings):
    settings.HUNTER_API_KEY = "hk"
    home = "<html><body><p>Rooms and suites in Westlands.</p></body></html>" * 20
    serve(web, "kilima.co.ke", home)
    serve(web, "baobab.co.ke", home)
    calls = []

    def hunter(request):
        calls.append(dict(request.url.params))
        emails = [
            {"value": "jane@x.co.ke", "first_name": "Jane", "confidence": 99},
            {"value": "info@x.co.ke", "confidence": 80},
        ]
        return httpx.Response(200, json={"data": {"emails": emails}})

    web.route("api.hunter.io", "/v2/domain-search", hunter)
    directory = osm(
        biz("Kilima Guest House", website="https://kilima.co.ke"),
        biz("Baobab Inn", website="https://baobab.co.ke"),
    )
    lookup_tools = tools()
    lookup_tools.hunter_left = 1  # one lookup left this run
    outcome, emitter, sink = run(request(), [directory], tools=lookup_tools)

    assert len(calls) == 1
    assert "type" not in calls[0]  # people and team inboxes
    with_email = [lead.business for lead in sink.saved if lead.business.get("email")]
    assert len(with_email) == 1
    assert with_email[0]["email"] == "info@x.co.ke"  # the team inbox first
    assert with_email[0]["found_on"] == {"email": "Hunter"}
