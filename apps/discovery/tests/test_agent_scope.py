"""An agent adds to the organization's search settings; it never replaces them."""

import pytest
from django.urls import reverse

from apps.ai.config.loader import load_sources_file
from apps.discovery import selectors, services
from apps.organizations.tests.factories import make_folder

from .factories import make_agent, ready_organization

pytestmark = pytest.mark.django_db


def settings_url(organization):
    return reverse("v1:discovery:run-settings", kwargs={"organization_id": organization.pk})


def agents_url(organization):
    return reverse("v1:discovery:agents", kwargs={"organization_id": organization.pk})


def agent_url(organization, agent_id):
    return reverse(
        "v1:discovery:agent-detail",
        kwargs={"organization_id": organization.pk, "agent_id": agent_id},
    )


def org_settings(auth_client, organization, **overrides):
    body = {
        "depth": "standard",
        "sources": ["web", "grants"],
        "opportunity_types": ["grant"],
        "folders": [],
        **overrides,
    }
    response = auth_client.put(settings_url(organization), body, format="json")
    assert response.status_code == 200, response.data


def config(organization, agent, overrides=None):
    return services._resolve_run_config(organization, agent, overrides)


def test_the_agent_adds_its_sources_types_and_keywords_to_the_orgs(auth_client, user, organization):
    org_settings(auth_client, organization)
    agent = make_agent(
        organization,
        user,
        sources=["news", "web"],
        opportunity_types=["fellowship"],
        keywords=["ai"],
    )
    resolved = config(organization, agent)
    assert resolved["sources"] == ["web", "grants", "news"]
    assert resolved["opportunity_types"] == ["grant", "fellowship"]
    assert resolved["keywords"] == [*selectors.search_keywords(organization), "ai"]


def test_an_agent_with_nothing_of_its_own_uses_the_org_settings(auth_client, user, organization):
    org_settings(auth_client, organization)
    agent = make_agent(organization, user, sources=[], opportunity_types=[], keywords=[])
    resolved = config(organization, agent)
    assert resolved["sources"] == ["web", "grants"]
    assert resolved["opportunity_types"] == ["grant"]


def test_the_agent_adds_folders_and_later_org_folders_reach_it(auth_client, user, organization):
    shared, own = make_folder(organization, "Shared"), make_folder(organization, "Own")
    org_settings(auth_client, organization, folders=[shared.pk])
    agent = make_agent(organization, user)
    agent.folders.set([own, shared])
    assert [f["name"] for f in config(organization, agent)["folders"]] == ["Shared", "Own"]

    later = make_folder(organization, "Later")
    org_settings(auth_client, organization, folders=[shared.pk, later.pk])
    assert [f["name"] for f in config(organization, agent)["folders"]] == [
        "Later",
        "Shared",
        "Own",
    ]


def test_the_agents_depth_wins_when_set(auth_client, user, organization):
    org_settings(auth_client, organization, depth="quick")
    inherits = make_agent(organization, user)
    deep = make_agent(organization, user, name="Deep one", depth="deep")
    assert config(organization, inherits)["depth"] == "quick"
    assert config(organization, deep)["depth"] == "deep"


def test_the_request_body_still_wins(auth_client, user, organization):
    org_settings(auth_client, organization)
    agent = make_agent(
        organization, user, goal="tenders", opportunity_types=[], sources=["news"], depth="deep"
    )
    resolved = config(organization, agent, {"sources": ["procurement"], "depth": "quick"})
    assert resolved["sources"] == ["procurement"]
    assert resolved["depth"] == "quick"


