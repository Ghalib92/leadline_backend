"""The weekly digest email: the week's finds, what closes soon, who to call back."""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.discovery.tests.factories import make_opportunity
from apps.organizations.models import OrganizationMembership
from apps.outreach.models import DigestLog, LeadActivity, ReminderPreference
from apps.outreach.tasks import send_weekly_digests
from apps.proposals.models import Proposal

from .conftest import url

pytestmark = pytest.mark.django_db(transaction=True)


def due_in(days):
    return timezone.now() + timedelta(days=days)


@pytest.fixture
def week(organization, user):
    """A lead and a tender found this week, an approved bid closing in 5 days with a
    missing document, and a lead to call back tomorrow."""
    make_opportunity(
        organization,
        title="Savannah Lodge",
        opportunity_type="client",
        match_score=81,
        location="Nairobi",
    )
    make_opportunity(
        organization,
        title="Kwale dashboard",
        opportunity_type="tender",
        match_score=73,
        funder="Kwale County",
    )
    old = make_opportunity(organization, title="Old find", match_score=99)
    old.found_at = timezone.now() - timedelta(days=20)
    old.save()
    make_opportunity(organization, title="Rejected", match_score=95, status="dismissed")
    bid = make_opportunity(
        organization,
        title="Kilifi M&E system",
        opportunity_type="tender",
        status="pursuing",
        deadline=due_in(5),
    )
    Proposal.objects.create(
        organization=organization,
        opportunity=bid,
        checklist=[
            {"key": "r1", "status": "missing", "requirement": "Tax compliance", "done": False},
            {"key": "r2", "status": "missing", "requirement": "Permit", "done": True},
        ],
    )
    lead = make_opportunity(
        organization, title="Afya Clinic", opportunity_type="client", status="pursuing"
    )
    LeadActivity.objects.create(
        organization=organization,
        opportunity=lead,
        actor=user,
        channel="call",
        outcome="call_back",
        follow_up_on=timezone.localdate() + timedelta(days=1),
    )
    mail.outbox.clear()


@pytest.mark.usefixtures("week")
def test_members_get_the_week_once(organization, user, member):
    send_weekly_digests()
    assert sorted(m.to[0] for m in mail.outbox) == sorted([user.email, member.email])
    message = mail.outbox[0]
    assert message.subject == f"Your week at {organization.name} | Leadline"
    body = message.body
    # Best first; rejected and older finds are left out.
    assert body.index("Savannah Lodge") < body.index("Kwale dashboard")
    assert "client lead, 81% match · Nairobi" in body
    assert "Old find" not in body and "Rejected" not in body
    assert "Kilifi M&E system, closes in 5 days" in body
    assert "1 document(s) still missing" in body  # the ticked one is in hand
    assert "Afya Clinic, tomorrow" in body
    html = message.alternatives[0][0]
    assert "Kwale dashboard" in html and "/calls" in html

    send_weekly_digests()  # a retry, or a second tick the same day
    assert len(mail.outbox) == 2
    assert DigestLog.objects.count() == 2


@pytest.mark.usefixtures("week")
def test_members_can_turn_it_off(member_client, organization, user, member):
    path = url("reminders", organization)
    response = member_client.patch(path, {"weekly_digest": False}, format="json")
    assert response.data["weekly_digest"] is False
    assert ReminderPreference.objects.get(user=member).weekly_digest is False
    send_weekly_digests()
    assert [m.to for m in mail.outbox] == [[user.email]]


def test_a_quiet_week_sends_nothing(organization):
    send_weekly_digests()
    assert mail.outbox == []


@pytest.mark.usefixtures("week")
def test_no_digest_for_archived_organizations_or_inactive_members(organization, user, member):
    OrganizationMembership.objects.filter(user=member).update(
        status=OrganizationMembership.Status.REMOVED
    )
    send_weekly_digests()
    assert [m.to for m in mail.outbox] == [[user.email]]
    mail.outbox.clear()
    DigestLog.objects.all().delete()
    organization.archived_at = timezone.now()
    organization.save()
    send_weekly_digests()
    assert mail.outbox == []


@pytest.mark.usefixtures("week")
def test_the_greeting_uses_a_name_never_the_email(organization, user):
    send_weekly_digests()
    body = next(m.body for m in mail.outbox if m.to == [user.email])
    assert body.startswith("Hi there,") and user.email not in body.split("\n")[0]
