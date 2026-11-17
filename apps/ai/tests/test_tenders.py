"""Tenders: Kenya's procurement portal as a source, and the tender judge in a run.
No network: the portal is answered by the fake web; the agents are scripted."""

import json
import re
from datetime import timedelta

import httpx
import pytest
from django.utils import timezone

from apps.ai.events import ListEmitter
from apps.ai.graphs.discovery.runner import run_discovery_graph
from apps.ai.tools.search.base import SearchError
from apps.ai.tools.sources.ppip import PPIPSource

from .test_discovery_graph import Sink, discovery_settings, request, scripted  # noqa: F401

# discovery_settings (imported above) sets one round per run for the graph tests.

HOST = "tenders.go.ke"


def when(days: float) -> str:
    return (timezone.localtime() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def tender(
    id, title, *, closes=10, reserved=(), terminated=0, pe="Kwale County Government", text=None
):
    return {
        "id": id,
        "title": title,
        "tender_ref": f"KCG/{id}/2026",
        "published": 1,
        "terminated": terminated,
        "published_at": when(-3),
        "close_at": when(closes),
        "tender_fee": 1000,
        "bid_security_value": 50000 if id % 2 else None,
        "is_reservation": 1 if reserved else 0,
        "agpo_groups": [{"name": group} for group in reserved],
        "description": text,
        "venue": "County HQ",
        # The official who posted it: never to be read or stored.
        "created_by": {"name": "Jane Officer", "id_number": "21702465", "phone": "+254 700"},
        "pe": {
            "name": pe,
            "email": "procurement@kwale.go.ke",
            "telephone": "0700000000",
            "org_url": "https://kwale.go.ke",
            "city": "Kwale",
        },
        "procurement_category": {"title": "Consultancy Services"},
        "procurement_method": {"title": "Open Tender"},
        "submission_methods": [{"title": "Online"}],
        "documents": [
            {"description": "1794218923037-TENDER DOCUMENT", "url": "/storage/Documents/t.pdf"}
        ],
        "addenda": [],
    }


PAGE_1 = [
    tender(
        1,
        "PROPOSED DEVELOPMENT OF PROJECTS MONITORING SYSTEM (INVESTMENT DASHBOARD)",
        text="<p>Develop and host the <b>dashboard</b>.</p>",
    ),
    tender(2, "Renovation of classrooms at Ombasa primary school"),
    tender(3, "Development of a mobile application for revenue", reserved=["Youth"]),
]
PAGE_2 = [
    tender(4, "Supply of software licences", closes=-1),  # closed
    tender(5, "Website redesign and hosting", terminated=1),  # terminated
    tender(6, "Data management system for the health department", closes=2),
]


@pytest.fixture(autouse=True)
def fresh_portal(monkeypatch):
    """No list shared from another test, and no waiting between pages."""
    from django.core.cache import cache

    cache.delete("ai:ppip:open-tenders")
    monkeypatch.setattr("apps.ai.tools.sources.ppip.time.sleep", lambda seconds: None)
    yield
    cache.delete("ai:ppip:open-tenders")


@pytest.fixture
def portal(web):
    seen = []

    def handler(request):
        seen.append(request)
        page = int(request.url.params.get("page", 1))
        data = {1: PAGE_1, 2: PAGE_2}[page]
        return httpx.Response(200, json={"data": data, "last_page": 2, "current_page": page})

    web.route(HOST, "/api/active-tenders", handler)
    return seen


# ---------------------------------------------------------------------------
# The source
# ---------------------------------------------------------------------------


def test_open_tenders_are_matched_in_code(portal):
    source = PPIPSource()
    hits = source.search("software system development", max_results=5)
    titles = [hit.title for hit in hits]
    # Closed and terminated tenders are gone; most words matched first, then the soonest
    # to close (the data system closes in 2 days, the mobile app in 10).
    assert titles == [
        "Proposed development of projects monitoring system (investment dashboard)",
        "Data management system for the health department",
        "Development of a mobile application for revenue",
    ]
    assert source.search("classrooms", max_results=5)[0].title.startswith("Renovation")
    assert source.search("the tender for supply", max_results=5) == []  # only stopwords
    assert len(portal) == 2  # both pages, fetched once for every search of the run


def test_a_hit_carries_the_tender_facts_but_never_the_official(portal):
    hit = PPIPSource().search("dashboard", max_results=1)[0]
    assert hit.url == "https://tenders.go.ke/tenders/1"
    details = hit.extra["tender"]
    assert details["reference"] == "KCG/1/2026"
    assert details["buyer"] == "Kwale County Government"
    assert details["bid_security"] == "50,000 KES"
    assert details["documents"] == [
        {"name": "Tender document", "url": "https://tenders.go.ke/storage/Documents/t.pdf"}
    ]
    assert details["buyer_contact"]["email"] == "procurement@kwale.go.ke"
    assert hit.extra["funder"] == "Kwale County Government"
    assert "Closing date:" in hit.content and "Develop and host the dashboard." in hit.content
    everything = json.dumps({"content": hit.content, "extra": hit.extra})
    for private in ("Jane Officer", "21702465"):
        assert private not in everything


def test_a_reserved_tender_says_for_whom(portal):
    hit = PPIPSource().search("mobile application", max_results=1)[0]
    assert hit.extra["tender"]["reserved_for"] == ["Youth"]
    assert "Reserved for (AGPO): Youth" in hit.content


def test_a_portal_failure_after_the_first_page_keeps_what_was_read(web):
    pages = iter([httpx.Response(200, json={"data": PAGE_1, "last_page": 3})])

    def handler(request):
        return next(pages, httpx.Response(502))

    web.route(HOST, "/api/active-tenders", handler)
    assert len(PPIPSource().search("system", max_results=5)) == 1


def test_a_portal_that_is_down_is_a_search_error(web):
    web.route(HOST, "/api/active-tenders", lambda request: httpx.Response(503))
    with pytest.raises(SearchError):
        PPIPSource().search("system", max_results=5)


# ---------------------------------------------------------------------------
# The tender judge in a run
# ---------------------------------------------------------------------------


def tender_verifier(prompt):
    title = re.search(r"Title: (.+)", prompt).group(1).strip()
    return {
        "is_opportunity": True,
        "is_open": True,
        "opportunity_type": "tender",
        "title": title,
        "funder": "Kwale County Government",
        "summary": "Build a system.",
        "location": "Kwale, Kenya",
    }


def tender_judge(prompt):
    return {"score": 80, "reasons": ["Software you build"], "concerns": ["Site visit"]}


def tender_request(**overrides):
    data = {
        "goal": "tenders",
        "opportunity_types": ["tender", "rfp", "consultancy"],
        "source_categories": ["procurement"],
        "keywords": ["software"],
        "profile": {
            "country": "KE",
            "tender_brief": {"categories": ["Software, systems & apps"], "eligibility": []},
        },
    }
    return request(**(data | overrides))


@pytest.mark.usefixtures("discovery_settings")
def test_tender_runs_use_the_tender_judge_and_cap_agpo_reservations(monkeypatch, settings, portal):
    plan = [{"source": "kenya_ppip", "query": "monitoring system mobile application"}]
    models = scripted(
        monkeypatch,
        settings,
        plan,
        verifier=tender_verifier,
        tender_judge=tender_judge,
    )
    emitter, sink = ListEmitter(), Sink()
    outcome = run_discovery_graph(tender_request(), emitter, sink)

    assert outcome.prompt_versions["tender_judge"] == "tender_judge/v1"
    assert "judge" not in outcome.prompt_versions
    saved = {c.title: c for c in sink.saved}
    dashboard = saved["Proposed development of projects monitoring system (investment dashboard)"]
    assert dashboard.match_score == 80
    assert dashboard.tender["reference"] == "KCG/1/2026"
    assert dashboard.source_name == "tenders.go.ke"
    # Reserved for youth-owned firms; the brief doesn't say this one is: capped, said why.
    assert "Development of a mobile application for revenue" not in saved
    capped = [
        e for e in emitter.events if "mobile application" in e.message and "/100" in e.message
    ]
    assert capped and capped[0].data["score"] == 20
    assert capped[0].data["concerns"][0].startswith("Reserved for AGPO youth firms")

    prompt = next(p for p in models["tender_judge"].calls if "investment dashboard" in p.lower())
    assert '"days_to_close"' in prompt
    assert '"reference": "KCG/1/2026"' in prompt
    assert "procurement@kwale.go.ke" not in prompt  # contacts are for people, not the judge


@pytest.mark.usefixtures("discovery_settings")
def test_a_brief_that_qualifies_for_the_reservation_is_not_capped(monkeypatch, settings, portal):
    plan = [{"source": "kenya_ppip", "query": "mobile application"}]
    scripted(monkeypatch, settings, plan, verifier=tender_verifier, tender_judge=tender_judge)
    profile = {
        "country": "KE",
        "tender_brief": {"eligibility": ["AGPO: youth-owned"]},
    }
    sink = Sink()
    run_discovery_graph(tender_request(profile=profile), ListEmitter(), sink)
    assert [c.match_score for c in sink.saved] == [80]


def test_runs_share_one_read_of_the_portal(portal):
    PPIPSource().search("dashboard", max_results=1)
    PPIPSource().search("dashboard", max_results=1)  # a later run
    assert len(portal) == 2  # two pages, read once


def test_a_rate_limit_is_waited_out_once(web):
    replies = iter(
        [
            httpx.Response(429, headers={"retry-after": "2"}),
            httpx.Response(200, json={"data": PAGE_1, "last_page": 1}),
        ]
    )
    web.route(HOST, "/api/active-tenders", lambda request: next(replies))
    assert PPIPSource().search("dashboard", max_results=1)


def test_a_failed_read_is_not_repeated_for_every_search(web):
    web.route(HOST, "/api/active-tenders", lambda request: httpx.Response(503))
    source = PPIPSource()
    for _ in range(3):
        with pytest.raises(SearchError):
            source.search("system", max_results=5)
    assert web.count(HOST) == 1