@pytest.mark.django_db(transaction=True)
def test_a_run_records_the_merged_scope_and_the_agents_depth_budget(
    auth_client, user, organization
):
    org_settings(auth_client, organization)
    agent = make_agent(organization, user, sources=["news"], depth="deep")
    run = services.start_run(organization_id=organization.pk, agent_id=agent.pk, actor=user)
    assert run.config["sources"] == ["web", "grants", "news"]
    assert run.budget == load_sources_file().discovery.budget_for("deep")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_an_agent_can_be_saved_with_only_additions(auth_client, organization):
    folder = make_folder(organization, "Contracts")
    response = auth_client.post(
        agents_url(organization),
        {
            "name": "Narrow",
            "sources": [],
            "opportunity_types": [],
            "depth": "deep",
            "folders": [folder.pk],
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["depth"] == "deep"
    assert response.data["folders"] == [{"id": folder.pk, "name": "Contracts"}]


def test_an_agent_cannot_use_another_orgs_folder(auth_client, user, organization):
    other = ready_organization(user, name="Other")
    response = auth_client.post(
        agents_url(organization),
        {"name": "Theirs", "folders": [make_folder(other).pk]},
        format="json",
    )
    assert response.status_code == 400 and "folders" in response.data


def test_the_agent_shows_what_it_will_use_and_where_each_part_comes_from(
    auth_client, user, organization
):
    shared = make_folder(organization, "Shared")
    org_settings(auth_client, organization, folders=[shared.pk], depth="quick")
    agent = make_agent(organization, user, sources=["news", "web"], opportunity_types=[])
    agent.folders.set([make_folder(organization, "Own")])
    effective = auth_client.get(agent_url(organization, agent.pk)).data["effective"]
    assert effective["depth"] == {"value": "quick", "origin": "organization"}
    assert effective["sources"] == [
        {"value": "web", "origin": "organization"},
        {"value": "grants", "origin": "organization"},
        {"value": "news", "origin": "agent"},
    ]
    assert [(f["name"], f["origin"]) for f in effective["folders"]] == [
        ("Shared", "organization"),
        ("Own", "agent"),
    ]


# ---------------------------------------------------------------------------
# Website links
# ---------------------------------------------------------------------------


def org_link(organization, user, url, **data):
    from apps.organizations import services as organization_services

    return organization_services.add_org_source(
        organization_id=organization.pk, actor=user, url=url, **data
    )


def test_the_agent_adds_its_links_to_the_orgs_enabled_ones(user, organization):
    org_link(organization, user, "https://org.example/calls", label="Org calls")
    org_link(organization, user, "https://org.example/off", enabled=False)
    agent = make_agent(
        organization,
        user,
        links=[
            {"url": "https://agent.example/grants", "label": "Theirs", "enabled": True},
            {"url": "https://org.example/calls", "label": "Dup", "notes": "", "enabled": True},
            {"url": "https://agent.example/off", "label": "", "notes": "", "enabled": False},
        ],
    )
    links = config(organization, agent)["links"]
    assert [link["url"] for link in links] == [
        "https://org.example/calls",
        "https://agent.example/grants",
    ]
    scope = selectors.agent_scope(organization, agent)
    assert [link["origin"] for link in scope["links"]] == ["organization", "agent"]


@pytest.mark.django_db(transaction=True)
def test_a_run_reads_the_links_frozen_on_it(auth_client, user, organization):
    from apps.discovery import tasks

    agent = make_agent(
        organization,
        user,
        links=[{"url": "https://agent.example/grants", "label": "", "notes": "", "enabled": True}],
    )
    run = services.start_run(organization_id=organization.pk, agent_id=agent.pk, actor=user)
    org_link(organization, user, "https://org.example/after-the-run")
    request = tasks._request(run)
    assert [s["url"] for s in request.org_sources] == ["https://agent.example/grants"]


def test_agent_links_are_validated_and_deduplicated(auth_client, organization):
    bad = auth_client.post(
        agents_url(organization),
        {"name": "Links", "links": [{"url": "not a url"}]},
        format="json",
    )
    assert bad.status_code == 400 and "links" in bad.data
    ok = auth_client.post(
        agents_url(organization),
        {
            "name": "Links",
            "links": [
                {"url": "https://a.example/x", "label": "A"},
                {"url": "https://a.example/x", "label": "Again"},
            ],
        },
        format="json",
    )
    assert ok.status_code == 201, ok.data
    assert ok.data["links"] == [
        {"url": "https://a.example/x", "label": "A", "notes": "", "enabled": True}
    ]


# ---------------------------------------------------------------------------
# What a run used, and where each part came from
# ---------------------------------------------------------------------------


def run_detail(auth_client, organization, run):
    return auth_client.get(
        reverse(
            "v1:discovery:run-detail",
            kwargs={"organization_id": organization.pk, "run_id": run.pk},
        )
    ).data


@pytest.mark.django_db(transaction=True)
def test_a_run_shows_built_in_sources_apart_from_websites_with_their_origin(
    auth_client, user, organization
):
    org_settings(auth_client, organization, sources=["web"])
    org_link(organization, user, "https://org.example/calls", label="Org calls")
    agent = make_agent(
        organization,
        user,
        sources=["news"],
        links=[{"url": "https://agent.example/x", "label": "Mine", "notes": "", "enabled": True}],
    )
    run = services.start_run(organization_id=organization.pk, agent_id=agent.pk, actor=user)
    used = run_detail(auth_client, organization, run)["settings"]
    assert used["builtin_sources"] == [
        {"value": "web", "origin": "organization"},
        {"value": "news", "origin": "agent"},
    ]
    assert used["websites"] == [
        {"url": "https://org.example/calls", "label": "Org calls", "origin": "organization"},
        {"url": "https://agent.example/x", "label": "Mine", "origin": "agent"},
    ]
    assert used["depth"] == {"value": "standard", "origin": "organization"}


@pytest.mark.django_db(transaction=True)
def test_settings_chosen_for_one_run_say_so(auth_client, user, organization):
    agent = make_agent(organization, user, goal="tenders", opportunity_types=[], sources=[])
    run = services.start_run(
        organization_id=organization.pk,
        agent_id=agent.pk,
        actor=user,
        overrides={"sources": ["procurement"], "depth": "deep"},
    )
    used = run_detail(auth_client, organization, run)["settings"]
    assert used["builtin_sources"] == [{"value": "procurement", "origin": "run"}]
    assert used["depth"] == {"value": "deep", "origin": "run"}


def test_older_runs_report_their_settings_as_the_organizations(auth_client, user, organization):
    from apps.discovery.models import DiscoveryRun

    agent = make_agent(organization, user)
    run = DiscoveryRun.objects.create(
        organization=organization,
        agent=agent,
        agent_name=agent.name,
        trigger="manual",
        config={"sources": ["web"], "depth": "quick"},
    )
    used = run_detail(auth_client, organization, run)["settings"]
    assert used["builtin_sources"] == [{"value": "web", "origin": "organization"}]
    assert used["websites"] == []
