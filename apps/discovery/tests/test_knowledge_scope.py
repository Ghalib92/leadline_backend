"""Which company documents a run may read, from the org's search settings, the
automation's extra folders and the run's own choices."""

import pytest
from django.urls import reverse

from apps.discovery import services
from apps.organizations.tests.factories import make_folder

from .factories import make_agent, search_settings

pytestmark = pytest.mark.django_db


def knowledge(organization, agent=None, overrides=None):
    return services._resolve_run_config(organization, agent, overrides)["knowledge"]


def test_by_default_a_run_reads_every_document(organization):
    assert knowledge(organization) == {"mode": "all", "folder_ids": []}


def test_selected_folders_are_the_orgs_plus_the_automations(user, organization):
    org_folder, own = make_folder(organization, "Org"), make_folder(organization, "Own")
    settings = search_settings(organization, knowledge_mode="selected")
    settings.folders.set([org_folder])
    agent = make_agent(organization, user)
    agent.folders.set([own])
    assert knowledge(organization, agent) == {
        "mode": "folders",
        "folder_ids": [org_folder.pk, own.pk],
    }


def test_no_documents_unless_the_automation_adds_folders(user, organization):
    search_settings(organization, knowledge_mode="none")
    plain = make_agent(organization, user)
    with_folder = make_agent(organization, user, name="With folder")
    own = make_folder(organization, "Own")
    with_folder.folders.set([own])
    assert knowledge(organization, plain) == {"mode": "none", "folder_ids": []}
    assert knowledge(organization, with_folder) == {"mode": "folders", "folder_ids": [own.pk]}


def test_all_documents_already_include_any_folder(user, organization):
    agent = make_agent(organization, user)
    agent.folders.set([make_folder(organization, "Own")])
    assert knowledge(organization, agent)["mode"] == "all"


@pytest.mark.parametrize(
    "overrides, expected_mode",
    [
        ({"knowledge_mode": "none"}, "none"),
        ({"knowledge_mode": "all"}, "all"),
        ({"folders": []}, "all"),  # as before scopes: no folders chosen reads everything
    ],
)
def test_a_run_can_choose_for_itself(organization, overrides, expected_mode):
    assert knowledge(organization, overrides=overrides)["mode"] == expected_mode


def test_a_run_choosing_folders_reads_only_those(organization):
    folder = make_folder(organization, "Chosen")
    assert knowledge(organization, overrides={"folders": [folder.pk]}) == {
        "mode": "folders",
        "folder_ids": [folder.pk],
    }


# ---------------------------------------------------------------------------
# Search settings API
# ---------------------------------------------------------------------------


def url(organization):
    return reverse("v1:discovery:run-settings", kwargs={"organization_id": organization.pk})


def body(**overrides):
    return {"depth": "standard", "sources": ["web"], "opportunity_types": ["grant"], **overrides}


def test_the_mode_is_saved_and_read(auth_client, organization):
    folder = make_folder(organization)
    response = auth_client.put(
        url(organization), body(knowledge_mode="selected", folders=[folder.pk]), format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["knowledge_mode"] == "selected"


def test_selected_needs_at_least_one_folder(auth_client, organization):
    response = auth_client.put(
        url(organization), body(knowledge_mode="selected", folders=[]), format="json"
    )
    assert response.status_code == 400 and "folders" in response.data


def test_without_a_mode_folders_mean_selected_and_none_mean_all(auth_client, organization):
    folder = make_folder(organization)
    picked = auth_client.put(url(organization), body(folders=[folder.pk]), format="json")
    assert picked.data["knowledge_mode"] == "selected"
    cleared = auth_client.put(url(organization), body(folders=[]), format="json")
    assert cleared.data["knowledge_mode"] == "all"


@pytest.mark.django_db(transaction=True)
def test_the_run_reports_which_documents_it_could_read(auth_client, user, organization):
    search_settings(organization, knowledge_mode="none")
    agent = make_agent(organization, user)
    run = services.start_run(organization_id=organization.pk, agent_id=agent.pk, actor=user)
    detail = reverse(
        "v1:discovery:run-detail",
        kwargs={"organization_id": organization.pk, "run_id": run.pk},
    )
    assert auth_client.get(detail).data["settings"]["knowledge_mode"] == "none"


def test_the_agent_shows_the_orgs_documents_mode(auth_client, user, organization):
    search_settings(organization, knowledge_mode="none")
    agent = make_agent(organization, user)
    detail = reverse(
        "v1:discovery:agent-detail",
        kwargs={"organization_id": organization.pk, "agent_id": agent.pk},
    )
    assert auth_client.get(detail).data["effective"]["knowledge_mode"] == "none"
