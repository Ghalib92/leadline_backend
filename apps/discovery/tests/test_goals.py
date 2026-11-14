"""Goals: what an organization wants to find (clients, tenders, grants). Each agent has
one, and it decides which opportunity types and sources the agent's runs use."""

import pytest
from django.urls import reverse

from apps.discovery import selectors, services
from apps.discovery.choices import GOAL_SCOPES
from apps.discovery.models import DiscoveryRun
from apps.organizations import services as organization_services

from .factories import make_agent, search_settings

pytestmark = pytest.mark.django_db


def url(name, organization, **kwargs):
    return reverse(f"v1:discovery:{name}", kwargs={"organization_id": organization.pk, **kwargs})


def set_goals(organization, user, goals):
    organization_services.update_organization(
        organization_id=organization.pk, actor=user, goals=goals
    )
    organization.refresh_from_db()


def create(client, organization, **body):
    return client.post(url("agents", organization), {"name": "Finder", **body}, format="json")


# --- The vocabulary ----------------------------------------------------------------------


def test_every_opportunity_type_belongs_to_exactly_one_goal():
    from apps.organizations.choices import OpportunityType

    owners = {
        t: [g for g, s in GOAL_SCOPES.items() if t in s.opportunity_types]
        for t in OpportunityType.values
    }
    assert all(len(goals) == 1 for goals in owners.values()), owners


def test_run_settings_list_the_goals_for_the_forms(auth_client, organization):
    goals = auth_client.get(url("run-settings", organization)).data["goals"]
    by_key = {goal["key"]: goal for goal in goals}
    assert list(by_key) == ["clients", "tenders", "grants"]
    assert by_key["tenders"]["opportunity_types"] == ["tender", "rfp", "consultancy"]
    assert by_key["tenders"]["sources"] == ["web", "procurement", "news"]
    assert by_key["grants"]["available"] is True
    assert by_key["clients"] == {
        **by_key["clients"],
        "opportunity_types": [],
        "sources": [],
        "available": False,
    }


# --- Creating and changing agents --------------------------------------------------------


def test_a_new_agent_takes_the_organizations_first_goal_that_can_run(
    auth_client, user, organization
):
    set_goals(organization, user, ["clients", "tenders"])
    response = create(auth_client, organization)
    assert response.status_code == 201, response.data
    assert response.data["goal"] == "tenders"


def test_without_goals_picked_an_agent_defaults_to_grants(auth_client, organization):
    response = create(auth_client, organization)
    assert response.data["goal"] == "grants"


def test_an_agent_can_only_have_a_goal_the_organization_picked(auth_client, user, organization):
    set_goals(organization, user, ["tenders"])
    response = create(auth_client, organization, goal="grants")
    assert response.status_code == 400
    assert "goal" in response.data
    assert create(auth_client, organization, goal="tenders").status_code == 201


def test_a_clients_agent_can_be_set_up_before_its_search_exists(auth_client, user, organization):
    set_goals(organization, user, ["clients"])
    response = create(auth_client, organization, goal="clients", keywords=["hotels"])
    assert response.status_code == 201, response.data
    assert response.data["effective"]["sources"] == []
    assert response.data["effective"]["opportunity_types"] == []


@pytest.mark.parametrize(
    "body, field",
    [
        ({"goal": "grants", "opportunity_types": ["tender"]}, "opportunity_types"),
        ({"goal": "tenders", "opportunity_types": ["grant"]}, "opportunity_types"),
        ({"goal": "tenders", "sources": ["grants"]}, "sources"),
        ({"goal": "grants", "sources": ["procurement"]}, "sources"),
    ],
)
def test_types_and_sources_must_fit_the_goal(auth_client, organization, body, field):
    response = create(auth_client, organization, **body)
    assert response.status_code == 400
    assert field in response.data


