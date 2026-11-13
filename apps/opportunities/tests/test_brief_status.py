"""The opportunity says whether its brief is ready, still being prepared, or failed."""

import pytest

from apps.ai.testing import use_scripted_models
from apps.discovery.tests.factories import make_opportunity
from apps.opportunities.api.serializers import OpportunitySerializer
from apps.opportunities.tasks import analyze_opportunity

from .test_analyst import SAMPLE_BRIEF

pytestmark = pytest.mark.django_db


def status(opportunity):
    opportunity.refresh_from_db()
    return OpportunitySerializer(opportunity).data["brief_status"]


def test_a_new_opportunity_waits_for_its_brief(organization):
    assert status(make_opportunity(organization)) == "pending"


def test_a_failed_brief_is_recorded_and_a_later_success_clears_it(
    monkeypatch, settings, organization
):
    opportunity = make_opportunity(organization)

    def broken(prompt):
        raise RuntimeError("model down")

    use_scripted_models(monkeypatch, settings, {"opportunity_analyst": broken})
    analyze_opportunity(opportunity.pk)
    assert status(opportunity) == "failed"

    use_scripted_models(monkeypatch, settings, {"opportunity_analyst": lambda p: SAMPLE_BRIEF})
    analyze_opportunity(opportunity.pk)
    assert status(opportunity) == "ready"
    assert "brief_error" not in opportunity.requirements


def test_the_brief_reads_only_the_finding_runs_folders_and_says_which(
    monkeypatch, settings, user, organization
):
    from apps.discovery.models import DiscoveryRun
    from apps.discovery.tests.factories import make_agent
    from apps.knowledge.tests.test_search import add_document
    from apps.opportunities.services import generate_brief_for_opportunity

    proposals = organization.folders.create(name="Proposals", purpose="Past applications")
    reports = organization.folders.create(name="Reports", purpose="Audits")
    add_document(organization, "Pilot proposal", ["Data skills grant pilot"], folder=proposals)
    add_document(organization, "Audit", ["Data skills grant audit"], folder=reports)
    agent = make_agent(organization, user)
    run = DiscoveryRun.objects.create(
        organization=organization,
        agent=agent,
        agent_name=agent.name,
        trigger="manual",
        config={
            "knowledge": {"mode": "folders", "folder_ids": [proposals.pk]},
            "folders": [{"id": proposals.pk, "name": "Proposals", "purpose": ""}],
        },
    )
    opportunity = make_opportunity(organization, title="Data Skills Grant", run=run)
    prompts = []

    def analyst(prompt):
        prompts.append(prompt)
        return SAMPLE_BRIEF

    use_scripted_models(monkeypatch, settings, {"opportunity_analyst": analyst})
    generate_brief_for_opportunity(opportunity, force=True)

    assert "Pilot proposal" in prompts[0] and "Audit" not in prompts[0]
    opportunity.refresh_from_db()
    assert opportunity.requirements["brief_sources"] == {
        "mode": "folders",
        "folders": ["Proposals"],
        "documents": ["Pilot proposal"],
    }
    assert OpportunitySerializer(opportunity).data["brief_sources"]["documents"] == [
        "Pilot proposal"
    ]
