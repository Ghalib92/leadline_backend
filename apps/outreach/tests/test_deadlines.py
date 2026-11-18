"""Deadline calendar, .ics files, reminder preferences and the daily reminder job."""

from datetime import timedelta

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.utils import timezone

from apps.authentication.tests.factories import UserFactory
from apps.discovery.tests.factories import make_opportunity
from apps.opportunities.models import OpportunityReview
from apps.organizations.models import OrganizationMembership
from apps.outreach.calendar import deadlines_ics
from apps.outreach.models import ReminderLog, ReminderPreference
from apps.outreach.tasks import send_deadline_reminders

from .conftest import url

pytestmark = pytest.mark.django_db


def due_in(days, hour=17):
    """A deadline `days` local days from today, at `hour` local time."""
    today = timezone.localtime()
    return today.replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days)


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def test_deadline_list_covers_pursued_and_saved(member_client, organization):
    pursued = make_opportunity(organization, title="Pursued", status="pursuing", deadline=due_in(3))
    saved = make_opportunity(organization, title="Saved", saved=True, deadline=due_in(10))
    make_opportunity(organization, title="Not saved", deadline=due_in(5))
    make_opportunity(organization, title="Dismissed", status="dismissed", deadline=due_in(5))
    make_opportunity(organization, title="Far", status="pursuing", deadline=due_in(200))

    response = member_client.get(url("deadlines", organization))
    assert response.status_code == 200
    assert [item["id"] for item in response.data] == [pursued.pk, saved.pk]
    assert response.data[0]["days_left"] == 3

    only_pursued = member_client.get(url("deadlines", organization), {"saved": "false"})
    assert [item["id"] for item in only_pursued.data] == [pursued.pk]
    later = (timezone.localdate() + timedelta(days=250)).isoformat()
    wide = member_client.get(url("deadlines", organization), {"to": later})
    assert len(wide.data) == 3


def test_deadline_window_is_validated(member_client, organization):
    path = url("deadlines", organization)
    assert member_client.get(path, {"from": "31-10-2026"}).status_code == 400
    assert member_client.get(path, {"from": "2026-10-10", "to": "2026-10-01"}).status_code == 400
    assert member_client.get(path, {"from": "2026-01-01", "to": "2027-06-01"}).status_code == 400


def test_calendar_file(member_client, organization):
    make_opportunity(
        organization,
        title="Youth Data Fund, round 2; " + "very long title " * 6,
        status="pursuing",
        deadline=due_in(3),
    )
    response = member_client.get(url("calendar", organization))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/calendar")
    assert "acme-africa-deadlines.ics" in response["Content-Disposition"]
    text = response.content.decode()
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert text.count("BEGIN:VEVENT") == 1
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    unfolded = text.replace("\r\n ", "")
    assert "SUMMARY:Deadline: Youth Data Fund\\, round 2\\; very long" in unfolded
    day = timezone.localdate() + timedelta(days=3)
    assert f"DTSTART;VALUE=DATE:{day:%Y%m%d}" in unfolded


def test_one_opportunity_calendar_file(member_client, organization, opportunity):
    response = member_client.get(
        url("opportunity-calendar", organization, opportunity_id=opportunity.pk)
    )
    assert response.status_code == 200
    assert f"UID:deadline-{opportunity.pk}@leadline" in response.content.decode()

    undated = make_opportunity(organization, title="No date", status="pursuing")
    missing = member_client.get(
        url("opportunity-calendar", organization, opportunity_id=undated.pk)
    )
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# Reminder preferences
# ---------------------------------------------------------------------------


def test_reminder_preferences(member_client, organization, member):
    path = url("reminders", organization)
    assert member_client.get(path).data == {
        "enabled": True,
        "days_before": [14, 3, 1],
        "weekly_digest": True,
    }

    response = member_client.patch(path, {"days_before": [1, 7, 7]}, format="json")
    assert response.status_code == 200 and response.data["days_before"] == [7, 1]
    assert member_client.patch(path, {"days_before": [0]}, format="json").status_code == 400
    assert member_client.patch(path, {"days_before": [99]}, format="json").status_code == 400

    member_client.patch(path, {"enabled": False}, format="json")
    assert member_client.get(path).data == {
        "enabled": False,
        "days_before": [7, 1],
        "weekly_digest": True,
    }
    assert ReminderPreference.objects.get(user=member).days_before == [7, 1]


# ---------------------------------------------------------------------------
# The daily reminder job
# ---------------------------------------------------------------------------


