import re
from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from apps.authentication import services as auth_services
from apps.authentication.models import User
from apps.authentication.tests.factories import DEFAULT_PASSWORD, UserFactory

from .. import emails, services
from ..models import OrganizationInvitation, OrganizationMembership
from .test_api import as_user, url

pytestmark = pytest.mark.django_db(transaction=True)


def last_invitation_token():
    for message in reversed(mail.outbox):
        match = re.search(r"#token=([A-Za-z0-9_-]{43})", message.body)
        if match:
            return match.group(1)
    raise AssertionError("Invitation email was not sent")


def invite(client, organization, email, **extra):
    response = client.post(
        url("invitations", organization), {"email": email, **extra}, format="json"
    )
    assert response.status_code == 201, response.data
    return response, last_invitation_token()


def act(client, token, name="invitation-accept", **extra):
    return client.post(url(name), {"token": token, **extra}, format="json")


def test_invite_normalizes_email_keeps_tokens_private_and_sends_multipart(
    auth_client, organization
):
    response, token = invite(
        auth_client, organization, " NEW@EXAMPLE.COM ", role="admin", message="Join our team."
    )
    invitation = OrganizationInvitation.objects.get(pk=response.data["id"])
    assert invitation.email == "new@example.com"
    assert invitation.token_hash == services.hash_token(token)
    assert token not in str(response.data) and "token_hash" not in response.data
    assert response.data["email_status"] == "sent"
    assert response.data["last_sent_at"]
    assert invitation.expires_at > timezone.now()
    assert not User.objects.filter(email="new@example.com").exists()
    message = mail.outbox[-1]
    assert message.to == ["new@example.com"] and message.alternatives[0].mimetype == "text/html"
    assert token in message.alternatives[0].content
    assert "Join our team." in message.body
    assert "Admin" in message.body
    assert token not in str(list(organization.events.values()))


