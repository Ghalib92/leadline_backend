from datetime import timedelta

import pytest
from django.contrib import admin
from django.core import mail
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from apps.authentication import services as auth_services
from apps.authentication.tests.factories import UserFactory

from .. import checks, services
from ..models import Organization, OrganizationInvitation, OrganizationMembership

pytestmark = pytest.mark.django_db(transaction=True)


def test_creation_rolls_back_membership_audit_and_email(user):
    with pytest.raises(RuntimeError), transaction.atomic():
        organization = services.create_organization(actor=user, name="Rollback example")
        assert organization.memberships.count() == 1
        assert organization.events.count() == 1
        assert not mail.outbox
        raise RuntimeError("Roll back")
    assert not Organization.objects.exists()
    assert not OrganizationMembership.objects.exists()
    assert not mail.outbox


def test_invitation_email_is_sent_only_after_commit(user, organization):
    with transaction.atomic():
        invitation = services.invite_member(
            organization_id=organization.pk, actor=user, email="new@example.com"
        )
        assert invitation.email_status == "pending"
        assert not mail.outbox
    invitation.refresh_from_db()
    assert invitation.email_status == "sent" and len(mail.outbox) == 1


def test_rolled_back_invitation_never_sends_email(user, organization):
    with pytest.raises(RuntimeError), transaction.atomic():
        services.invite_member(organization_id=organization.pk, actor=user, email="new@example.com")
        raise RuntimeError("Roll back")
    assert not organization.invitations.exists()
    assert not mail.outbox


def test_database_rejects_duplicate_memberships(user, organization):
    with pytest.raises(IntegrityError), transaction.atomic():
        OrganizationMembership.objects.create(organization=organization, user=user)


@pytest.mark.parametrize("values", [{"role": "owner"}, {"status": "deleted"}])
def test_database_rejects_invalid_membership_state(organization, values):
    with pytest.raises(IntegrityError), transaction.atomic():
        OrganizationMembership.objects.create(
            organization=organization, user=UserFactory(), **values
        )


def test_database_rejects_case_variant_duplicate_pending_invites(organization):
    values = {"organization": organization, "expires_at": timezone.now() + timedelta(days=1)}
    OrganizationInvitation.objects.create(email="new@example.com", token_hash="a" * 64, **values)
    with pytest.raises(IntegrityError), transaction.atomic():
        OrganizationInvitation.objects.create(
            email="NEW@EXAMPLE.COM", token_hash="b" * 64, **values
        )


def test_membership_prevents_user_deletion_bypassing_last_admin_guard(user, organization):
    with pytest.raises(ProtectedError):
        user.delete()
    assert organization.memberships.filter(role="admin", status="active").exists()


def test_django_admin_cannot_bypass_organization_service_rules():
    request = APIRequestFactory().get("/admin/")
    request.user = UserFactory(is_superuser=True, is_staff=True)
    for model in (Organization, OrganizationMembership, OrganizationInvitation):
        model_admin = admin.site._registry[model]
        assert not model_admin.has_add_permission(request)
        assert not model_admin.has_change_permission(request)
        assert not model_admin.has_delete_permission(request)
    assert "token_hash" not in admin.site._registry[OrganizationInvitation].get_readonly_fields(
        request
    )


@pytest.mark.parametrize(
    "setting,value,error",
    [
        ("FRONTEND_URL", "javascript:alert(1)", "organizations.E001"),
        ("FRONTEND_URL", "https://[invalid", "organizations.E001"),
        ("FRONTEND_URL", "https://example.com?token=something", "organizations.E001"),
        ("ORGANIZATION_INVITATION_PATH", "//evil.example.com", "organizations.E002"),
        ("ORGANIZATION_INVITATION_EXPIRY_HOURS", 0, "organizations.E003"),
        ("ORGANIZATION_INVITATION_RESEND_COOLDOWN_SECONDS", -1, "organizations.E004"),
    ],
)
def test_invalid_email_link_configuration_is_detected(settings, setting, value, error):
    setattr(settings, setting, value)
    assert error in {issue.id for issue in checks.organization_configuration(None)}


def test_otp_generation_uses_current_mailbox_even_with_stale_user_instance(user):
    current = type(user).objects.get(pk=user.pk)
    auth_services.change_email(current, "updated@example.com")
    auth_services.send_verification_otp(user)
    user.refresh_from_db()
    assert mail.outbox[-1].to == ["updated@example.com"]
    assert user.otp_code in mail.outbox[-1].body


def test_smtp_zero_send_result_is_a_failure(user, monkeypatch):
    from django.core.mail import EmailMultiAlternatives

    monkeypatch.setattr(EmailMultiAlternatives, "send", lambda self, **kwargs: 0)
    with pytest.raises(RuntimeError, match="did not accept"):
        auth_services.send_verification_otp(user)