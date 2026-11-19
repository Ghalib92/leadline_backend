"""Bids: proposals for tenders, with a compliance checklist and a deadline plan."""

import io
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from django.urls import reverse
from django.utils import timezone
from docx import Document
from rest_framework.test import APIClient

from apps.ai.config.loader import BidPlanSettings
from apps.authentication.tests.factories import UserFactory
from apps.discovery.tests.factories import make_opportunity
from apps.organizations.models import OrganizationDocument
from apps.proposals import selectors
from apps.proposals.bid_plan import bid_plan
from apps.proposals.models import Proposal

pytestmark = pytest.mark.django_db(transaction=True)


def url(name, organization, **kwargs):
    return reverse(f"v1:proposals:{name}", kwargs={"organization_id": organization.pk, **kwargs})


def approve(client, organization, opportunity):
    path = reverse(
        "v1:opportunities:reviews",
        kwargs={"organization_id": organization.pk, "opportunity_id": opportunity.pk},
    )
    assert client.post(path, {"decision": "approve"}, format="json").status_code == 201


@pytest.fixture
def certificate(organization):
    return OrganizationDocument.objects.create(
        organization=organization,
        folder=organization.folders.get_or_create(name="Compliance")[0],
        title="Certificate of Incorporation",
        category="registration",
        file="c.pdf",
        original_filename="c.pdf",
        size=1,
        processing_status="processed",
    )


@pytest.fixture
def tender(organization, certificate):
    return make_opportunity(
        organization,
        title="Investment dashboard",
        opportunity_type="tender",
        funder="County Government of Kwale",
        deadline=timezone.now() + timezone.timedelta(days=20),
        tender={"reference": "CGK-1", "bid_security": "50,000 KES", "documents": []},
    )


@pytest.fixture
def bid(auth_client, organization, tender):
    approve(auth_client, organization, tender)
    return Proposal.objects.get(opportunity=tender)


def detail(client, organization, proposal):
    return client.get(url("detail", organization, proposal_id=proposal.pk)).data


def test_approving_a_tender_writes_a_bid_with_a_checklist_and_plan(
    auth_client, organization, bid, certificate
):
    data = detail(auth_client, organization, bid)
    assert data["status"] == "ready"
    assert data["opportunity"]["opportunity_type"] == "tender"
    assert data["checked_at"] is not None
    first, second, third = data["checklist"]
    assert first["status"] == "ready" and first["code"] == "MR1"
    assert first["documents"] == [{"id": certificate.pk, "title": certificate.title}]
    assert second["status"] == "missing" and third["status"] == "prepare"
    assert not any(item["done"] for item in data["checklist"])

    plan = data["bid_plan"]
    assert plan["days_left"] in (19, 20) and not plan["closed"] and not plan["tight"]
    keys = [step["key"] for step in plan["steps"]]
    assert keys[0] == "clarifications" and keys[-1] == "closing"
    assert "bid_security" in keys  # the tender asks for one
    documents = next(step for step in plan["steps"] if step["key"] == "documents")
    assert documents["note"] == "1 missing: [Sample] Valid tax compliance certificate."
    assert documents["state"] == "upcoming"
    events = auth_client.get(url("events", organization, proposal_id=bid.pk)).data["events"]
    assert "checklist" in [event["step"] for event in events]


def test_grant_proposals_have_no_checklist_or_plan(auth_client, organization, certificate):
    grant = make_opportunity(organization, title="Grant", deadline=timezone.now())
    approve(auth_client, organization, grant)
    data = detail(auth_client, organization, Proposal.objects.get(opportunity=grant))
    assert data["checklist"] == [] and data["bid_plan"] is None
    assert selectors.writer_inputs(Proposal.objects.get(opportunity=grant))["documents"] == []


def test_writer_inputs_for_a_bid(bid, certificate):
    inputs = selectors.writer_inputs(bid)
    assert inputs["opportunity"]["tender"]["reference"] == "CGK-1"
    assert inputs["documents"] == [
        {
            "id": certificate.pk,
            "title": "Certificate of Incorporation",
            "folder": "Compliance",
            "category": "Registration or legal certificate",
            "readable": True,
        }
    ]


