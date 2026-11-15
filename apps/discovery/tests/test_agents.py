import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.authentication.tests.factories import UserFactory
from apps.discovery.models import DiscoveryRun, SearchAgent
from apps.organizations import services as organization_services

from .factories import make_agent

pytestmark = pytest.mark.django_db

AGENT = {
    "name": "Education grants",
    "description": "Grants for youth data skills",
    "opportunity_types": ["grant", "fellowship", "grant"],
    "keywords": ["data skills", " ", "youth", "youth"],
    "sources": ["web", "grants"],
    "schedule": "weekly",
}


def url(name, organization, **kwargs):
    return reverse(f"v1:discovery:{name}", kwargs={"organization_id": organization.pk, **kwargs})


def as_user(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_member_creates_an_agent_and_owns_it(organization, member):
    client = as_user(member)
    payload = {
        **AGENT,
        "instructions": "Focus on renewable energy in East Africa. Exclude academic research.",
    }
    response = client.post(url("agents", organization), payload, format="json")
    assert response.status_code == 201, response.data
    assert response.data["owner"] == member.pk
    assert response.data["instructions"] == (
        "Focus on renewable energy in East Africa. Exclude academic research."
    )
    assert response.data["opportunity_types"] == ["grant", "fellowship"]  # deduplicated
    assert response.data["keywords"] == ["data skills", "youth"]
    assert response.data["enabled"] is True
    assert response.data["can_manage"] is True
    assert response.data["last_run_id"] is None

    listing = client.get(url("agents", organization))
    assert [agent["name"] for agent in listing.data] == ["Education grants"]
    assert listing.data[0]["instructions"] == (
        "Focus on renewable energy in East Africa. Exclude academic research."
    )
    assert organization.events.filter(action="agent.created").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "x"},
        {"opportunity_types": ["lottery"]},
        {"sources": ["darkweb"]},
        {"keywords": [f"k{n}" for n in range(21)]},
        {"schedule": "hourly"},
        {"description": "x" * 281},
    ],
)
def test_agent_validation(auth_client, organization, overrides):
    response = auth_client.post(url("agents", organization), {**AGENT, **overrides}, format="json")
    assert response.status_code == 400


def test_only_admins_and_the_owner_manage_an_agent(auth_client, user, organization, member):
    admins_agent = make_agent(organization, user)
    own_agent = make_agent(organization, member, name="Mine")
    client = as_user(member)

    detail = url("agent-detail", organization, agent_id=admins_agent.pk)
    assert client.get(detail).data["can_manage"] is False
    assert client.patch(detail, {"name": "Taken"}, format="json").status_code == 403
    assert client.delete(detail).status_code == 403
    assert client.post(url("agent-run", organization, agent_id=admins_agent.pk)).status_code == 403

    own = url("agent-detail", organization, agent_id=own_agent.pk)
    response = client.patch(own, {"enabled": False, "keywords": ["water"]}, format="json")
    assert response.status_code == 200
    assert (response.data["enabled"], response.data["keywords"]) == (False, ["water"])
    # Admins manage everyone's agents.
    assert auth_client.patch(own, {"name": "Renamed"}, format="json").data["name"] == "Renamed"


def test_deleting_an_agent_keeps_its_runs(auth_client, user, organization):
    agent = make_agent(organization, user)
    run = DiscoveryRun.objects.create(
        organization=organization,
        agent=agent,
        agent_name=agent.name,
        trigger="manual",
        status="succeeded",
    )
    response = auth_client.delete(url("agent-detail", organization, agent_id=agent.pk))
    assert response.status_code == 204
    run.refresh_from_db()
    assert run.agent is None and run.agent_name == "Education grants"


def test_agents_are_private_to_their_organization(auth_client, user, organization):
    agent = make_agent(organization, user)
    other = organization_services.create_organization(actor=user, name="Other")
    assert auth_client.get(url("agent-detail", other, agent_id=agent.pk)).status_code == 404
    outsider = as_user(UserFactory())
    assert outsider.get(url("agents", organization)).status_code == 404
    assert outsider.post(url("agents", organization), AGENT, format="json").status_code == 404


def test_archived_organizations_are_read_only(auth_client, user, organization):
    agent = make_agent(organization, user)
    organization_services.archive_organization(organization_id=organization.pk, actor=user)
    assert auth_client.get(url("agents", organization)).status_code == 200
    assert auth_client.post(url("agents", organization), AGENT, format="json").status_code == 400
    detail = url("agent-detail", organization, agent_id=agent.pk)
    assert auth_client.patch(detail, {"name": "No"}, format="json").status_code == 400
    assert SearchAgent.objects.get(pk=agent.pk).name == "Education grants"


def test_source_categories_for_the_form(auth_client, organization):
    response = auth_client.get(url("sources", organization))
    assert {item["value"] for item in response.data} == {
        "web",
        "grants",
        "procurement",
        "news",
        "businesses",
    }
