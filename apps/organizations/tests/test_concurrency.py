"""Real PostgreSQL transactions; deliberately skipped by the SQLite smoke suite."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connection, connections
from rest_framework.exceptions import APIException

from apps.authentication.models import User
from apps.authentication.tests.factories import UserFactory

from .. import services
from .test_invitations import last_invitation_token

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.postgresql]


@pytest.fixture(autouse=True)
def require_postgresql(monkeypatch):
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL row locks are required; SQLite cannot exercise this test.")
    monkeypatch.setattr(services, "schedule_notification", lambda **kwargs: None)


def race(*operations):
    barrier = Barrier(len(operations))

    def run(operation):
        connections.close_all()
        try:
            barrier.wait(timeout=10)
            try:
                operation()
                return "ok"
            except APIException as exc:
                return exc.status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return list(pool.map(run, operations))


def test_parallel_admin_demotions_preserve_an_active_admin(user, organization, membership):
    membership.role = "admin"
    membership.save()
    first = organization.memberships.get(user=user)

    def demote(user_id, member_id):
        services.update_membership(
            organization_id=organization.pk,
            membership_id=member_id,
            actor=User.objects.get(pk=user_id),
            role="member",
        )

    results = race(
        lambda: demote(user.pk, first.pk), lambda: demote(membership.user_id, membership.pk)
    )
    assert results.count("ok") == 1 and results.count(400) == 1
    assert organization.memberships.filter(role="admin", status="active").count() == 1


def test_parallel_acceptance_consumes_invitation_once(user, organization):
    recipient = UserFactory()
    services.invite_member(organization_id=organization.pk, actor=user, email=recipient.email)
    token = last_invitation_token()

    def accept():
        services.respond_to_invitation(token=token, actor=User.objects.get(pk=recipient.pk))

    # The repeat accept is an idempotent no-op; the invitation is still consumed once.
    results = race(accept, accept)
    assert results == ["ok", "ok"]
    assert organization.memberships.filter(user=recipient).count() == 1
    assert organization.events.filter(action="invitation.accepted").count() == 1


def test_acceptance_and_revocation_have_one_consistent_winner(user, organization):
    recipient = UserFactory()
    invitation = services.invite_member(
        organization_id=organization.pk, actor=user, email=recipient.email
    )
    token = last_invitation_token()
    results = race(
        lambda: services.respond_to_invitation(
            token=token, actor=User.objects.get(pk=recipient.pk)
        ),
        lambda: services.revoke_invitation(
            organization_id=organization.pk,
            invitation_id=invitation.pk,
            actor=User.objects.get(pk=user.pk),
        ),
    )
    assert results.count("ok") == 1 and results.count(400) == 1
    invitation.refresh_from_db()
    assert organization.memberships.filter(user=recipient).exists() == (
        invitation.status == "accepted"
    )