"""Client leads through the app: a Clients worker's run saves businesses as leads."""

from unittest import mock

import pytest
from django.urls import reverse

from apps.discovery import selectors
from apps.discovery.models import DiscoveryRun
from apps.opportunities import services as opportunity_services
from apps.opportunities.models import Opportunity
from apps.organizations import services as organization_services

from .factories import make_agent

# Starting a run hands it over on commit; with JOBS_RUN_INLINE it then runs inline
# (the "fake" runner in tests: sample businesses, no network or keys).
pytestmark = pytest.mark.django_db(transaction=True)


def discovery_url(name, organization, **kwargs):
    return reverse(f"v1:discovery:{name}", kwargs={"organization_id": organization.pk, **kwargs})


def opportunities_url(name, organization, **kwargs):
    return reverse(
        f"v1:opportunities:{name}", kwargs={"organization_id": organization.pk, **kwargs}
    )


@pytest.fixture
def clients_org(user, organization):
    organization_services.update_organization(
        organization_id=organization.pk, actor=user, goals=["clients"]
    )
    organization_services.update_client_brief(
        organization_id=organization.pk,
        actor=user,
        services=[{"name": "Business websites", "description": "", "price": ""}],
        customer_types=["hospitality", "other"],
        target_cities=["Nairobi", "Mombasa"],
        target_countries=["KE"],
    )
    organization.refresh_from_db()
    return organization


def test_a_clients_worker_saves_businesses_as_client_leads(auth_client, user, clients_org):
    agent = make_agent(clients_org, user, goal="clients", opportunity_types=[], sources=[])
    response = auth_client.post(discovery_url("agent-run", clients_org, agent_id=agent.pk))
    assert response.status_code == 202, response.data
    run = DiscoveryRun.objects.get(pk=response.data["id"])
    assert run.status == "succeeded", run.error
    assert run.config["goal"] == "clients"
    assert run.config["sources"] == ["businesses"]
    assert run.config["match_threshold"] == 40  # leads are ranked, not filtered hard

    leads = Opportunity.objects.filter(organization=clients_org)
    assert leads.count() == run.found > 0
    assert set(leads.values_list("opportunity_type", flat=True)) == {"client"}
    lead = leads.order_by("-match_score").first()
    assert lead.business["phone"]
    assert "signals" in lead.business

    detail = auth_client.get(opportunities_url("detail", clients_org, opportunity_id=lead.pk))
    assert detail.data["business"]["source_label"] == "Sample directory"
    assert detail.data["brief_status"] == "none"  # leads get a pitch, not a call brief


def test_a_client_search_is_planned_from_the_brief(clients_org):
    assert selectors.client_search_plan(clients_org) == {
        "customer_types": ["hospitality"],  # "other" maps to no directory category
        "places": ["Nairobi", "Mombasa"],
        "country": "KE",
        "signals": [],
    }


def test_without_cities_the_profile_city_is_searched(user, organization):
    organization_services.update_organization(
        organization_id=organization.pk, actor=user, city="Kisumu"
    )
    organization.refresh_from_db()
    plan = selectors.client_search_plan(organization)
    assert plan["places"] == ["Kisumu"]
    assert plan["country"] == "KE"


def test_an_organization_that_only_wants_clients_searches_for_them(auth_client, clients_org):
    response = auth_client.post(discovery_url("runs", clients_org), {}, format="json")
    assert response.status_code == 202, response.data
    run = DiscoveryRun.objects.get(pk=response.data["id"])
    assert run.config["goal"] == "clients"
    assert run.status == "succeeded"
    assert run.found > 0


def candidate(name, location, url):
    from apps.ai.schemas.discovery import Candidate

    return Candidate(
        title=name,
        opportunity_type="client",
        source_url=url,
        location=location,
        match_score=70,
        business={"name": name},
    )


def test_leads_are_told_apart_by_name_and_place(user, clients_org):
    agent = make_agent(clients_org, user, goal="clients", opportunity_types=[], sources=[])
    run = DiscoveryRun.objects.create(
        organization=clients_org, agent=agent, agent_name=agent.name, trigger="manual"
    )
    add = opportunity_services.add_discovered
    with mock.patch("apps.opportunities.services.enqueue") as enqueue:
        _, nairobi = add(run=run, candidate=candidate("City Hotel", "Nairobi", "https://a.org/1"))
        _, mombasa = add(run=run, candidate=candidate("City Hotel", "Mombasa", "https://b.org/2"))
        _, again = add(run=run, candidate=candidate("City Hotel", "Nairobi", "https://c.org/3"))
    assert (nairobi, mombasa, again) == (True, True, False)
    enqueue.assert_not_called()  # no opportunity brief for client leads


def test_a_placeholder_domain_in_the_user_agent_is_flagged(settings):
    from django.core.checks import run_checks

    settings.AI_HTTP_USER_AGENT = "LeadlineBot/0.1 (+https://leadline.example.com)"
    assert "discovery.W006" in {message.id for message in run_checks()}
    settings.AI_HTTP_USER_AGENT = "LeadlineBot/0.1"
    assert "discovery.W006" not in {message.id for message in run_checks()}
