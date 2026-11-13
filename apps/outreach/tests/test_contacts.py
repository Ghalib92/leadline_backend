"""Contacts through the API, and the contact search worker with a stubbed lead scout."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.ai.exceptions import AIConfigError
from apps.ai.graphs import lead_scout
from apps.ai.graphs.lead_scout import LeadResult
from apps.authentication.tests.factories import UserFactory
from apps.discovery.tests.factories import make_opportunity
from apps.outreach.models import Contact, ContactSearch, OutreachEmail

from .conftest import as_user, url

# Searches are queued on commit and, with JOBS_RUN_INLINE, run inline.
pytestmark = pytest.mark.django_db(transaction=True)


def found(email, **overrides):
    return {
        "name": "Amina Otieno",
        "role": "Programme Officer",
        "email": email,
        "phone": "",
        "kind": "person",
        "relevance": "Named as the contact for the call.",
        "confidence": 80,
        "source": "page",
        "source_url": "https://northbridge.org/contact",
        "verified": False,
        **overrides,
    }


@pytest.fixture
def scout(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return fake.result

    fake.result = LeadResult(contacts=[])
    fake.calls = calls
    monkeypatch.setattr(lead_scout, "find_contacts", fake)
    return fake


# ---------------------------------------------------------------------------
# Contacts by hand
# ---------------------------------------------------------------------------


def test_member_adds_edits_and_lists_contacts(member_client, organization, opportunity):
    contacts = url("contacts", organization, opportunity_id=opportunity.pk)
    response = member_client.post(
        contacts, {"name": "Grants team", "email": "grants@northbridge.org", "kind": "inbox"}
    )
    assert response.status_code == 201, response.data
    data = response.data
    assert data["source"] == "manual" and data["confidence"] == 100
    assert data["can_email"] is True and data["opportunity_id"] == opportunity.pk

    duplicate = member_client.post(contacts, {"email": "GRANTS@northbridge.org"})
    assert duplicate.status_code == 409 and duplicate.data["code"] == "duplicate"
    empty = member_client.post(contacts, {"role": "Someone"})
    assert empty.status_code == 400

    detail = url("contact", organization, contact_id=data["id"])
    edited = member_client.patch(detail, {"do_not_contact": True, "notes": "Asked us not to."})
    assert edited.status_code == 200
    assert edited.data["do_not_contact"] is True and edited.data["can_email"] is False

    listed = member_client.get(contacts)
    assert [contact["id"] for contact in listed.data] == [data["id"]]


def test_deleting_a_contact_discards_their_unsent_drafts(
    auth_client, organization, opportunity, user
):
    contact = Contact.objects.create(
        organization=organization, opportunity=opportunity, email="a@b.org", source="manual"
    )
    draft = OutreachEmail.objects.create(
        organization=organization,
        opportunity=opportunity,
        contact=contact,
        to_email="a@b.org",
        purpose="question",
        status="draft",
        subject="Hi",
        body="Hello",
    )
    response = auth_client.delete(url("contact", organization, contact_id=contact.pk))
    assert response.status_code == 204
    assert not Contact.objects.filter(pk=contact.pk).exists()
    draft.refresh_from_db()
    assert draft.status == "discarded" and draft.to_email == "a@b.org"


def test_outsiders_see_nothing(organization, opportunity):
    outsider = as_user(UserFactory())
    response = outsider.get(url("contacts", organization, opportunity_id=opportunity.pk))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Contact search (lead scout)
# ---------------------------------------------------------------------------


def test_contact_search_saves_new_contacts(member_client, organization, opportunity, scout):
    Contact.objects.create(
        organization=organization,
        opportunity=opportunity,
        email="amina@northbridge.org",
        source="manual",
    )
    scout.result = LeadResult(
        contacts=[
            found("Amina@northbridge.org"),  # already a contact
            found("grants@northbridge.org", name="", kind="inbox", role="x" * 400),
        ],
        warnings=["Could not read devex.com: blocked"],
        prompt_versions={"lead_scout": "lead_scout/v1"},
        models_used={"lead_scout": "test-research"},
        usage={"pages": 2},
    )
    path = url("contact-search", organization, opportunity_id=opportunity.pk)
    assert member_client.get(path).status_code == 404

    response = member_client.post(path)
    assert response.status_code == 202, response.data
    assert response.data["status"] == "done" and response.data["found"] == 1
    assert response.data["warning"] == "Could not read devex.com: blocked"
    assert scout.calls[0]["title"] == "Youth Data Fund"
    assert scout.calls[0]["source_url"] == opportunity.source_url

    inbox = opportunity.contacts.get(email="grants@northbridge.org")
    assert inbox.source == "page" and inbox.kind == "inbox" and inbox.created_by is None
    assert len(inbox.role) == 150  # cut to the column
    search = ContactSearch.objects.get()
    assert search.prompt_versions == {"lead_scout": "lead_scout/v1"}
    assert member_client.get(path).data["id"] == search.pk


def test_contact_search_needs_an_approved_opportunity(member_client, organization, scout):
    opportunity = make_opportunity(organization, title="Not yet")
    response = member_client.post(
        url("contact-search", organization, opportunity_id=opportunity.pk)
    )
    assert response.status_code == 409 and response.data["code"] == "not_approved"
    assert scout.calls == []


def test_one_contact_search_at_a_time(member_client, organization, opportunity, scout, settings):
    path = url("contact-search", organization, opportunity_id=opportunity.pk)
    running = ContactSearch.objects.create(
        organization=organization,
        opportunity=opportunity,
        status="running",
        started_at=timezone.now(),
    )
    response = member_client.post(path)
    assert response.status_code == 409 and response.data["code"] == "busy"

    # A search whose worker stopped responding is replaced.
    stale = timezone.now() - timedelta(minutes=settings.OUTREACH_JOB_STALE_MINUTES + 1)
    ContactSearch.objects.filter(pk=running.pk).update(started_at=stale)
    assert member_client.post(path).status_code == 202
    running.refresh_from_db()
    assert running.status == "failed" and running.error == "Stopped responding."


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (AIConfigError("no key"), "The AI is not configured on the server yet."),
        (RuntimeError("boom"), "Looking for contacts failed."),
    ],
)
def test_contact_search_failures_are_reported(
    member_client, organization, opportunity, monkeypatch, error, message
):
    def broken(**kwargs):
        raise error

    monkeypatch.setattr(lead_scout, "find_contacts", broken)
    response = member_client.post(
        url("contact-search", organization, opportunity_id=opportunity.pk)
    )
    assert response.data["status"] == "failed" and response.data["error"] == message