def test_ticking_items_and_keeping_ticks_across_rechecks_and_redrafts(
    auth_client, organization, bid
):
    tick = url("checklist-item", organization, proposal_id=bid.pk, key="r2")
    response = auth_client.patch(tick, {"done": True}, format="json")
    assert response.status_code == 200
    item = response.data["checklist"][1]
    assert item["done"] and item["done_at"]
    documents = next(s for s in response.data["bid_plan"]["steps"] if s["key"] == "documents")
    assert documents["state"] == "done" and documents["note"] == "Nothing marked missing."

    recheck = auth_client.post(url("checklist-recheck", organization, proposal_id=bid.pk))
    assert recheck.status_code == 202
    data = detail(auth_client, organization, bid)
    assert data["status"] == "ready" and data["job"]["kind"] == "checklist"
    assert [item["done"] for item in data["checklist"]] == [False, True, False]
    assert data["version"] == 1  # the text is not touched

    redraft = auth_client.post(
        url("for-opportunity", organization, opportunity_id=bid.opportunity_id)
    )
    assert redraft.status_code == 202
    data = detail(auth_client, organization, bid)
    assert data["version"] == 2
    assert [item["done"] for item in data["checklist"]] == [False, True, False]

    untick = auth_client.patch(tick, {"done": False}, format="json")
    assert untick.data["checklist"][1]["done"] is False


def test_checklist_errors(auth_client, organization, bid, user):
    missing = url("checklist-item", organization, proposal_id=bid.pk, key="r9")
    assert auth_client.patch(missing, {"done": True}, format="json").status_code == 400
    bad = url("checklist-item", organization, proposal_id=bid.pk, key="r1")
    assert auth_client.patch(bad, {}, format="json").status_code == 400

    grant = make_opportunity(organization, title="Grant")
    approve(auth_client, organization, grant)
    proposal = Proposal.objects.get(opportunity=grant)
    response = auth_client.post(url("checklist-recheck", organization, proposal_id=proposal.pk))
    assert response.status_code == 409 and response.data["code"] == "not_tender"

    Proposal.objects.filter(pk=bid.pk).update(checklist=[])
    response = auth_client.post(url("checklist-recheck", organization, proposal_id=bid.pk))
    assert response.status_code == 409 and response.data["code"] == "no_checklist"

    outsider = APIClient()
    outsider.force_authenticate(UserFactory())
    assert outsider.patch(bad, {"done": True}, format="json").status_code in (403, 404)


def test_export_adds_the_checklist(auth_client, organization, bid):
    auth_client.patch(
        url("checklist-item", organization, proposal_id=bid.pk, key="r1"),
        {"done": True},
        format="json",
    )
    response = auth_client.get(url("export", organization, proposal_id=bid.pk))
    document = Document(io.BytesIO(response.content))
    texts = [p.text for p in document.paragraphs]
    assert any("Buyer: County Government of Kwale" in t and "Tender no. CGK-1" in t for t in texts)
    assert "Compliance checklist (internal, remove before submitting)" in texts
    rows = [[cell.text for cell in row.cells] for row in document.tables[0].rows]
    assert rows[0] == ["Requirement", "Status", "Your documents", "In hand"]
    assert rows[1] == [
        "MR1: [Sample] Certificate of incorporation",
        "Ready",
        "Certificate of Incorporation",
        "Yes",
    ]
    assert rows[2][1] == "Missing" and rows[2][3] == ""


# ---------------------------------------------------------------------------
# The deadline plan (plain code)
# ---------------------------------------------------------------------------


def opportunity(closes, **tender):
    return SimpleNamespace(deadline=closes, tender=tender)


def nairobi(*args):
    return timezone.make_aware(datetime(*args))


def plan_for(closes, today, requirements=None, checklist=(), **tender):
    return bid_plan(
        opportunity(closes, **tender), requirements or {}, list(checklist), BidPlanSettings(), today
    )


def test_steps_count_back_from_closing_and_skip_weekends():
    # Closes Thursday 26 Nov 2026 at 10:00; today is Monday the 9th.
    plan = plan_for(nairobi(2026, 11, 26, 10, 0), date(2026, 11, 9))
    due = {step["key"]: step["due"] for step in plan["steps"]}
    assert due == {
        "clarifications": "2026-11-19",
        "draft": "2026-11-23",
        "review": "2026-11-24",
        "submit": "2026-11-25",
        "closing": "2026-11-26",
    }
    assert plan["days_left"] == 17 and not plan["tight"]
    closing = plan["steps"][-1]
    assert closing["time"] == "10:00" and closing["from_tender"]
    # Closing on a Monday: "5 days before" falls on a Wednesday, "1 day before" on
    # Sunday, which moves back to Friday.
    monday = plan_for(
        nairobi(2026, 11, 30, 11, 0), date(2026, 11, 2), checklist=[{"status": "ready"}]
    )
    due = {step["key"]: step["due"] for step in monday["steps"]}
    assert due["documents"] == "2026-11-25"
    assert due["submit"] == "2026-11-27"


