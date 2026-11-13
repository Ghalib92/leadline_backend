from decimal import Decimal

import pytest
from django.urls import reverse

from apps.ai.schemas.discovery import OpportunityBrief
from apps.ai.testing import use_scripted_models
from apps.discovery.tests.factories import make_agent, make_opportunity
from apps.opportunities import services
from apps.opportunities.api.serializers import OpportunitySerializer
from apps.opportunities.tasks import analyze_opportunity

pytestmark = pytest.mark.django_db(transaction=True)

SAMPLE_BRIEF = {
    "what_it_is": "A $100k grant supporting clean water access and infrastructure.",
    "why_it_fits": (
        "Directly matches Acme's track record in community water management in East Africa."
    ),
    "eligibility_checklist": [
        {"criterion": "Operating in Kenya", "meets": True, "notes": "Active operations in Kenya."},
        {"criterion": "Registered NGO", "meets": True, "notes": "Holds legal NGO registration."},
    ],
    "documents_needed": ["Technical Proposal", "Detailed Budget", "Audit Reports"],
    "deadline_plan": "Deadline is in 30 days; begin drafting narrative immediately.",
    "effort_estimate": "medium (2 weeks of staff time)",
    "next_step": "Confirm proposal lead and schedule internal kick-off.",
}


@pytest.fixture
def opportunity(user, organization):
    agent = make_agent(organization, user)
    return make_opportunity(
        organization,
        title="Clean Water Initiative",
        funder="Water Impact Fund",
        opportunity_type="grant",
        agent=agent,
        match_score=88,
        amount_min=Decimal("50000"),
        amount_max=Decimal("100000"),
        currency="USD",
        location="Kenya",
    )


@pytest.fixture
def analyst_model(monkeypatch, settings):
    calls = []

    def respond(prompt):
        calls.append(prompt)
        return SAMPLE_BRIEF

    use_scripted_models(monkeypatch, settings, {"opportunity_analyst": respond})
    return calls


def brief_url(organization, opportunity):
    return reverse(
        "v1:opportunities:brief",
        kwargs={"organization_id": organization.pk, "opportunity_id": opportunity.pk},
    )


def test_generate_brief_for_opportunity_uses_analyst_agent(opportunity, analyst_model):
    brief = services.generate_brief_for_opportunity(opportunity)
    assert isinstance(brief, OpportunityBrief)
    assert brief.what_it_is == SAMPLE_BRIEF["what_it_is"]
    assert len(analyst_model) == 1
    assert "Clean Water Initiative" in analyst_model[0]

    opportunity.refresh_from_db()
    assert "brief" in opportunity.requirements
    assert opportunity.requirements["brief"]["what_it_is"] == SAMPLE_BRIEF["what_it_is"]


def test_generate_brief_skips_when_already_present_unless_forced(opportunity, analyst_model):
    opportunity.requirements = {"brief": SAMPLE_BRIEF}
    opportunity.save()

    # Should not call analyst_model because brief is already present
    brief = services.generate_brief_for_opportunity(opportunity, force=False)
    assert brief.what_it_is == SAMPLE_BRIEF["what_it_is"]
    assert len(analyst_model) == 0

    # Force should trigger a new call
    brief = services.generate_brief_for_opportunity(opportunity, force=True)
    assert len(analyst_model) == 1


def test_analyze_opportunity_job(opportunity, analyst_model):
    assert "brief" not in opportunity.requirements
    analyze_opportunity(opportunity.pk)

    opportunity.refresh_from_db()
    assert "brief" in opportunity.requirements
    assert opportunity.requirements["brief"]["what_it_is"] == SAMPLE_BRIEF["what_it_is"]

    # Calling for non-existent pk handles cleanly without error
    analyze_opportunity("0000000000000000000000")


def test_opportunity_serializer_includes_brief(opportunity):
    serializer = OpportunitySerializer(opportunity)
    assert serializer.data["brief"] is None

    opportunity.requirements = {"brief": SAMPLE_BRIEF}
    opportunity.save()

    serializer = OpportunitySerializer(opportunity)
    assert serializer.data["brief"] is not None
    assert serializer.data["brief"]["what_it_is"] == SAMPLE_BRIEF["what_it_is"]
    assert len(serializer.data["brief"]["eligibility_checklist"]) == 2


def test_brief_api_get_and_post(auth_client, user, organization, opportunity, analyst_model):
    url = brief_url(organization, opportunity)

    # GET generates the brief on demand if not present
    res = auth_client.get(url)
    assert res.status_code == 200, res.data
    assert res.data["what_it_is"] == SAMPLE_BRIEF["what_it_is"]
    assert len(analyst_model) == 1

    # Second GET uses cached brief
    res2 = auth_client.get(url)
    assert res2.status_code == 200
    assert len(analyst_model) == 1

    # POST force-regenerates
    res3 = auth_client.post(url)
    assert res3.status_code == 200
    assert len(analyst_model) == 2

