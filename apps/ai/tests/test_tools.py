import json
from datetime import date, timedelta

import httpx
import pytest

from apps.ai.exceptions import AIConfigError
from apps.ai.tools import http
from apps.ai.tools.fetch.base import FetchError
from apps.ai.tools.fetch.http import HttpFetcher
from apps.ai.tools.ratelimit import MinuteRateLimiter, RateLimited
from apps.ai.tools.search.base import SearchError
from apps.ai.tools.search.tavily import TavilySearch
from apps.ai.tools.sources.base import html_to_text
from apps.ai.tools.sources.eu_funding import EuFundingSource
from apps.ai.tools.sources.grants_gov import GrantsGovSource
from apps.ai.tools.sources.registry import KNOWN_ADAPTERS, build_source
from apps.ai.tools.sources.ted_europa import TedEuropaSource
from apps.ai.tools.sources.world_bank import WorldBankSource

ARTICLE = (
    "<html><head><title>Youth Data Fund 2027</title></head><body><nav>Menu</nav>"
    "<article><h1>Youth Data Fund 2027</h1>"
    + "<p>The fund supports organizations training young people in data skills. </p>" * 8
    + "</article></body></html>"
)


@pytest.fixture(autouse=True)
def clear_cache():
    from django.core.cache import cache

    cache.clear()


def tavily_results(*results):
    return httpx.Response(200, json={"results": list(results)})


# ---------------------------------------------------------------------------
# Tavily
# ---------------------------------------------------------------------------


def test_tavily_sends_one_basic_search_with_page_text(web):
    sent = {}

    @web.route("api.tavily.com", "/search")
    def search(request):
        sent["body"] = json.loads(request.content)
        sent["auth"] = request.headers["authorization"]
        return tavily_results(
            {
                "title": "Youth Data Fund",
                "url": "https://fund.org/a",
                "content": "Snippet",
                "raw_content": "Full text",
                "score": 0.9,
            },
            {"title": "No url"},
        )

    hits = TavilySearch(api_key="k").search("youth data", max_results=50, options={})
    assert [(h.title, h.url, h.snippet, h.content) for h in hits] == [
        ("Youth Data Fund", "https://fund.org/a", "Snippet", "Full text")
    ]
    assert sent["auth"] == "Bearer k"
    assert sent["body"]["search_depth"] == "basic"
    assert sent["body"]["include_raw_content"] == "text"
    assert sent["body"]["max_results"] == 20  # capped
    assert web.count("api.tavily.com") == 1


def test_tavily_news_options(web):
    sent = {}

    @web.route("api.tavily.com")
    def search(request):
        sent.update(json.loads(request.content))
        return tavily_results()

    options = {"topic": "news", "days": 45, "include_domains": ["devex.com"]}
    TavilySearch(api_key="k").search("q", max_results=5, options=options)
    assert (sent["topic"], sent["days"], sent["include_domains"]) == ("news", 45, ["devex.com"])


@pytest.mark.parametrize(
    ("status", "error"), [(429, SearchError), (500, SearchError), (401, AIConfigError)]
)
def test_tavily_errors(web, status, error):
    web.route("api.tavily.com", handler=lambda request: httpx.Response(status))
    with pytest.raises(error):
        TavilySearch(api_key="k").search("q", max_results=5, options={})


def test_tavily_needs_a_key(settings):
    settings.TAVILY_API_KEY = ""
    with pytest.raises(AIConfigError, match="TAVILY_API_KEY"):
        TavilySearch()


def test_tavily_respects_the_rate_limit(web):
    web.route("api.tavily.com", handler=lambda request: tavily_results())
    limiter = MinuteRateLimiter("tavily-test", 2, clock=lambda: 120.0)
    tavily = TavilySearch(api_key="k", limiter=limiter)
    for _ in range(2):
        tavily.search("q", max_results=5, options={})
    with pytest.raises(SearchError, match="2 requests per minute"):
        tavily.search("q", max_results=5, options={"max_wait": 0})
    assert web.count("api.tavily.com") == 2  # the third never left


def test_rate_limiter_waits_for_the_next_minute():
    now = [60.0]
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    limiter = MinuteRateLimiter("waiting", 1, clock=lambda: now[0], sleep=sleep)
    limiter.acquire()
    limiter.acquire(max_wait=120)
    assert len(slept) == 1 and 59 < slept[0] <= 61
    with pytest.raises(RateLimited):
        limiter.acquire(max_wait=1)


