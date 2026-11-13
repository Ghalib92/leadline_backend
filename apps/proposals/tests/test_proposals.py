"""Proposals through the API and the worker, with the fake writer."""

import io
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from docx import Document
from rest_framework.test import APIClient

from apps.ai.graphs.proposal_runner import ProposalResult, SectionRewriteFailed
from apps.authentication.tests.factories import UserFactory
from apps.discovery.tests.factories import make_opportunity
from apps.organizations import services as organization_services
from apps.proposals import services
from apps.proposals.models import Proposal

# Jobs are queued on commit and, with JOBS_RUN_INLINE, run inline.
pytestmark = pytest.mark.django_db(transaction=True)


def url(name, organization, **kwargs):
    return reverse(f"v1:proposals:{name}", kwargs={"organization_id": organization.pk, **kwargs})


def as_user(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def approve(client, organization, opportunity):
    path = reverse(
        "v1:opportunities:reviews",
        kwargs={"organization_id": organization.pk, "opportunity_id": opportunity.pk},
    )
    response = client.post(path, {"decision": "approve"}, format="json")
    assert response.status_code == 201, response.data


@pytest.fixture
def opportunity(organization):
    return make_opportunity(organization, title="Youth Data Fund", deadline=timezone.now())


@pytest.fixture
def proposal(auth_client, organization, opportunity):
    approve(auth_client, organization, opportunity)
    return Proposal.objects.get(opportunity=opportunity)


def detail(client, organization, proposal):
    return client.get(url("detail", organization, proposal_id=proposal.pk))


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------


def test_approval_writes_the_first_draft(auth_client, organization, opportunity, proposal):
    response = auth_client.get(url("for-opportunity", organization, opportunity_id=opportunity.pk))
    assert response.status_code == 200
    data = response.data
    assert data["status"] == "ready" and data["version"] == 1
    assert data["opportunity"]["title"] == "Youth Data Fund"
    assert [s["key"] for s in data["sections"]][:2] == ["summary", "problem"]
    assert data["sections"][0]["content"].startswith("[Sample]")
    assert data["sections"][0]["evidence"][0]["n"] == 1
    assert data["requirements"]["documents_required"] == ["Budget", "Registration certificate"]
    assert data["review"]["score"] == 62
    assert data["job"]["kind"] == "draft"

    events = auth_client.get(url("events", organization, proposal_id=proposal.pk)).data
    assert events["status"] == "ready"
    steps = [event["step"] for event in events["events"]]
    assert steps[0] == "requirements" and steps[-1] == "review"
    later = auth_client.get(
        url("events", organization, proposal_id=proposal.pk), {"after": events["last_seq"]}
    ).data
    assert later["events"] == [] and later["last_seq"] == events["last_seq"]


def test_no_proposal_until_approved(auth_client, organization, opportunity):
    path = url("for-opportunity", organization, opportunity_id=opportunity.pk)
    assert auth_client.get(path).status_code == 404
    response = auth_client.post(path)
    assert response.status_code == 409 and response.data["code"] == "not_approved"


def test_approving_again_keeps_the_existing_proposal(
    auth_client, organization, opportunity, proposal
):
    path = reverse(
        "v1:opportunities:reviews",
        kwargs={"organization_id": organization.pk, "opportunity_id": opportunity.pk},
    )
    auth_client.post(
        path, {"decision": "reject", "reason": "capacity", "comment": "Later"}, format="json"
    )
    approve(auth_client, organization, opportunity)
    proposal.refresh_from_db()
    assert proposal.version == 1  # not redrafted


def test_redraft_from_scratch_keeps_history(auth_client, organization, opportunity, proposal):
    response = auth_client.post(url("for-opportunity", organization, opportunity_id=opportunity.pk))
    assert response.status_code == 202
    assert detail(auth_client, organization, proposal).data["version"] == 2
    versions = auth_client.get(url("versions", organization, proposal_id=proposal.pk)).data
    assert [(v["number"], v["source"]) for v in versions] == [(2, "ai_draft"), (1, "ai_draft")]
    assert versions[0]["created_by"]["id"] == organization.created_by.pk
    assert versions[1]["created_by"] is None  # drafted automatically on approval


def test_busy_proposals_refuse_new_work(auth_client, organization, opportunity, proposal):
    Proposal.objects.filter(pk=proposal.pk).update(status="drafting", job_started_at=timezone.now())
    response = auth_client.post(url("for-opportunity", organization, opportunity_id=opportunity.pk))
    assert response.status_code == 409 and response.data["code"] == "busy"
    edit = auth_client.patch(
        url("section", organization, proposal_id=proposal.pk, key="summary"),
        {"base_version": 1, "content": "x"},
        format="json",
    )
    assert edit.status_code == 409
    # A job whose worker died long ago no longer blocks.
    Proposal.objects.filter(pk=proposal.pk).update(
        job_started_at=timezone.now() - timedelta(hours=2)
    )
    response = auth_client.post(url("for-opportunity", organization, opportunity_id=opportunity.pk))
    assert response.status_code == 202


def test_a_stale_worker_cannot_overwrite_a_newer_job(
    auth_client, organization, opportunity, proposal
):
    with patch("apps.proposals.services.transaction.on_commit"):  # do not run the jobs
        services.start_draft(
            organization_id=organization.pk,
            opportunity_id=opportunity.pk,
            actor=organization.created_by,
        )
        first = services.claim_job(proposal.pk)
        Proposal.objects.filter(pk=proposal.pk).update(
            job_started_at=timezone.now() - timedelta(hours=2)
        )
        services.start_draft(
            organization_id=organization.pk,
            opportunity_id=opportunity.pk,
            actor=organization.created_by,
        )
        second = services.claim_job(proposal.pk)
    result = ProposalResult(sections=[{"key": "a", "title": "A", "content": "late"}])
    assert services.save_draft(proposal.pk, first.job_started_at, result) is False
    assert services.save_draft(proposal.pk, second.job_started_at, result) is True
    proposal.refresh_from_db()
    assert proposal.version == 2 and proposal.status == "ready"


def test_a_failing_writer_fails_the_job_but_keeps_versions(
    auth_client, organization, opportunity, proposal
):
    with patch(
        "apps.ai.graphs.fake_proposal.FakeProposalWriter.draft", side_effect=RuntimeError("boom")
    ):
        auth_client.post(url("for-opportunity", organization, opportunity_id=opportunity.pk))
    data = detail(auth_client, organization, proposal).data
    assert data["status"] == "failed"
    assert data["error"] == "Writing stopped because of an unexpected error."
    assert data["version"] == 1 and data["sections"]  # the earlier draft is still there


def test_missing_configuration_is_explained(
    auth_client, organization, opportunity, proposal, settings
):
    settings.PROPOSAL_RUNNER = "graph"
    settings.GROQ_API_KEY = ""
    settings.AI_LLM_PROVIDER = "groq"
    auth_client.post(url("for-opportunity", organization, opportunity_id=opportunity.pk))
    data = detail(auth_client, organization, proposal).data
    assert data["status"] == "failed"
    assert data["error"] == "The Proposal Writer is not configured on the server yet."


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------


def test_member_edits_a_section(organization, member, proposal):
    client = as_user(member)
    path = url("section", organization, proposal_id=proposal.pk, key="budget")
    response = client.patch(
        path,
        {
            "base_version": 1,
            "title": "Budget and value for money",
            "content": "We will spend **wisely**.",
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["version"] == 2
    budget = next(s for s in response.data["sections"] if s["key"] == "budget")
    assert budget["title"] == "Budget and value for money"
    assert budget["content"] == "We will spend **wisely**."
    versions = client.get(url("versions", organization, proposal_id=proposal.pk)).data
    assert versions[0]["source"] == "edit"
    assert versions[0]["note"] == "Edited “Budget and value for money”"
    assert versions[0]["created_by"]["id"] == member.pk


def test_edits_on_a_stale_version_conflict(auth_client, organization, proposal):
    path = url("section", organization, proposal_id=proposal.pk, key="budget")
    assert (
        auth_client.patch(path, {"base_version": 1, "content": "A"}, format="json").status_code
        == 200
    )
    response = auth_client.patch(path, {"base_version": 1, "content": "B"}, format="json")
    assert response.status_code == 409 and response.data["code"] == "version_conflict"


def test_edit_validation(auth_client, organization, proposal):
    path = url("section", organization, proposal_id=proposal.pk, key="budget")
    assert auth_client.patch(path, {"base_version": 1}, format="json").status_code == 400
    missing = url("section", organization, proposal_id=proposal.pk, key="nope")
    response = auth_client.patch(missing, {"base_version": 1, "content": "x"}, format="json")
    assert response.status_code == 400
    # An edit that changes nothing makes no new version.
    current = detail(auth_client, organization, proposal).data
    budget = next(s for s in current["sections"] if s["key"] == "budget")
    same = {"base_version": 1, "content": budget["content"]}
    assert auth_client.patch(path, same, format="json").data["version"] == 1


def test_ai_rewrites_one_section_with_instructions(auth_client, organization, proposal):
    path = url("section-rewrite", organization, proposal_id=proposal.pk, key="problem")
    response = auth_client.post(
        path, {"instructions": "Shorter, cite the 2025 survey"}, format="json"
    )
    assert response.status_code == 202
    data = detail(auth_client, organization, proposal).data
    assert data["version"] == 2 and data["job"]["kind"] == "section"
    problem = next(s for s in data["sections"] if s["key"] == "problem")
    assert "as asked: Shorter, cite the 2025 survey" in problem["content"]
    others = [s for s in data["sections"] if s["key"] != "problem"]
    assert all("as asked" not in s["content"] for s in others)
    versions = auth_client.get(url("versions", organization, proposal_id=proposal.pk)).data
    assert versions[0]["note"] == "AI rewrote “Problem and need”: Shorter, cite the 2025 survey"
    missing = url("section-rewrite", organization, proposal_id=proposal.pk, key="nope")
    assert auth_client.post(missing, {}, format="json").status_code == 400


def test_a_failed_rewrite_keeps_the_text(auth_client, organization, proposal):
    with patch(
        "apps.ai.graphs.fake_proposal.FakeProposalWriter.rewrite_section",
        side_effect=SectionRewriteFailed("“Budget” could not be rewritten: x."),
    ):
        auth_client.post(
            url("section-rewrite", organization, proposal_id=proposal.pk, key="budget"),
            {},
            format="json",
        )
    data = detail(auth_client, organization, proposal).data
    assert (data["status"], data["error"]) == ("failed", "“Budget” could not be rewritten: x.")
    assert data["version"] == 1


def test_restore_an_earlier_version(auth_client, organization, proposal):
    path = url("section", organization, proposal_id=proposal.pk, key="summary")
    auth_client.patch(path, {"base_version": 1, "content": "Edited"}, format="json")
    restore = url("version-restore", organization, proposal_id=proposal.pk, number=1)
    response = auth_client.post(restore)
    assert response.status_code == 201 and response.data["version"] == 3
    assert response.data["sections"][0]["content"].startswith("[Sample]")
    again = url("version-restore", organization, proposal_id=proposal.pk, number=3)
    assert auth_client.post(again).status_code == 409
    old = auth_client.get(url("version", organization, proposal_id=proposal.pk, number=2)).data
    assert old["sections"][0]["content"] == "Edited"


# ---------------------------------------------------------------------------
# Export and access
# ---------------------------------------------------------------------------


def test_export_docx(auth_client, organization, proposal):
    path = url("section", organization, proposal_id=proposal.pk, key="summary")
    auth_client.patch(
        path,
        {
            "base_version": 1,
            "content": (
                "Intro **bold** and *italic*.\nSame paragraph.\n- first point\n1. step one\n\n"
                "## Sub heading\nClosing."
            ),
        },
        format="json",
    )
    response = auth_client.get(url("export", organization, proposal_id=proposal.pk))
    assert response.status_code == 200
    assert response["Content-Disposition"] == 'attachment; filename="youth-data-fund-v2.docx"'
    document = Document(io.BytesIO(response.content))
    texts = [(p.style.name, p.text) for p in document.paragraphs]
    assert ("Title", "Youth Data Fund") in texts
    assert ("Heading 1", "Executive summary") in texts
    assert ("Normal", "Intro bold and italic. Same paragraph.") in texts
    assert ("List Bullet", "first point") in texts
    assert ("List Number", "step one") in texts
    assert ("Heading 3", "Sub heading") in texts
    bold = next(p for p in document.paragraphs if p.text.startswith("Intro"))
    assert [run.text for run in bold.runs if run.bold] == ["bold"]
    assert any(p.text.startswith("Sources") for p in document.paragraphs)

    first = auth_client.get(url("export", organization, proposal_id=proposal.pk), {"version": 1})
    assert "v1.docx" in first["Content-Disposition"]
    bad = auth_client.get(url("export", organization, proposal_id=proposal.pk), {"version": 9})
    assert bad.status_code == 400


def test_access(auth_client, user, organization, opportunity, proposal):
    outsider = as_user(UserFactory())
    assert detail(outsider, organization, proposal).status_code == 404
    assert outsider.get(url("list", organization)).status_code == 404
    listing = auth_client.get(url("list", organization)).data
    assert [row["id"] for row in listing["results"]] == [proposal.pk]
    other = organization_services.create_organization(actor=user, name="Other")
    assert auth_client.get(url("detail", other, proposal_id=proposal.pk)).status_code == 404
    organization_services.archive_organization(organization_id=organization.pk, actor=user)
    path = url("section", organization, proposal_id=proposal.pk, key="summary")
    response = auth_client.patch(path, {"base_version": 1, "content": "x"}, format="json")
    assert response.status_code == 400
    assert detail(auth_client, organization, proposal).status_code == 200  # still readable
