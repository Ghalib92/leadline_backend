import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.ai.config.loader import load_sources_file
from apps.discovery import services
from apps.discovery.models import DiscoveryRun
from apps.organizations.tests.factories import make_folder

from .factories import make_agent, ready_organization

pytestmark = pytest.mark.django_db


def url(organization):
    return reverse("v1:discovery:run-settings", kwargs={"organization_id": organization.pk})


def body(**overrides):
    return {
        "depth": "quick",
        "sources": ["web"],
        "opportunity_types": ["grant"],
        "folders": [],
        **overrides,
    }


def test_defaults_are_created_on_first_read(auth_client, organization):
    data = auth_client.get(url(organization)).data
    assert data["depth"] == "standard"
    assert data["sources"] == ["web", "grants", "procurement"]
    assert data["folders"] == []
    assert [d["key"] for d in data["depths"]] == ["quick", "standard", "deep"]


def test_admin_saves_settings(auth_client, organization):
    folder = make_folder(organization, "Contracts")
    response = auth_client.put(url(organization), body(folders=[folder.pk]), format="json")
    assert response.status_code == 200, response.data
    assert response.data["depth"] == "quick"
    assert response.data["folders"] == [{"id": folder.pk, "name": "Contracts"}]


@pytest.mark.parametrize(
    "bad",
    [
        {"depth": "huge"},
        {"sources": []},
        {"sources": ["fax"]},
        {"opportunity_types": []},
        {"opportunity_types": ["lottery"]},
        {"folders": ["nope12345678"]},
    ],
)
def test_invalid_settings_are_rejected(auth_client, organization, bad):
    assert auth_client.put(url(organization), body(**bad), format="json").status_code == 400


def test_another_organizations_folder_is_rejected(auth_client, user, organization):
    other = ready_organization(user, name="Other")
    response = auth_client.put(
        url(organization), body(folders=[make_folder(other).pk]), format="json"
    )
    assert response.status_code == 400 and "folders" in response.data


def test_members_read_but_cannot_save(organization, member):
    client = APIClient()
    client.force_authenticate(member)
    assert client.get(url(organization)).status_code == 200
    assert client.put(url(organization), body(), format="json").status_code == 403


def test_a_deleted_folder_drops_out_of_the_settings(auth_client, organization):
    keep, gone = make_folder(organization, "Keep"), make_folder(organization, "Gone")
    auth_client.put(url(organization), body(folders=[keep.pk, gone.pk]), format="json")
    gone.delete()
    data = auth_client.get(url(organization)).data
    assert data["folders"] == [{"id": keep.pk, "name": "Keep"}]


# ---------------------------------------------------------------------------
# Starting a run with settings
# ---------------------------------------------------------------------------


def run_url(organization, agent):
    return reverse(
        "v1:discovery:agent-run",
        kwargs={"organization_id": organization.pk, "agent_id": agent.pk},
    )


@pytest.mark.django_db(transaction=True)
def test_a_run_uses_org_settings_plus_the_agents_additions(auth_client, user, organization):
    folder = make_folder(organization, "Contracts", purpose="Delivery proof")
    auth_client.put(url(organization), body(depth="quick", folders=[folder.pk]), format="json")
    agent = make_agent(organization, user, sources=["news"], opportunity_types=["fellowship"])
    response = auth_client.post(run_url(organization, agent), format="json")
    assert response.status_code == 202, response.data
    run = DiscoveryRun.objects.get(pk=response.data["id"])
    assert run.config["depth"] == "quick"
    assert run.config["sources"] == ["web", "news"]  # the org's, plus the agent's
    assert run.config["opportunity_types"] == ["grant", "fellowship"]
    assert run.config["folders"] == [
        {"id": folder.pk, "name": "Contracts", "purpose": "Delivery proof"}
    ]
    assert run.config["overrides"] == []
    assert run.budget == load_sources_file().discovery.budget_for("quick")
    assert response.data["depth"] == "quick"


@pytest.mark.django_db(transaction=True)
def test_the_request_body_overrides_everything(auth_client, user, organization):
    agent = make_agent(organization, user, goal="tenders", opportunity_types=[], sources=[])
    response = auth_client.post(
        run_url(organization, agent),
        {"depth": "deep", "sources": ["procurement"], "opportunity_types": ["rfp"]},
        format="json",
    )
    assert response.status_code == 202, response.data
    run = DiscoveryRun.objects.get(pk=response.data["id"])
    assert run.config["depth"] == "deep" and run.config["sources"] == ["procurement"]
    assert run.budget["max_rounds"] == 3
    assert run.config["overrides"] == ["depth", "opportunity_types", "sources"]


@pytest.mark.django_db(transaction=True)
def test_an_empty_override_list_is_rejected(auth_client, user, organization):
    agent = make_agent(organization, user)
    response = auth_client.post(run_url(organization, agent), {"sources": []}, format="json")
    assert response.status_code == 400
    assert not DiscoveryRun.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_folders_on_a_run_survive_a_rename(auth_client, user, organization):
    folder = make_folder(organization, "Old name")
    agent = make_agent(organization, user)
    response = auth_client.post(
        run_url(organization, agent), {"folders": [folder.pk]}, format="json"
    )
    assert response.status_code == 202, response.data
    folder.name = "New name"
    folder.save()
    run = DiscoveryRun.objects.get(pk=response.data["id"])
    assert run.config["folders"][0]["name"] == "Old name"
    folder_event = run.events.filter(tool="folders").first()
    assert folder_event is not None
    assert "Using 1 knowledge folder(s): Old name" in folder_event.message


@pytest.mark.django_db(transaction=True)
def test_a_scheduled_run_uses_the_org_defaults(auth_client, user, organization):
    auth_client.put(url(organization), body(depth="deep"), format="json")
    agent = make_agent(organization, user, schedule="daily")
    run = services.start_scheduled_run(agent.pk)
    assert run.config["depth"] == "deep"
    assert run.budget == load_sources_file().discovery.budget_for("deep")


def test_old_runs_have_no_depth_or_folders(auth_client, user, organization):
    agent = make_agent(organization, user)
    run = DiscoveryRun.objects.create(
        organization=organization,
        agent=agent,
        agent_name=agent.name,
        trigger="manual",
        status="succeeded",
        config={"match_threshold": 55},
    )
    detail = reverse(
        "v1:discovery:run-detail",
        kwargs={"organization_id": organization.pk, "run_id": run.pk},
    )
    data = auth_client.get(detail).data
    assert data["depth"] == "" and data["folders"] == []