# ---------------------------------------------------------------------------
# Structured sources
# ---------------------------------------------------------------------------


def test_grants_gov_search_and_detail(web):
    @web.route("api.grants.gov", "/v1/api/search2")
    def search(request):
        assert json.loads(request.content)["oppStatuses"] == "posted|forecasted"
        return httpx.Response(
            200,
            json={
                "errorcode": 0,
                "data": {
                    "oppHits": [
                        {
                            "id": "362787",
                            "title": "Youth Corps&nbsp;&nbsp;",
                            "agency": "BLM",
                            "closeDate": "10/16/2026",
                            "oppStatus": "posted",
                        },
                        {"id": "", "title": "broken"},
                    ]
                },
            },
        )

    @web.route("api.grants.gov", "/v1/api/fetchOpportunity")
    def detail(request):
        assert json.loads(request.content) == {"opportunityId": 362787}
        return httpx.Response(
            200,
            json={
                "errorcode": 0,
                "data": {
                    "opportunityTitle": "Youth Corps",
                    "opportunityNumber": "L26",
                    "synopsis": {
                        "agencyName": "BLM",
                        "responseDate": "Oct 16, 2026",
                        "awardCeiling": "750000",
                        "synopsisDesc": "<p>Funds youth crews.</p>",
                        "applicantTypes": [{"description": "Nonprofits"}],
                    },
                },
            },
        )

    source = GrantsGovSource()
    [hit] = source.search("youth", max_results=10)
    assert hit.title == "Youth Corps"
    assert hit.url == "https://www.grants.gov/search-results-detail/362787"
    assert hit.extra == {
        "grants_gov_id": "362787",
        "funder": "BLM",
        "deadline": "2026-10-16",
        "status": "posted",
    }
    page = source.read(hit)
    assert "Award ceiling (USD): 750000" in page.text
    assert "Funds youth crews." in page.text and "<p>" not in page.text
    assert "Eligible applicants: Nonprofits" in page.text


def test_grants_gov_api_errors(web):
    web.route(
        "api.grants.gov", handler=lambda r: httpx.Response(200, json={"errorcode": 1, "msg": "bad"})
    )
    with pytest.raises(SearchError, match="bad"):
        GrantsGovSource().search("x", max_results=5)


def test_world_bank_keeps_only_open_notices(web):
    future = (date.today() + timedelta(days=30)).isoformat()
    past = (date.today() - timedelta(days=1)).isoformat()

    def notice(id, notice_type, deadline, **extra):
        return {
            "id": id,
            "notice_type": notice_type,
            "notice_status": "Published",
            "submission_deadline_date": f"{deadline}T00:00:00Z" if deadline else None,
            "bid_description": f"Notice {id}",
            "project_ctry_name": "Kenya",
            "project_name": "Skills",
            "notice_text": "<p>Submit bids.</p>",
            **extra,
        }

    @web.route("search.worldbank.org", "/api/v2/procnotices")
    def search(request):
        assert request.url.params["qterm"] == "education"
        return httpx.Response(
            200,
            json={
                "procnotices": [
                    notice("A", "Invitation for Bids", future),
                    notice("B", "Contract Award", future),
                    notice("C", "Request for Expression of Interest", past),
                    notice("D", "Request for Expression of Interest", None),
                    notice("E", "Request for Expression of Interest", future),
                    notice(
                        "F",
                        "Request for Expression of Interest",
                        future,
                        procurement_method_name="Individual Consultant Selection",
                    ),
                ]
            },
        )

    options = {"exclude_methods": ["Individual Consultant Selection"]}
    hits = WorldBankSource(options=options).search("education", max_results=5)
    assert [hit.title for hit in hits] == ["Notice A", "Notice E"]
    assert len(WorldBankSource().search("education", max_results=5)) == 3  # F kept
    assert hits[0].extra["deadline"] == future
    assert "Submit bids." in WorldBankSource().read(hits[0]).text


