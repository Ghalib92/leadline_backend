"""What an organization wants to find: clients, tenders or grants."""

from unittest import mock

import pytest
from django.urls import reverse

from .. import services

pytestmark = pytest.mark.django_db


def detail(organization):
    return reverse("v1:organizations:detail", kwargs={"organization_id": organization.pk})


def test_new_organizations_have_not_picked_goals(auth_client, organization):
    assert auth_client.get(detail(organization)).data["goals"] == []


def test_goals_are_set_at_creation(auth_client):
    response = auth_client.post(
        reverse("v1:organizations:list"),
        {"name": "Digital Appex", "country": "KE", "goals": ["clients", "tenders"]},
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["goals"] == ["clients", "tenders"]


def test_admin_changes_goals(auth_client, organization):
    response = auth_client.patch(
        detail(organization), {"goals": ["grants", "grants", "tenders"]}, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["goals"] == ["grants", "tenders"]  # deduplicated, order kept
    event = organization.events.filter(action="organization.updated").latest("created_at")
    assert event.metadata["fields"] == ["goals"]


@pytest.mark.parametrize("goals", [["jobs"], ["clients", "lottery"], "clients"])
def test_goal_validation(auth_client, organization, goals):
    response = auth_client.patch(detail(organization), {"goals": goals}, format="json")
    assert response.status_code == 400
    assert "goals" in response.data


def test_changing_goals_does_not_rebuild_the_ai_summary(user, organization):
    with mock.patch.object(services, "_context_changed") as context_changed:
        services.update_organization(organization_id=organization.pk, actor=user, goals=["clients"])
        context_changed.assert_not_called()
        services.update_organization(
            organization_id=organization.pk, actor=user, mission="Build useful software."
        )
        context_changed.assert_called_once()