def test_changing_the_goal_drops_what_does_not_fit(auth_client, user, organization):
    agent = make_agent(
        organization, user, opportunity_types=["grant", "award"], sources=["web", "grants"]
    )
    detail = url("agent-detail", organization, agent_id=agent.pk)
    response = auth_client.patch(detail, {"goal": "tenders"}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["goal"] == "tenders"
    assert response.data["opportunity_types"] == []
    assert response.data["sources"] == ["web"]
    event = organization.events.filter(action="agent.updated").latest("created_at")
    assert event.metadata["fields"] == ["goal", "opportunity_types", "sources"]


def test_a_new_goal_with_its_own_types_is_checked_against_the_new_goal(
    auth_client, user, organization
):
    agent = make_agent(organization, user)
    detail = url("agent-detail", organization, agent_id=agent.pk)
    response = auth_client.patch(
        detail, {"goal": "tenders", "opportunity_types": ["rfp"]}, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["opportunity_types"] == ["rfp"]


def test_an_agent_keeps_its_goal_when_the_organization_drops_it(auth_client, user, organization):
    agent = make_agent(organization, user, goal="grants")
    set_goals(organization, user, ["tenders"])
    detail = url("agent-detail", organization, agent_id=agent.pk)
    response = auth_client.patch(detail, {"name": "Renamed"}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["goal"] == "grants"


# --- What a run searches -----------------------------------------------------------------


def test_an_agents_run_only_uses_its_goals_types_and_sources(user, organization):
    search_settings(
        organization,
        sources=["web", "grants", "procurement"],
        opportunity_types=["grant", "tender"],
    )
    grants = make_agent(organization, user, opportunity_types=[], sources=[])
    tenders = make_agent(
        organization, user, name="Tenders", goal="tenders", opportunity_types=[], sources=[]
    )

    grants_config = services._resolve_run_config(organization, grants, None)
    assert grants_config["goal"] == "grants"
    assert grants_config["opportunity_types"] == ["grant"]
    assert grants_config["sources"] == ["web", "grants"]

    tenders_config = services._resolve_run_config(organization, tenders, None)
    assert tenders_config["goal"] == "tenders"
    assert tenders_config["opportunity_types"] == ["tender"]
    assert tenders_config["sources"] == ["web", "procurement"]


def test_with_nothing_left_a_run_uses_the_goals_defaults(user, organization):
    search_settings(organization, sources=["grants"], opportunity_types=["grant"])
    agent = make_agent(organization, user, goal="tenders", opportunity_types=[], sources=[])
    scope = selectors.agent_scope(organization, agent)
    assert [row["value"] for row in scope["opportunity_types"]] == [
        "tender",
        "rfp",
        "consultancy",
    ]
    assert [row["value"] for row in scope["sources"]] == ["web", "procurement", "news"]
    assert {row["origin"] for row in scope["sources"]} == {"agent"}


def test_a_run_may_narrow_its_goal_but_never_leave_it(user, organization):
    agent = make_agent(organization, user, goal="tenders", opportunity_types=[], sources=[])
    narrowed = services._resolve_run_config(
        organization, agent, {"sources": ["procurement", "grants"]}
    )
    assert narrowed["sources"] == ["procurement"]
    outside = services._resolve_run_config(organization, agent, {"opportunity_types": ["grant"]})
    assert outside["opportunity_types"] == ["tender", "rfp", "consultancy"]


def test_an_organization_search_covers_the_goals_it_picked(user, organization):
    search_settings(
        organization,
        sources=["web", "grants", "procurement"],
        opportunity_types=["grant", "tender"],
    )
    set_goals(organization, user, ["clients", "tenders"])
    config = services._resolve_run_config(organization, None, None)
    assert config["goal"] == ""
    assert config["opportunity_types"] == ["tender"]
    assert config["sources"] == ["web", "procurement"]


def test_without_goals_picked_an_organization_search_is_unchanged(user, organization):
    search_settings(organization, sources=["web", "grants"], opportunity_types=["grant", "tender"])
    config = services._resolve_run_config(organization, None, None)
    assert config["opportunity_types"] == ["grant", "tender"]
    assert config["sources"] == ["web", "grants"]


# --- Goals that cannot run yet -----------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_clients_agent_cannot_run_yet(auth_client, user, organization):
    agent = make_agent(organization, user, goal="clients", opportunity_types=[], sources=[])
    response = auth_client.post(url("agent-run", organization, agent_id=agent.pk), format="json")
    assert response.status_code == 409
    assert response.data["code"] == "goal_not_available"
    assert not DiscoveryRun.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_an_organization_that_only_wants_clients_cannot_search_yet(auth_client, user, organization):
    set_goals(organization, user, ["clients"])
    response = auth_client.post(url("runs", organization), {}, format="json")
    assert response.status_code == 409
    assert response.data["code"] == "goal_not_available"
    assert not DiscoveryRun.objects.exists()


def test_a_scheduled_clients_agent_is_skipped(user, organization):
    clients = make_agent(
        organization,
        user,
        name="Clients",
        goal="clients",
        schedule="daily",
        opportunity_types=[],
        sources=[],
    )
    grants = make_agent(organization, user, name="Grants", schedule="daily")
    assert not selectors.is_agent_eligible(clients)
    assert selectors.agents_due(limit=10) == [grants.pk]
