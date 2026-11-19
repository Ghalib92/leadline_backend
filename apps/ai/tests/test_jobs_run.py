"""A jobs run: career pages read in code, each job scored against the CV by the job judge."""

import pytest
from django.core.cache import cache

from apps.ai.events import ListEmitter
from apps.ai.graphs.discovery.runner import run_discovery_graph

from .test_careers import KENYA, boards  # noqa: F401
from .test_discovery_graph import Sink, discovery_settings, request, scripted  # noqa: F401


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def our_boards(monkeypatch, discovery_settings):  # noqa: F811 — after its copy
    """Only the fake web's boards (the shipped list names real companies)."""
    config = discovery_settings()  # the copy the runner reads
    spec = config.sources["career_pages"]
    monkeypatch.setitem(spec.options, "boards", ["greenhouse:moniepoint", "ashby:andela"])
    monkeypatch.setitem(spec.options, "workable_pause_seconds", 0)


def job_judge(prompt):
    if "Sales Manager" in prompt:
        return {"score": 10, "reasons": ["A sales role"], "concerns": ["Not a developer role"]}
    return {
        "score": 78,
        "reasons": ["Asks for Python and Django; the CV shows 4 years of both"],
        "concerns": ["Asks for Kafka, which the CV doesn't show"],
    }


def jobs_request(**overrides):
    data = {
        "goal": "jobs",
        "agent_name": "Python jobs",
        "opportunity_types": ["job"],
        "source_categories": ["jobs"],
        "keywords": ["backend developer"],
        "job_search": {**KENYA, "companies": ["https://jobs.lever.co/acme"]},
        "profile": {
            "country": "KE",
            "job_brief": {"target_roles": ["Backend developer"], "skills": ["Python"]},
        },
    }
    return request(**(data | overrides))


@pytest.mark.usefixtures("discovery_settings", "our_boards", "boards")
def test_a_jobs_run_reads_career_pages_and_scores_fit_against_the_cv(monkeypatch, settings):
    plan = [{"source": "career_pages", "query": "python engineer developer"}]
    models = scripted(monkeypatch, settings, plan, job_judge=job_judge)
    emitter, sink = ListEmitter(), Sink()
    outcome = run_discovery_graph(jobs_request(), emitter, sink)

    assert outcome.prompt_versions["job_judge"] == "job_judge/v1"
    assert "verifier" not in models  # postings are read in code, not by a model
    saved = {candidate.title: candidate for candidate in sink.saved}
    assert "Python Developer" in saved  # the company the candidate follows (Lever)
    assert "Backend Engineer" not in saved  # remote for Poland only
    job = saved["Python Developer"]
    assert job.opportunity_type == "job"
    assert job.funder == "Acme"
    assert job.match_score == 78
    assert job.requirements["concerns"] == ["Asks for Kafka, which the CV doesn't show"]
    assert job.job["apply_url"] == "https://jobs.lever.co/acme/a1/apply"
    assert job.job["salary"] == "3,000 – 4,500 USD"

    prompt = next(p for p in models["job_judge"].calls if "Python Developer" in p)
    assert '"workplace": "remote"' in prompt
    assert "apply_url" not in prompt  # links are for people
    assert "Write Python services." in prompt  # the posting itself
    assert 'address them as "you"' in prompt  # never a guessed he or she
    messages = [event.message for event in emitter.events]
    assert any(m.startswith("Reading all") and "official portals" in m for m in messages)


LIST_PAGE = """<html><body><article><h1>Backend developer jobs in Kenya</h1>
<p>Latest backend developer jobs in Nairobi and across Kenya, updated daily.</p>
<a href="/listings/backend-developer-pesalink-abc">Backend Developer at PesaLink</a>
<a href="/listings/django-developer-soko-def">Django Developer at Soko</a>
</article></body></html>"""
ADVERT = """<html><body><article><h1>Backend Developer at PesaLink</h1>
<p>PesaLink is hiring a backend developer in Nairobi. Python, Django and PostgreSQL;
3+ years. Apply by 30 November 2026.</p></article></body></html>"""


@pytest.mark.usefixtures("discovery_settings", "our_boards", "boards")
def test_job_board_list_pages_become_one_lead_per_advert(monkeypatch, settings, web):
    import httpx

    settings.TAVILY_API_KEY = "tvly-test"
    web.route(
        "api.tavily.com",
        "/search",
        lambda r: httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Backend developer jobs in Kenya",
                        "url": "https://www.brightermonday.co.ke/jobs/backend",
                        "content": "list",
                        "raw_content": "list",
                    }
                ]
            },
        ),
    )
    web.route("www.brightermonday.co.ke", "/robots.txt", lambda r: httpx.Response(404))

    def page(text):
        def respond(request):
            return httpx.Response(200, text=text, headers={"content-type": "text/html"})

        return respond

    web.route("www.brightermonday.co.ke", "/jobs/backend", page(LIST_PAGE))
    web.route("www.brightermonday.co.ke", "/listings/backend-developer-pesalink-abc", page(ADVERT))

    def reader(prompt):
        return {
            "calls": [
                {
                    "title": "Backend Developer at PesaLink",
                    "url": "/listings/backend-developer-pesalink-abc",
                    "summary": "Python and Django in Nairobi.",
                    "opportunity_type": "job",
                }
            ]
        }

    def verifier(prompt):
        return {
            "is_opportunity": True,
            "is_open": True,
            "opportunity_type": "job",
            "title": "Backend Developer",
            "funder": "PesaLink",
            "summary": "Python and Django in Nairobi.",
            "location": "Nairobi, Kenya",
        }

    plan = [{"source": "kenya_job_boards", "query": "backend developer Nairobi"}]
    models = scripted(
        monkeypatch, settings, plan, source_reader=reader, verifier=verifier, job_judge=job_judge
    )
    emitter, sink = ListEmitter(), Sink()
    run_discovery_graph(jobs_request(source_categories=["jobs"]), emitter, sink)

    by_title = {c.title: c for c in sink.saved}
    assert "Backend Developer" in by_title  # the advert, not the list page
    assert not any("jobs in Kenya" in title for title in by_title)
    saved = by_title["Backend Developer"]
    assert str(saved.source_url).endswith("/listings/backend-developer-pesalink-abc")
    assert "researcher" not in models or not models["researcher"].calls  # not asked
    messages = [event.message for event in emitter.events]
    assert "1 advert(s) found" in messages
    prompt = models["verifier"].calls[0]
    assert 'when "job" is among the types below, one job advert' in prompt