def test_eu_funding_search_and_read(web):
    future = (date.today() + timedelta(days=45)).isoformat()
    past = (date.today() - timedelta(days=5)).isoformat()

    @web.route("api.tech.ec.europa.eu", "/search-api/prod/rest/search")
    def search(request):
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "identifier": "HORIZON-CL4-2026-DATA",
                        "title": "Digital and emerging technologies",
                        "deadlineDate": future,
                        "frameworkProgramme": "Horizon Europe",
                        "summary": "<p>Funding for data technologies.</p>",
                        "status": "open",
                    },
                    {
                        "identifier": "OLD-CALL-2025",
                        "title": "Old Closed Call",
                        "deadlineDate": past,
                        "frameworkProgramme": "Horizon Europe",
                    },
                    {"identifier": "", "title": "Missing id"},
                ]
            },
        )

    source = EuFundingSource()
    hits = source.search("data technologies", max_results=5)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.title == "Digital and emerging technologies"
    assert "HORIZON-CL4-2026-DATA" in hit.url
    assert hit.extra["funder"] == "European Commission"
    assert hit.extra["programme"] == "Horizon Europe"
    assert hit.extra["deadline"] == future
    page = source.read(hit)
    assert page is not None
    assert "Digital and emerging technologies" in page.text
    assert "Funding for data technologies." in page.text


def test_eu_funding_api_errors(web):
    web.route("api.tech.ec.europa.eu", handler=lambda r: httpx.Response(500))
    with pytest.raises(SearchError, match="ec.europa.eu"):
        EuFundingSource().search("data", max_results=5)


def test_ted_europa_search_and_read(web):
    future = (date.today() + timedelta(days=30)).isoformat()
    queries = []

    @web.route("api.ted.europa.eu", "/v3/notices/search")
    def search(request):
        body = json.loads(request.content)
        queries.append(body["query"])
        if "FT~kenya" in body["query"]:
            return httpx.Response(200, json={"notices": [], "totalNoticeCount": 0})
        return httpx.Response(
            200,
            json={
                "notices": [
                    {
                        "publication-number": "123456-2026",
                        "publication-date": "2026-11-01+01:00",
                        "notice-title": {"eng": "Cloud Infrastructure Services", "fra": "Cloud"},
                        "deadline-receipt-tender-date-lot": [f"{future}+01:00"],
                        "buyer-name": {"eng": ["European Commission DG DIGIT"]},
                        "buyer-country": ["BEL"],
                        "description-lot": {"eng": ["<p>Procurement of cloud infrastructure.</p>"]},
                        "estimated-value-lot": ["5000000"],
                        "estimated-value-cur-lot": ["EUR"],
                        "place-of-performance": ["BE100", "BEL"],
                    },
                    {"publication-number": "", "notice-title": {"eng": "No number"}},
                ]
            },
        )

    source = TedEuropaSource()
    hits = source.search('"Cloud" hosting services Kenya', max_results=5)
    # An expert query (plain text is a syntax error), open notices only; with nothing
    # found, the last word is dropped. Stopwords ("services") are never searched.
    assert queries == [
        "(FT~cloud AND FT~hosting AND FT~kenya) AND deadline-receipt-tender-date-lot>=today() "
        "SORT BY publication-date DESC",
        "(FT~cloud AND FT~hosting) AND deadline-receipt-tender-date-lot>=today() "
        "SORT BY publication-date DESC",
    ]
    assert len(hits) == 1
    hit = hits[0]
    assert hit.title == "Cloud Infrastructure Services"
    assert hit.url == "https://ted.europa.eu/en/notice/-/detail/123456-2026"
    assert hit.extra["funder"] == "European Commission DG DIGIT"
    assert hit.extra["deadline"] == future
    assert hit.extra["source"] == "TED"
    page = source.read(hit)
    assert "Cloud Infrastructure Services" in page.text
    assert "5000000 EUR" in page.text
    assert "Place of performance: BEL" in page.text
    assert "Procurement of cloud infrastructure." in page.text


def test_ted_europa_places_option_and_empty_queries(web):
    queries = []

    @web.route("api.ted.europa.eu", "/v3/notices/search")
    def search(request):
        queries.append(json.loads(request.content)["query"])
        return httpx.Response(200, json={"notices": []})

    source = TedEuropaSource(options={"places": ["KEN", "bad; DROP"]})
    assert source.search("software development portal design", max_results=5) == []
    assert "place-of-performance IN (KEN) " in queries[0]
    # Three tries at most, and never fewer than two words.
    assert len(queries) == 3
    assert "(FT~software AND FT~development)" in queries[-1]
    assert source.search("the tender of services", max_results=5) == []
    assert len(queries) == 3


def test_ted_europa_api_errors(web):
    web.route("api.ted.europa.eu", handler=lambda r: httpx.Response(500))
    with pytest.raises(SearchError, match="ted.europa.eu"):
        TedEuropaSource().search("cloud", max_results=5)


