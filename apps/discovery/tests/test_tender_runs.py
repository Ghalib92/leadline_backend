"""Tenders through the app: a Tenders worker's run saves tenders with their portal facts."""

import pytest
from django.urls import reverse

from apps.discovery.models import DiscoveryRun
from apps.opportunities.models import Opportunity
from apps.organizations import services as organization_services

from .factories import make_agent

# Starting a run hands it over on commit; with JOBS_RUN_INLINE it then runs inline
# (the "fake" runner in tests: sample opportunities, no network or keys).
pytestmark = pytest.mark.django_db(transaction=True)


def test_a_tenders_worker_saves_tenders_with_their_details(auth_client, user, organization):
    organization_services.update_organization(
        organization_id=organization.pk, actor=user, goals=["tenders"]
    )
    organization_services.update_tender_brief(
        organization_id=organization.pk,
        actor=user,
        categories=["software", "it_services"],
        keywords=["information system"],
        target_countries=["KE"],
    )
    agent = make_agent(organization, user, goal="tenders", opportunity_types=[], sources=[])
    response = auth_client.post(
        reverse(
            "v1:discovery:agent-run",
            kwargs={"organization_id": organization.pk, "agent_id": agent.pk},
        )
    )
    assert response.status_code == 202, response.data
    run = DiscoveryRun.objects.get(pk=response.data["id"])
    assert run.status == "succeeded", run.error

    tenders = Opportunity.objects.filter(organization=organization)
    assert tenders.exists()
    assert set(tenders.values_list("opportunity_type", flat=True)) <= {
        "tender",
        "rfp",
        "consultancy",
    }
    tender = tenders.first()
    assert tender.tender["reference"].startswith("SAMPLE/")

    detail = auth_client.get(
        reverse(
            "v1:opportunities:detail",
            kwargs={"organization_id": organization.pk, "opportunity_id": tender.pk},
        )
    )
    assert detail.data["tender"]["buyer"] == tender.funder
    assert detail.data["tender"]["documents"][0]["name"] == "Tender document (sample)"
