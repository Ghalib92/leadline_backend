"""Runs with the real discovery graph, through the API, worker and database.

Models are scripted and the web is fake; nothing leaves the machine.
"""

import httpx
import pytest
from django.urls import reverse

from apps.ai.testing import use_scripted_models
from apps.ai.tests.test_discovery_graph import LONG, PLAN, judge, pick_all, verifier
from apps.discovery.models import DiscoveryRun
from apps.opportunities.models import Opportunity

from .factories import make_agent, search_settings

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def graph_runner(settings, monkeypatch, web):
    settings.DISCOVERY_RUNNER = "graph"
    use_scripted_models(
        monkeypatch,
        settings,
        {
            "supervisor": lambda prompt: {"searches": PLAN[:2]},
            "researcher": pick_all,
            "verifier": verifier,
            "judge": judge,
        },
    )
    web.route(
        "api.tavily.com",
        handler=lambda r: httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Youth Data Fund",
                        "url": "https://fund.org/youth-data",
                        "raw_content": LONG,
                    },
                ]
            },
        ),
    )
    web.route(
        "api.grants.gov",
        "/v1/api/search2",
        lambda r: httpx.Response(200, json={"errorcode": 0, "data": {"oppHits": []}}),
    )
    web.route(
        "api.tech.ec.europa.eu",
        "/search-api/prod/rest/search",
        lambda r: httpx.Response(200, json={"results": []}),
    )
    web.route(
        "api.ted.europa.eu",
        "/v3/notices/search",
        lambda r: httpx.Response(200, json={"notices": []}),
    )
    return web


def run_now(client, organization, agent):
    path = reverse(
        "v1:discovery:agent-run",
        kwargs={"organization_id": organization.pk, "agent_id": agent.pk},
    )
    return client.post(path)


def test_a_graph_run_fills_the_feed_and_records_versions(
    auth_client, user, organization, graph_runner
):
    search_settings(organization, sources=["grants"], opportunity_types=["grant"])
    agent = make_agent(organization, user, sources=["grants"], opportunity_types=["grant"])
    response = run_now(auth_client, organization, agent)
    assert response.status_code == 202, response.data

    run = DiscoveryRun.objects.get(pk=response.data["id"])
    assert run.status == "succeeded", run.error
    assert run.runner == "graph"
    assert run.prompt_versions["judge"] == "judge/v3"
    assert run.models_used["judge"] == "test-answer"
    assert run.found == 1 and run.pages_read == 1 and run.usage["tokens"] > 0
    opportunity = Opportunity.objects.get(organization=organization)
    assert (opportunity.title, opportunity.source_name, opportunity.match_score) == (
        "Youth Data Fund 2027",
        "fund.org",
        82,
    )
    assert opportunity.run_id == run.pk and opportunity.agent_id == agent.pk
    assert run.candidates.get().decision == "added"
    tools = set(run.events.values_list("tool", flat=True))
    assert {"supervisor", "search", "researcher", "fetch", "judge"} <= tools

    # A second run skips what is already in the feed instead of reading it again.
    second = run_now(auth_client, organization, agent)
    run = DiscoveryRun.objects.get(pk=second.data["id"])
    assert run.status == "succeeded" and run.pages_read == 0
    assert run.events.filter(message="Skipped 1 result(s) already in your feed").exists()
    assert Opportunity.objects.filter(organization=organization).count() == 1


def test_missing_model_configuration_fails_the_run_clearly(
    auth_client, user, organization, graph_runner, settings
):
    settings.AI_LLM_PROVIDER = "groq"
    settings.GROQ_API_KEY = ""
    search_settings(organization, sources=["grants"])
    agent = make_agent(organization, user, sources=["grants"])
    run = DiscoveryRun.objects.get(pk=run_now(auth_client, organization, agent).data["id"])
    assert run.status == "failed"
    assert run.error == "Discovery is not configured on the server yet."