def test_admins_get_each_reminder_once(organization, user, member):
    opportunity = make_opportunity(
        organization, title="Youth Data Fund", status="pursuing", deadline=due_in(3)
    )
    make_opportunity(organization, title="Not due", status="pursuing", deadline=due_in(5))
    make_opportunity(organization, title="Only saved", saved=True, deadline=due_in(3))

    send_deadline_reminders()
    assert [message.to for message in mail.outbox] == [[user.email]]  # admins only
    message = mail.outbox[0]
    assert message.subject.startswith("Deadline in 3 days: Youth Data Fund")
    assert "is in 3 days" in message.body
    assert f"/opportunities/{opportunity.pk}" in message.body
    filename, content, mimetype = message.attachments[0]
    assert filename == f"deadline-{opportunity.pk}.ics" and mimetype == "text/calendar"
    assert "BEGIN:VEVENT" in content  # Django decodes text/* attachments

    send_deadline_reminders()  # a rerun the same day sends nothing new
    assert len(mail.outbox) == 1
    assert ReminderLog.objects.count() == 1


def test_whoever_approved_it_is_reminded(organization, member):
    opportunity = make_opportunity(
        organization, title="Youth Data Fund", status="pursuing", deadline=due_in(1)
    )
    OpportunityReview.objects.create(
        organization=organization,
        opportunity=opportunity,
        reviewer=member,
        decision="approve",
        status_before="new",
        match_score=70,
    )
    send_deadline_reminders()
    recipients = sorted(message.to[0] for message in mail.outbox)
    assert member.email in recipients and len(recipients) == 2
    assert mail.outbox[0].subject.startswith("Deadline tomorrow")


def test_preferences_and_moved_deadlines(organization, user):
    opportunity = make_opportunity(
        organization, title="Youth Data Fund", status="pursuing", deadline=due_in(7)
    )
    send_deadline_reminders()
    assert mail.outbox == []  # 7 is not a default reminder day

    ReminderPreference.objects.create(organization=organization, user=user, days_before=[7])
    send_deadline_reminders()
    assert len(mail.outbox) == 1

    opportunity.deadline = due_in(7, hour=9)  # a new time on the same day: no repeat
    opportunity.save()
    send_deadline_reminders()
    assert len(mail.outbox) == 1

    ReminderPreference.objects.filter(user=user).update(enabled=False)
    make_opportunity(organization, title="Another", status="pursuing", deadline=due_in(7))
    send_deadline_reminders()
    assert len(mail.outbox) == 1


def test_a_moved_deadline_is_reminded_again(organization, user):
    opportunity = make_opportunity(
        organization, title="Youth Data Fund", status="pursuing", deadline=due_in(3)
    )
    send_deadline_reminders()
    ReminderLog.objects.update(deadline=timezone.localdate() + timedelta(days=10))  # old date
    send_deadline_reminders()
    assert len(mail.outbox) == 2
    assert ReminderLog.objects.filter(opportunity=opportunity).count() == 2


def test_no_reminders_for_archived_organizations_or_inactive_admins(organization, user):
    make_opportunity(organization, title="Youth Data Fund", status="pursuing", deadline=due_in(3))
    other_admin = UserFactory()
    OrganizationMembership.objects.create(
        organization=organization,
        user=other_admin,
        role="admin",
        status=OrganizationMembership.Status.INACTIVE,
    )
    organization.archived_at = timezone.now()
    organization.save()
    send_deadline_reminders()
    assert mail.outbox == []


def test_calendar_text_is_escaped(organization):
    opportunity = make_opportunity(
        organization, title="A; B, C\\D\nE", status="pursuing", deadline=due_in(2)
    )
    text = deadlines_ics([opportunity], name="Test").decode().replace("\r\n ", "")
    assert "SUMMARY:Deadline: A\\; B\\, C\\\\D\\nE" in text


# ---------------------------------------------------------------------------
# Configuration checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "value", "check_id"),
    [
        ("OUTREACH_DAILY_SEND_LIMIT", 0, "outreach.E001"),
        ("OUTREACH_REPEAT_DAYS", -1, "outreach.E002"),
        ("DEADLINE_REMINDER_DAYS", [0, 3], "outreach.E003"),
        ("DEADLINE_REMINDER_DAYS", [], "outreach.E003"),
        ("DISCOVERY_SCHEDULE_BATCH", 0, "outreach.E004"),
        ("OUTREACH_JOB_STALE_MINUTES", 1, "outreach.E006"),
    ],
)
def test_broken_settings_fail_the_checks(settings, setting, value, check_id):
    setattr(settings, setting, value)
    with pytest.raises(SystemCheckError, match=check_id):
        call_command("check")