def test_new_recipient_can_register_verify_then_accept(auth_client, organization):
    api_client = APIClient()
    _, token = invite(auth_client, organization, "new@example.com")
    assert act(api_client, token).status_code == 401
    register = api_client.post(
        reverse("v1:authentication:register"),
        {
            "email": "new@example.com",
            "password": DEFAULT_PASSWORD,
            "profile": {"first_name": "New", "last_name": "Member"},
        },
        format="json",
    )
    assert register.status_code == 201
    user = User.objects.get(email="new@example.com")
    session = api_client.post(
        reverse("v1:authentication:verify_email"),
        {
            "email": user.email,
            "otp_code": user.otp_code,
        },
        format="json",
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {session.data['access']}")
    preview = act(api_client, token, "invitation-preview")
    assert preview.status_code == 200 and preview.data["organization_name"] == organization.name
    response = act(api_client, token, role="admin", organization_id="ignored")
    assert response.status_code == 200, response.data
    assert response.data["role"] == "member"
    assert api_client.get(url("detail", organization)).status_code == 200
    # Accepting again is a no-op, and the preview now reports the answered status.
    again = act(api_client, token)
    assert again.status_code == 200 and again.data["id"] == response.data["id"]
    assert act(api_client, token, "invitation-preview").data["status"] == "accepted"
    assert organization.memberships.filter(user=user).count() == 1


@pytest.mark.parametrize("role", ["admin", "member"])
def test_accept_uses_invited_role(auth_client, organization, role):
    recipient = UserFactory()
    _, token = invite(auth_client, organization, recipient.email, role=role)
    response = act(as_user(recipient), token)
    assert response.status_code == 200 and response.data["role"] == role


def test_wrong_or_unverified_account_cannot_inspect_accept_or_decline(auth_client, organization):
    recipient = UserFactory(is_verified=False)
    _, token = invite(auth_client, organization, recipient.email)
    for client in (as_user(recipient), as_user(UserFactory())):
        for name in ("invitation-preview", "invitation-accept", "invitation-decline"):
            response = act(client, token, name)
            assert response.status_code == 403
            assert organization.name not in str(response.data)
    assert not organization.memberships.filter(user=recipient).exists()


@pytest.mark.parametrize("value", ["", "invalid", "a" * 42, "!" * 43, "a" * 10000, "A" * 43])
def test_bad_invitation_tokens_return_safe_validation_errors(auth_client, value):
    response = act(auth_client, value)
    assert response.status_code == 400
    assert "Traceback" not in str(response.data)


def test_duplicate_pending_invitation_is_rejected(auth_client, organization):
    invite(auth_client, organization, "new@example.com")
    response = auth_client.post(
        url("invitations", organization), {"email": "NEW@EXAMPLE.COM"}, format="json"
    )
    assert response.status_code == 400
    assert organization.invitations.count() == 1


def test_active_and_inactive_members_cannot_be_reinvited(auth_client, organization, membership):
    for member_status in ("active", "inactive"):
        membership.status = member_status
        membership.save()
        response = auth_client.post(
            url("invitations", organization), {"email": membership.user.email}, format="json"
        )
        assert response.status_code == 400
    assert not organization.invitations.exists()


def test_resend_rotates_token_and_respects_cooldown(auth_client, organization, settings):
    recipient = UserFactory()
    response, original = invite(auth_client, organization, recipient.email)
    endpoint = url("invitation-resend", organization, invitation_id=response.data["id"])
    assert auth_client.post(endpoint).status_code == 429
    settings.ORGANIZATION_INVITATION_RESEND_COOLDOWN_SECONDS = 0
    resent = auth_client.post(endpoint)
    assert resent.status_code == 200 and resent.data["email_status"] == "sent"
    replacement = last_invitation_token()
    assert replacement != original
    client = as_user(recipient)
    assert act(client, original).status_code == 400
    assert act(client, replacement).status_code == 200
    assert auth_client.post(endpoint).status_code == 400


def test_expired_invitation_rejected_and_can_be_renewed(auth_client, organization, settings):
    recipient = UserFactory()
    response, token = invite(auth_client, organization, recipient.email)
    OrganizationInvitation.objects.filter(pk=response.data["id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    assert act(as_user(recipient), token).status_code == 400
    expired = auth_client.get(url("invitations", organization), {"status": "expired"})
    assert expired.data["count"] == 1 and expired.data["results"][0]["status"] == "expired"
    assert (
        auth_client.get(url("invitations", organization), {"status": "pending"}).data["count"] == 0
    )
    settings.ORGANIZATION_INVITATION_RESEND_COOLDOWN_SECONDS = 0
    assert (
        auth_client.post(
            url("invitation-resend", organization, invitation_id=response.data["id"])
        ).status_code
        == 200
    )
    assert act(as_user(recipient), last_invitation_token()).status_code == 200


def test_new_invitation_after_expiry_prevents_resurrecting_old_row(auth_client, organization):
    response, _ = invite(auth_client, organization, "new@example.com")
    old_id = response.data["id"]
    OrganizationInvitation.objects.filter(pk=old_id).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    invite(auth_client, organization, "new@example.com")
    assert organization.invitations.filter(status="pending").count() == 1
    assert (
        auth_client.post(url("invitation-resend", organization, invitation_id=old_id)).status_code
        == 400
    )


@pytest.mark.parametrize("action", ["revoke", "decline", "archive"])
def test_cancelled_invitation_cannot_create_membership(auth_client, organization, action):
    recipient = UserFactory()
    response, token = invite(auth_client, organization, recipient.email)
    client = as_user(recipient)
    if action == "revoke":
        endpoint = url("invitation-revoke", organization, invitation_id=response.data["id"])
        assert auth_client.post(endpoint).status_code == 200
        assert auth_client.post(endpoint).status_code == 200
    elif action == "decline":
        assert act(client, token, "invitation-decline").status_code == 204
    else:
        assert auth_client.post(url("archive", organization)).status_code == 200
        assert auth_client.post(url("restore", organization)).status_code == 200
    assert act(client, token).status_code == 400
    assert not organization.memberships.filter(user=recipient).exists()


def test_removed_member_must_accept_fresh_invitation(auth_client, organization, membership):
    auth_client.delete(url("member-detail", organization, membership_id=membership.pk))
    _, token = invite(auth_client, organization, membership.user.email)
    assert as_user(membership.user).get(url("detail", organization)).status_code == 404
    response = act(as_user(membership.user), token)
    assert response.status_code == 200 and str(response.data["id"]) == str(membership.pk)
    assert organization.memberships.filter(user=membership.user).count() == 1


def test_stale_invitation_never_promotes_existing_active_member(auth_client, organization):
    recipient = UserFactory()
    _, token = invite(auth_client, organization, recipient.email, role="admin")
    member = OrganizationMembership.objects.create(organization=organization, user=recipient)
    assert act(as_user(recipient), token).status_code == 400
    member.refresh_from_db()
    assert member.role == "member"


def test_stale_invitation_never_reactivates_inactive_member(auth_client, organization):
    recipient = UserFactory()
    _, token = invite(auth_client, organization, recipient.email)
    member = OrganizationMembership.objects.create(
        organization=organization, user=recipient, status="inactive"
    )
    assert act(as_user(recipient), token).status_code == 403
    member.refresh_from_db()
    assert member.status == "inactive"


def test_invitation_actions_are_scoped_to_organization(auth_client, user, organization):
    response, _ = invite(auth_client, organization, "new@example.com")
    other = services.create_organization(actor=user, name="Other")
    for name in ("invitation-resend", "invitation-revoke"):
        assert (
            auth_client.post(url(name, other, invitation_id=response.data["id"])).status_code == 404
        )


def test_delivery_failure_is_visible_and_recoverable(
    auth_client, organization, settings, monkeypatch, caplog
):
    real_sender = emails.send_templated_email

    def fail(**kwargs):
        raise RuntimeError("private-provider-message-do-not-log")

    monkeypatch.setattr(emails, "send_templated_email", fail)
    response = auth_client.post(
        url("invitations", organization), {"email": "new@example.com"}, format="json"
    )
    assert response.status_code == 201 and response.data["email_status"] == "failed"
    assert "private-provider-message" not in caplog.text
    assert organization.invitations.count() == 1
    monkeypatch.setattr(emails, "send_templated_email", real_sender)
    settings.ORGANIZATION_INVITATION_RESEND_COOLDOWN_SECONDS = 0
    retry = auth_client.post(
        url("invitation-resend", organization, invitation_id=response.data["id"])
    )
    assert retry.status_code == 200 and retry.data["email_status"] == "sent"


def test_invitation_content_is_escaped_in_html_but_plain_in_text(auth_client, organization):
    organization.name = "Research & <script>alert(1)</script>"
    organization.save()
    invite(auth_client, organization, "new@example.com", message='<img src=x onerror="alert(2)">')
    html = mail.outbox[-1].alternatives[0].content
    assert "<script>" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html
    assert organization.name in mail.outbox[-1].body


def test_invitation_rate_limit_applies_to_admin_sends(auth_client, organization, monkeypatch):
    monkeypatch.setattr(
        ScopedRateThrottle, "THROTTLE_RATES", {"organization_invitation": "1/minute"}
    )
    assert (
        auth_client.post(
            url("invitations", organization), {"email": "first@example.com"}, format="json"
        ).status_code
        == 201
    )
    assert (
        auth_client.post(
            url("invitations", organization), {"email": "second@example.com"}, format="json"
        ).status_code
        == 429
    )
    assert organization.invitations.count() == 1


def test_changed_email_must_be_verified_before_accepting_an_invitation(
    auth_client, organization, monkeypatch
):
    codes = iter(["111111", "222222"])
    monkeypatch.setattr(auth_services, "generate_otp", lambda: next(codes))
    attacker = UserFactory()
    old_email = attacker.email
    session = auth_services.issue_session(attacker)
    old_code = auth_services.issue_otp(attacker)
    _, token = invite(auth_client, organization, "invitee@example.com")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {session['access']}")
    change = client.post(
        reverse("v1:authentication:change_email"),
        {
            "new_email": "invitee@example.com",
            "password": DEFAULT_PASSWORD,
        },
        format="json",
    )
    assert change.status_code == 200 and change.data["requires_verification"]
    assert act(client, token).status_code == 403
    refresh = client.post(
        reverse("v1:authentication:refresh_token"), {"refresh": session["refresh"]}, format="json"
    )
    assert refresh.status_code == 401
    attacker.refresh_from_db()
    assert not attacker.is_verified and attacker.email != old_email
    assert not auth_services.consume_otp(attacker, old_code)
    # Once the new mailbox is actually verified, its invitation can be accepted.
    attacker.refresh_from_db()
    verify = client.post(
        reverse("v1:authentication:verify_email"),
        {
            "email": attacker.email,
            "otp_code": attacker.otp_code,
        },
        format="json",
    )
    assert verify.status_code == 200
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {verify.data['access']}")
    assert act(client, token).status_code == 200