def test_registry_builds_eu_funding_and_ted_europa():
    from apps.ai.config.loader import SourceSpec

    assert {"eu_funding", "ted_europa"} <= KNOWN_ADAPTERS
    eu = build_source("eu_funding", SourceSpec(adapter="eu_funding", domain="ec.europa.eu"))
    assert isinstance(eu, EuFundingSource)
    ted = build_source("ted_europa", SourceSpec(adapter="ted_europa", domain="ted.europa.eu"))
    assert isinstance(ted, TedEuropaSource)


def test_html_to_text():
    assert html_to_text("<p>One<br/>two</p><ul><li>three</li></ul>") == "One\ntwo\nthree"
    assert html_to_text("") == ""


# ---------------------------------------------------------------------------
# Fetching pages
# ---------------------------------------------------------------------------


def test_fetch_extracts_the_main_text(web):
    web.route("fund.org", "/robots.txt", lambda r: httpx.Response(404))
    web.route(
        "fund.org",
        "/call",
        lambda r: httpx.Response(
            200, text=ARTICLE, headers={"content-type": "text/html; charset=utf-8"}
        ),
    )
    page = HttpFetcher().fetch("https://fund.org/call")
    assert page.title == "Youth Data Fund 2027"
    assert "training young people in data skills" in page.text
    assert "Menu" not in page.text


def test_fetch_honors_robots_txt(web):
    robots = "User-agent: *\nDisallow: /private/\n"
    web.route("fund.org", "/robots.txt", lambda r: httpx.Response(200, text=robots))
    web.route(
        "fund.org",
        "/open",
        lambda r: httpx.Response(200, text=ARTICLE, headers={"content-type": "text/html"}),
    )
    fetcher = HttpFetcher()
    with pytest.raises(FetchError, match="robots.txt"):
        fetcher.fetch("https://fund.org/private/call")
    assert fetcher.fetch("https://fund.org/open").text
    assert web.count("fund.org") == 2  # robots.txt read once, then the open page


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"])
def test_fetch_refuses_private_addresses(monkeypatch, web, address):
    monkeypatch.setattr(http, "resolve_host", lambda host: [address])
    with pytest.raises(FetchError, match="not a public address"):
        HttpFetcher().fetch("https://internal.example/admin")
    assert web.requests == []


def test_fetch_checks_every_redirect(monkeypatch, web):
    def resolve(host):
        return ["10.0.0.1"] if host == "internal.example" else ["93.184.216.34"]

    monkeypatch.setattr(http, "resolve_host", resolve)
    web.route("fund.org", "/robots.txt", lambda r: httpx.Response(404))
    web.route(
        "fund.org",
        "/go",
        lambda r: httpx.Response(302, headers={"location": "http://internal.example/secret"}),
    )
    with pytest.raises(FetchError, match="not a public address"):
        HttpFetcher().fetch("https://fund.org/go")


def test_fetch_rejects_non_pages_and_errors(web):
    web.route("fund.org", "/robots.txt", lambda r: httpx.Response(404))
    web.route(
        "fund.org",
        "/file.pdf",
        lambda r: httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}),
    )
    web.route("fund.org", "/gone", lambda r: httpx.Response(404))
    web.route(
        "fund.org",
        "/empty",
        lambda r: httpx.Response(
            200, text="<html><body></body></html>", headers={"content-type": "text/html"}
        ),
    )
    fetcher = HttpFetcher()
    with pytest.raises(FetchError, match="Not a web page"):
        fetcher.fetch("https://fund.org/file.pdf")
    with pytest.raises(FetchError, match="HTTP 404"):
        fetcher.fetch("https://fund.org/gone")
    with pytest.raises(FetchError, match="No readable text"):
        fetcher.fetch("https://fund.org/empty")
    with pytest.raises(FetchError, match="Only http"):
        fetcher.fetch("ftp://fund.org/file")


def test_fetch_caps_the_download(web):
    web.route("fund.org", "/robots.txt", lambda r: httpx.Response(404))
    web.route(
        "fund.org",
        "/huge",
        lambda r: httpx.Response(
            200, text=ARTICLE + "x" * 50_000, headers={"content-type": "text/html"}
        ),
    )
    page = HttpFetcher(max_bytes=len(ARTICLE)).fetch("https://fund.org/huge")
    assert "x" * 100 not in page.text