def test_tender_dates_security_and_missing_documents():
    requirements = {
        "key_dates": [
            {
                "event": "Clarification requests close",
                "date": "2026-11-20",
                "kind": "clarification",
            },
            {"event": "Site visit", "date": "2026-11-12", "time": "09:00", "kind": "site_visit"},
            {"event": "Opening", "date": "2026-11-26", "kind": "opening"},  # not a step
            {"event": "Typo", "date": "2027-01-01", "kind": "meeting"},  # after closing
        ],
        "bid_security": "KES 2,500,000 bank guarantee",
        "submission": ["Upload on tenders.go.ke"],
    }
    checklist = [
        {"status": "missing", "requirement": "Tax compliance", "done": False},
        {"status": "missing", "requirement": "NCA licence", "done": True},
        {"status": "prepare", "kind": "security", "requirement": "Bid security", "done": True},
    ]
    plan = plan_for(nairobi(2026, 11, 26, 10, 0), date(2026, 11, 9), requirements, checklist)
    steps = {step["key"]: step for step in plan["steps"]}
    assert "clarifications" not in steps  # the tender sets its own date
    assert steps["tender_1"]["title"] == "Clarification requests close"
    assert steps["tender_2"]["time"] == "09:00" and steps["tender_2"]["due"] == "2026-11-12"
    assert len([key for key in steps if key.startswith("tender_")]) == 2
    assert steps["bid_security"]["note"] == "KES 2,500,000 bank guarantee."
    assert steps["bid_security"]["state"] == "done"
    assert steps["documents"]["note"] == "1 missing: Tax compliance."
    assert steps["submit"]["note"] == "Upload on tenders.go.ke"
    assert [step["due"] for step in plan["steps"]] == sorted(step["due"] for step in plan["steps"])


def test_a_tight_plan_and_a_closed_tender():
    tight = plan_for(nairobi(2026, 11, 19, 10, 0), date(2026, 11, 18))
    assert tight["tight"] and tight["days_left"] == 1
    due = {step["key"]: (step["due"], step["state"]) for step in tight["steps"]}
    assert due["draft"] == ("2026-11-18", "today")
    assert due["submit"] == ("2026-11-18", "today")
    assert due["closing"] == ("2026-11-19", "upcoming")

    closed = plan_for(nairobi(2020, 1, 10, 10, 0), date(2026, 11, 18))
    assert closed["closed"] and not closed["tight"]
    assert all(step["state"] == "past" for step in closed["steps"])


def test_closing_time_from_the_portal_and_unknown():
    plan = plan_for(None, date(2026, 11, 9), closes_at="2026-11-26T10:00:00+03:00")
    assert plan["closes_at"].startswith("2026-11-26T10:00:00")
    # A date-only deadline reads as midnight; the portal's time wins.
    both = plan_for(
        nairobi(2026, 11, 26, 0, 0), date(2026, 11, 9), closes_at="2026-11-26T10:00:00+03:00"
    )
    assert both["steps"][-1]["time"] == "10:00"
    assert plan_for(None, date(2026, 11, 9)) is None


def test_a_long_missing_list_is_shortened():
    checklist = [
        {"status": "missing", "requirement": f"Requirement {n}. " + "word " * 30} for n in range(5)
    ]
    plan = plan_for(nairobi(2026, 11, 26, 10, 0), date(2026, 11, 9), checklist=checklist)
    note = next(step for step in plan["steps"] if step["key"] == "documents")["note"]
    assert note.startswith("5 missing: Requirement 0. word word")
    assert note.endswith("…; and 2 more.")
    assert ".;" not in note and len(note) < 300


def test_approving_a_job_writes_no_proposal(auth_client, organization):
    job = make_opportunity(organization, title="Backend developer", opportunity_type="job")
    approve(auth_client, organization, job)
    assert not Proposal.objects.filter(opportunity=job).exists()
    response = auth_client.post(url("for-opportunity", organization, opportunity_id=job.pk))
    assert response.status_code == 409 and response.data["code"] == "job"
