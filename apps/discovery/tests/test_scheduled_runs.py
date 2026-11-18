"""Daily and weekly agents started by the hourly tick (apps.discovery.jobs.start_due_searches,
run by a Temporal Schedule)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.discovery import jobs, selectors, services
from apps.discovery.models import DiscoveryRun
from apps.organizations.models import OrganizationMembership

from .factories import make_agent

# No transaction: runs are created but their dispatch (on commit) never happens.
pytestmark = pytest.mark.django_db


def past_run(agent, *, ago, status="succeeded"):
    run = DiscoveryRun.objects.create(
        organization=agent.organization,
        agent=agent,
        agent_name=agent.name,
        trigger="schedule",
        status=status,
    )
    DiscoveryRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - ago)
    return run


def scheduled_runs():
    return list(DiscoveryRun.objects.filter(trigger="schedule", status="queued"))


def test_due_agents_start(organization, user):
    never = make_agent(organization, user, name="Never ran", schedule="daily")
    daily = make_agent(organization, user, name="Daily", schedule="daily")
    weekly = make_agent(organization, user, name="Weekly", schedule="weekly")
    make_agent(organization, user, name="Manual", schedule="manual")
    make_agent(organization, user, name="Off", schedule="daily", enabled=False)
    past_run(daily, ago=timedelta(hours=2))
    past_run(weekly, ago=timedelta(days=8))

    assert selectors.agents_due(limit=10) == [never.pk, weekly.pk]
    assert selectors.agents_due(limit=1) == [never.pk]  # never-run agents go first

    jobs.start_due_searches()
    # One active run per organization: the first starts, the other waits for the next tick.
    runs = scheduled_runs()
    assert [run.agent_id for run in runs] == [never.pk]
    assert runs[0].triggered_by is None


def test_a_daily_agent_is_due_again_a_day_later(organization, user):
    daily = make_agent(organization, user, schedule="daily")
    past_run(daily, ago=timedelta(hours=23, minutes=45))  # a little early is fine
    assert selectors.agents_due(limit=10) == [daily.pk]


def test_skipped_when_turned_off(organization, user, settings):
    settings.SCHEDULED_RUNS_ENABLED = False
    make_agent(organization, user, schedule="daily")
    jobs.start_due_searches()
    assert scheduled_runs() == []


def test_skipped_while_another_run_is_active(organization, user):
    agent = make_agent(organization, user, schedule="daily")
    other = make_agent(organization, user, name="Manual", schedule="manual")
    past_run(other, ago=timedelta(minutes=1), status="running")
    jobs.start_due_searches()
    assert not DiscoveryRun.objects.filter(agent=agent).exists()


def test_skipped_when_the_owner_left_or_the_organization_is_archived(organization, user, member):
    agent = make_agent(organization, member, schedule="daily")
    OrganizationMembership.objects.filter(user=member).update(
        status=OrganizationMembership.Status.INACTIVE
    )
    jobs.start_due_searches()
    assert not DiscoveryRun.objects.filter(agent=agent).exists()

    make_agent(organization, user, name="Admin's", schedule="weekly")
    organization.archived_at = timezone.now()
    organization.save()
    jobs.start_due_searches()
    assert scheduled_runs() == []


def test_temporal_schedules_cover_every_timer():
    from config.temporal import timers

    assert set(timers()) == {
        "discovery-scheduled-searches",
        "discovery-recover-runs",
        "outreach-deadline-reminders",
        "outreach-weekly-digest",
    }


def test_ineligible_agents_do_not_starve_eligible_agents():
    from apps.authentication.tests.factories import UserFactory
    from apps.discovery.tests.factories import ready_organization
    from apps.organizations.services import create_organization

    # Ineligible Org 1 (incomplete setup) with a daily agent
    user1 = UserFactory()
    incomplete_org = create_organization(actor=user1, name="Incomplete Org", country="KE")
    ineligible_agent = make_agent(incomplete_org, user1, name="Ineligible", schedule="daily")

    # Eligible Org 2 (ready_organization) with a daily agent
    user2 = UserFactory()
    ready_org = ready_organization(user2, name="Ready Org")
    eligible_agent = make_agent(ready_org, user2, name="Eligible", schedule="daily")

    # Limit is 1: ineligible_agent was created first, but because it's ineligible,
    # it must NOT occupy the batch or starve eligible_agent.
    assert ineligible_agent.pk not in selectors.agents_due(limit=10)
    assert selectors.agents_due(limit=1) == [eligible_agent.pk]

    jobs.start_due_searches()
    runs = DiscoveryRun.objects.filter(trigger="schedule", status="queued")
    assert [run.agent_id for run in runs] == [eligible_agent.pk]


def test_recheck_due_inside_locked_scheduling_prevents_duplicate_from_stale_selection(
    organization, user
):
    agent = make_agent(organization, user, schedule="daily")
    # Scheduler selects the agent
    due = selectors.agents_due(limit=10)
    assert due == [agent.pk]

    # Before start_scheduled_run executes, a run is started (e.g. manual search or concurrent run)
    past_run(agent, ago=timedelta(minutes=5))

    # Stale scheduler execution rechecks inside the lock and skips
    run = services.start_scheduled_run(agent.pk)
    assert run is None
    # No queued runs created
    assert scheduled_runs() == []


def test_scheduled_run_assigns_unique_slot_identifier_and_rejects_duplicate_slot(
    organization, user
):
    from django.db import IntegrityError

    agent = make_agent(organization, user, schedule="daily")
    slot = selectors.schedule_slot(agent)
    assert slot.startswith(f"{agent.pk}:daily:")

    # First scheduled run succeeds and records schedule_slot
    run = services.start_scheduled_run(agent.pk)
    assert run is not None
    assert run.schedule_slot == slot
    assert run.config.get("schedule_slot") == slot

    # Attempting to start another scheduled run for the same slot returns None
    duplicate_run = services.start_scheduled_run(agent.pk, slot=slot)
    assert duplicate_run is None

    # Directly creating a run with the duplicate slot raises IntegrityError
    with pytest.raises(IntegrityError):
        DiscoveryRun.objects.create(
            organization=organization,
            agent=agent,
            agent_name=agent.name,
            trigger="schedule",
            schedule_slot=slot,
        )
