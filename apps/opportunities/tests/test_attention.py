"""The overview's pipeline counts and its "needs your attention" list."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.discovery.models import DiscoveryRun
from apps.discovery.tests.factories import make_agent, make_opportunity

pytestmark = pytest.mark.django_db


def overview(auth_client, organization):
    return auth_client.get(
        reverse("v1:opportunities:overview", kwargs={"organization_id": organization.pk})
    ).data


def gap_brief():
    return {
        "brief": {
            "eligibility_checklist": [
                {"criterion": "Registered NGO", "meets": True, "notes": ""},
                {"criterion": "Three audits", "meets": False, "notes": ""},
            ]
        }
    }


def test_pipeline_counts_open_opportunities_by_stage(auth_client, organization):
    for n, status in enumerate(["new", "new", "reviewing", "pursuing", "submitted", "won"]):
        make_opportunity(organization, title=f"Opp {n}", status=status)
    assert overview(auth_client, organization)["pipeline"] == {
        "new": 2,
        "reviewing": 1,
        "pursuing": 1,
        "submitted": 1,
    }


def test_attention_lists_what_needs_doing(auth_client, user, organization):
    soon = timezone.now() + timedelta(days=5)
    make_opportunity(organization, title="To review")
    make_opportunity(organization, title="Due soon", status="pursuing", deadline=soon)
    make_opportunity(organization, title="Gap", status="reviewing", requirements=gap_brief())
    agent = make_agent(organization, user)
    DiscoveryRun.objects.create(
        organization=organization,
        agent=agent,
        agent_name=agent.name,
        trigger="manual",
        status="failed",
    )
    items = {item["kind"]: item for item in overview(auth_client, organization)["attention"]}
    assert items["to_review"]["count"] == 2  # new or reviewing, no decision yet
    assert items["deadline_without_proposal"]["count"] == 1
    assert items["eligibility_gap"]["count"] == 1
    assert items["failed_runs"]["count"] == 1
    assert items["failed_runs"]["target"]


def test_nothing_needs_attention_on_an_empty_organization(auth_client, organization):
    assert overview(auth_client, organization)["attention"] == []
