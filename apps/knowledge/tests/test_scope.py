"""Which company documents an AI job may read: all of them, some folders, or none."""

import pytest

from apps.knowledge import embeddings
from apps.knowledge.scope import KnowledgeScope
from apps.knowledge.selectors import search_knowledge
from apps.organizations import services as organization_services

from .test_search import add_document

pytestmark = pytest.mark.django_db


@pytest.fixture
def folders(user, organization):
    proposals = organization.folders.create(name="Proposals", purpose="Past applications")
    reports = organization.folders.create(name="Reports", purpose="Audited reports")
    add_document(organization, "Proposal", ["Water harvesting pilot"], folder=proposals)
    add_document(organization, "Report", ["Water harvesting audit"], folder=reports)
    other = organization_services.create_organization(actor=user, name="Other org")
    add_document(other, "Theirs", ["Water harvesting pilot, theirs"])
    return proposals, reports


def titles(organization, scope):
    return sorted(
        {h.document_title for h in search_knowledge(organization.pk, "water", scope=scope)}
    )


def test_all_covers_every_processed_document_of_the_organization(organization, folders):
    assert titles(organization, KnowledgeScope.all()) == ["Proposal", "Report"]


def test_folders_never_reach_another_folder_or_organization(organization, folders):
    proposals, _ = folders
    assert titles(organization, KnowledgeScope.of_folders([proposals.pk])) == ["Proposal"]


def test_none_reads_nothing_and_never_embeds(monkeypatch, organization, folders):
    def boom():
        raise AssertionError("embedded a query for a scope with no documents")

    monkeypatch.setattr("apps.knowledge.selectors.get_embedder", boom)
    assert search_knowledge(organization.pk, "water", scope=KnowledgeScope.none()) == []
    assert search_knowledge(organization.pk, "water", scope=KnowledgeScope.of_folders([])) == []
    assert embeddings  # imported for the patch target's module to be loaded


@pytest.mark.parametrize(
    "stored, expected",
    [
        (None, KnowledgeScope.all()),
        ({"mode": "all"}, KnowledgeScope.all()),
        ({"mode": "none"}, KnowledgeScope.none()),
        ({"mode": "folders", "folder_ids": ["a", "b"]}, KnowledgeScope.of_folders(["a", "b"])),
        ({"mode": "nonsense"}, KnowledgeScope.all()),
    ],
)
def test_a_stored_scope_round_trips(stored, expected):
    scope = KnowledgeScope.from_config(stored)
    assert scope == expected
    assert KnowledgeScope.from_config(scope.to_config()) == scope
