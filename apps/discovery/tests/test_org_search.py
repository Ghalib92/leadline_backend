""""Search for opportunities" from the organization's search settings (no saved search),
and the run outcome that explains a run that added nothing."""

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.discovery import selectors, services
from apps.discovery.models import DiscoveryRun, RunCandidate, RunEvent, RunSettings
from apps.organizations import services as organization_services
from apps.organizations.tests.factories import make_folder

from .factories import make_agent, ready_organization

# Starting a run hands it over on commit; with JOBS_RUN_INLINE it then runs inline.
pytestmark = pytest.mark.django_db(transaction=True)


def url(name, organization, **kwargs):
    return reverse(f"v1:discovery:{name}", kwargs={"organization_id": organization.pk, **kwargs})


def search(client, organization, **body):
    return client.post(url("runs", organization), body, format="json")


def save_settings(organization, actor, **overrides):
    data = {
        "depth": "quick",
        "sources": ["procurement"],
        "opportunity_types": ["tender"],
        "folders": [],
        **overrides,
    }
    return services.update_run_settings(organization_id=organization.pk, actor=actor, **data)


def test_search_uses_the_organization_settings_not_a_saved_search(
    auth_client, user, organization
):
    # A saved search with other settings must not be picked up by the main button.
    make_agent(organization, user, sources=["web"], opportunity_types=["fellowship"])
    folder = make_folder(organization, "Contracts")
    save_settings(organization, user, folders=[folder.pk])
    organization_services.update_preferences(
        organization_id=organization.pk, actor=user, keywords=["data skills"]
    )

    response = search(auth_client, organization)
    assert response.status_code == 202, response.data
    run = DiscoveryRun.objects.get(pk=response.data["id"])
    assert run.agent is None
    assert run.agent_name == services.ORG_SEARCH_NAME
    assert run.config["sources"] == ["procurement"]
    assert run.config["opportunity_types"] == ["tender"]
    assert run.config["keywords"] == ["data skills"]
    assert run.config["depth"] == "quick"
    assert [f["name"] for f in run.config["folders"]] == ["Contracts"]
    assert run.config["overrides"] == []
    assert run.status == "succeeded"


def test_overrides_apply_to_this_search_only(auth_client, user, organization):
    save_settings(organization, user)
    body = {"depth": "deep", "sources": ["web"], "opportunity_types": ["grant"]}
    run = DiscoveryRun.objects.get(pk=search(auth_client, organization, **body).data["id"])
    assert run.config["depth"] == "deep"
    assert run.config["sources"] == ["web"]
    assert run.config["overrides"] == ["depth", "opportunity_types", "sources"]
    assert selectors.run_settings_for(organization).sources == ["procurement"]


def test_any_member_may_search(organization, member):
    client = APIClient()
    client.force_authenticate(member)
    response = search(client, organization)
    assert response.status_code == 202, response.data
    assert DiscoveryRun.objects.get(pk=response.data["id"]).triggered_by == member


def test_outsiders_cannot_search(organization):
    from apps.authentication.tests.factories import UserFactory

    client = APIClient()
    client.force_authenticate(UserFactory())
    assert search(client, organization).status_code == 404
    assert not DiscoveryRun.objects.exists()


def test_search_refuses_while_another_run_is_active(auth_client, user, organization):
    DiscoveryRun.objects.create(
        organization=organization, agent_name="Busy", trigger="manual", status="running"
    )
    response = search(auth_client, organization)
    assert response.status_code == 409
    assert response.data["code"] == "run_in_progress"


def test_search_refuses_an_incomplete_setup(auth_client, organization):
    organization.description = ""
    organization.save()
    response = search(auth_client, organization)
    assert response.status_code == 409
    assert response.data["code"] == "setup_incomplete"


def test_invalid_overrides_are_rejected(auth_client, organization):
    assert search(auth_client, organization, depth="huge").status_code == 400
    assert not DiscoveryRun.objects.exists()


def test_new_settings_start_from_the_preferred_types(user):
    organization = ready_organization(user, name="Preferred types")
    assert not RunSettings.objects.filter(organization=organization).exists()
    assert selectors.run_settings_for(organization).opportunity_types == ["grant", "tender"]


def test_outcome_explains_a_run_that_added_nothing(auth_client, organization):
    run = DiscoveryRun.objects.create(
        organization=organization,
        agent_name="Opportunity search",
        trigger="manual",
        status="succeeded",
        config={"match_threshold": 55},
    )
    now = timezone.now()
    for seq, skip in enumerate(["closed", "closed", "wrong_type", "unreadable", ""], start=1):
        RunEvent.objects.create(
            run=run, seq=seq, step="read", message="…", data={"skip": skip} if skip else {}, at=now
        )
    for score in (20, 8):
        RunCandidate.objects.create(
            run=run,
            title=f"Fellowship {score}",
            opportunity_type="fellowship",
            match_score=score,
            decision="below_threshold",
        )

    outcome = auth_client.get(url("run-detail", organization, run_id=run.pk)).data["outcome"]
    assert outcome["added"] == 0
    assert outcome["below_threshold"] == 2
    assert outcome["best_score"] == 20
    assert outcome["match_threshold"] == 55
    assert outcome["skipped"] == {
        "closed": 2,
        "not_an_opportunity": 0,
        "wrong_type": 1,
        "excluded": 0,
        "unreadable": 1,
    }
