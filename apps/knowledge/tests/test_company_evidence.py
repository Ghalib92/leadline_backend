"""Passages from the company's documents that the judge and the brief writer read,
limited to the documents the run may use."""

import pytest

from apps.ai.prompts.loader import load_prompt
from apps.ai.tools.knowledge import company_evidence
from apps.knowledge.scope import KnowledgeScope
from apps.knowledge.tests.test_search import add_document

pytestmark = pytest.mark.django_db

FACTS = {"title": "Water harvesting grant", "summary": "Funds rural water pilots"}


@pytest.fixture
def folders(organization):
    proposals = organization.folders.create(name="Proposals", purpose="Past applications")
    reports = organization.folders.create(name="Reports", purpose="Audits")
    add_document(organization, "Pilot proposal", ["Water harvesting pilot"], folder=proposals)
    add_document(organization, "Audit", ["Water harvesting audit"], folder=reports)
    return proposals, reports


def test_evidence_comes_only_from_the_runs_folders(organization, folders):
    proposals, _ = folders
    text = company_evidence(organization.pk, FACTS, KnowledgeScope.of_folders([proposals.pk]))
    assert 'source="Pilot proposal' in text
    assert "Audit" not in text


def test_a_run_without_documents_says_so(organization, folders):
    text = company_evidence(organization.pk, FACTS, KnowledgeScope.none())
    assert text == "This search was set to use no company documents."


def test_the_latest_judge_and_analyst_prompts_take_evidence():
    assert "company_evidence" in load_prompt("judge").variables
    assert "company_evidence" in load_prompt("opportunity_analyst").variables
