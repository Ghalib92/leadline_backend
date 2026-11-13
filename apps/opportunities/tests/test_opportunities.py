from decimal import Decimal

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.ai.schemas.discovery import Candidate
from apps.authentication.tests.factories import UserFactory
from apps.discovery.tests.factories import days, make_agent, make_opportunity
from apps.opportunities import services
from apps.opportunities.dedupe import normalize_url
from apps.organizations import services as organization_services

pytestmark = pytest.mark.django_db


def url(name, organization, **kwargs):
    return reverse(
        f"v1:opportunities:{name}", kwargs={"organization_id": organization.pk, **kwargs}
    )


def as_user(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def titles(response):
    assert response.status_code == 200, response.data
    return [row["title"] for row in response.data["results"]]


@pytest.fixture
def feed(user, organization):
    agent = make_agent(organization, user)
    return {
        "grant": make_opportunity(
            organization,
            title="Water Grant",
            agent=agent,
            match_score=90,
            deadline=days(5),
            amount_min=Decimal("10000"),
            amount_max=Decimal("50000"),
            currency="USD",
        ),
        "tender": make_opportunity(
            organization,
            title="Clinic Tender",
            opportunity_type="tender",
            match_score=60,
            deadline=days(40),
            status="pursuing",
            summary="Build rural clinics",
        ),
        "saved": make_opportunity(
            organization, title="Old Award", opportunity_type="award", match_score=40, saved=True
        ),
    }


def test_feed_shape(auth_client, organization, feed):
    response = auth_client.get(url("list", organization))
    assert response.data["count"] == 3
    grant = next(row for row in response.data["results"] if row["title"] == "Water Grant")
    assert grant["agent_name"] == "Education grants"
    assert grant["opportunity_type"] == "grant"
    assert grant["amount"] == {"min": "10000.00", "max": "50000.00", "currency": "USD"}
    assert grant["status"] == "new" and grant["saved"] is False
    tender = next(row for row in response.data["results"] if row["title"] == "Clinic Tender")
    assert tender["amount"] is None and tender["agent"] is None


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"type": ["grant", "tender"]}, {"Water Grant", "Clinic Tender"}),
        ({"status": "pursuing"}, {"Clinic Tender"}),
        ({"saved": "true"}, {"Old Award"}),
        ({"search": "rural"}, {"Clinic Tender"}),
        ({"min_score": 60}, {"Water Grant", "Clinic Tender"}),
        ({"deadline_before": days(10).isoformat()}, {"Water Grant"}),
    ],
)
def test_feed_filters(auth_client, organization, feed, params, expected):
    assert set(titles(auth_client.get(url("list", organization), params))) == expected


def test_feed_filter_by_run(auth_client, organization, user, feed):
    from apps.discovery.models import DiscoveryRun

    agent = feed["grant"].agent
    run = DiscoveryRun.objects.create(
        organization=organization,
        agent=agent,
        agent_name=agent.name,
        trigger="manual",
        budget={},
    )
    feed["grant"].run = run
    feed["grant"].save(update_fields=["run"])

    endpoint = url("list", organization)
    assert titles(auth_client.get(endpoint, {"run": run.pk})) == ["Water Grant"]
    assert titles(auth_client.get(endpoint, {"run": "nonexistent"})) == []


def test_feed_sorting(auth_client, organization, feed):
    endpoint = url("list", organization)
    assert titles(auth_client.get(endpoint, {"sort": "-match_score"})) == [
        "Water Grant",
        "Clinic Tender",
        "Old Award",
    ]
    # No deadline sorts last both ways.
    assert titles(auth_client.get(endpoint, {"sort": "deadline"}))[-1] == "Old Award"
    assert titles(auth_client.get(endpoint, {"sort": "-deadline"}))[-1] == "Old Award"


@pytest.mark.parametrize(
    "params", [{"type": "lottery"}, {"status": "lost-ish"}, {"min_score": 101}, {"sort": "title"}]
)
def test_invalid_filters(auth_client, organization, params):
    assert auth_client.get(url("list", organization), params).status_code == 400


def test_members_triage(organization, member, feed):
    client = as_user(member)
    detail = url("detail", organization, opportunity_id=feed["grant"].pk)
    response = client.patch(
        detail, {"status": "reviewing", "saved": True, "notes": "Call them"}, format="json"
    )
    assert response.status_code == 200, response.data
    assert (response.data["status"], response.data["saved"], response.data["notes"]) == (
        "reviewing",
        True,
        "Call them",
    )
    event = organization.events.get(action="opportunity.updated")
    assert event.metadata == {"fields": ["notes", "saved", "status"], "status": "reviewing"}
    assert client.patch(detail, {"status": "maybe"}, format="json").status_code == 400
    assert client.patch(detail, {"notes": "x" * 5001}, format="json").status_code == 400


def test_opportunities_are_private(user, auth_client, organization, feed):
    detail = url("detail", organization, opportunity_id=feed["grant"].pk)
    outsider = as_user(UserFactory())
    assert outsider.get(detail).status_code == 404
    assert outsider.patch(detail, {"status": "maybe"}, format="json").status_code == 404
    other = organization_services.create_organization(actor=user, name="Other")
    assert auth_client.get(url("detail", other, opportunity_id=feed["grant"].pk)).status_code == 404
    organization_services.archive_organization(organization_id=organization.pk, actor=user)
    assert auth_client.patch(detail, {"saved": True}, format="json").status_code == 400


def test_overview(auth_client, user, organization, feed):
    response = auth_client.get(url("overview", organization))
    assert response.status_code == 200
    data = response.data
    assert data["new_this_week"] == 3
    assert data["pursuing"] == 1
    assert data["active_agents"] == 1
    assert data["due_soon"] == 1  # within 14 days and still open
    assert [row["title"] for row in data["upcoming"]] == ["Water Grant"]
    assert len(data["latest"]) == 3
    assert data["checklist"] == {"profile": True, "agent": True, "teammate": False}


def candidate(**overrides):
    data = {
        "title": "Water Grant",
        "funder": "Northbridge Foundation",
        "opportunity_type": "grant",
        "source_url": "https://example.org/water-grant",
        "match_score": 80,
    }
    return Candidate(**{**data, **overrides})


def test_add_discovered_deduplicates_by_url_and_by_title(organization, feed):
    same_url = candidate(
        title="Totally different", source_url="https://www.EXAMPLE.org/water-grant/?utm_source=x"
    )
    same_title = candidate(source_url="https://elsewhere.org/listing/9")
    for item in (same_url, same_title):
        existing, created = services.add_discovered(run=_run(organization), candidate=item)
        assert (existing.pk, created) == (feed["grant"].pk, False)
    fresh, created = services.add_discovered(
        run=_run(organization), candidate=candidate(title="New Call", source_url="https://n.org/1")
    )
    assert created and fresh.title == "New Call"


def _run(organization):
    from apps.discovery.models import DiscoveryRun

    return DiscoveryRun.objects.create(
        organization=organization, agent_name="Agent", trigger="manual", status="succeeded"
    )


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("https://www.Example.org/a/", "http://example.org/a"),
        ("https://example.org/a?utm_source=x&id=2&b=1", "https://example.org/a?b=1&id=2"),
        ("https://example.org/a#apply", "https://example.org/a"),
    ],
)
def test_url_normalisation(first, second):
    assert normalize_url(first) == normalize_url(second)
    assert normalize_url("https://example.org/a?id=1") != normalize_url(
        "https://example.org/a?id=2"
    )
