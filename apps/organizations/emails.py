import logging
from functools import partial
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.emails import send_templated_email

from .models import OrganizationInvitation

logger = logging.getLogger(__name__)


def deliver_invitation(*, invitation_id, token, token_hash):
    invitation = (
        OrganizationInvitation.objects.select_related(
            "organization", "invited_by")
        .filter(
            pk=invitation_id,
            token_hash=token_hash,
            status="pending",
            expires_at__gt=timezone.now(),
            organization__archived_at__isnull=True,
        )
        .first()
    )
    if invitation is None:
        return
    # Fragment tokens are not sent to the frontend server in request/referrer URLs.
    accept_url = (
        settings.FRONTEND_URL.rstrip("/")
        + settings.ORGANIZATION_INVITATION_PATH
        + "#"
        + urlencode({"token": token})
    )
    try:
        send_templated_email(
            to=invitation.email,
            subject=f"You're invited to {invitation.organization.name} | {settings.PRODUCT_NAME}",
            template="organizations/emails/invitation",
            context={
                "organization_name": invitation.organization.name,
                "inviter_name": invitation.invited_by.get_full_name()
                if invitation.invited_by
                else "An admin",
                "recipient_email": invitation.email,
                "role": invitation.get_role_display(),
                "message": invitation.message,
                "expires_at": invitation.expires_at,
                "accept_url": accept_url,
            },
        )
    except Exception as exc:
        if settings.DEBUG:
            logger.exception(
                "Organization invitation email failed: invitation_id=%s",
                invitation_id,
            )
        else:
            logger.warning(
                "Organization invitation email failed: invitation_id=%s error_type=%s",
                invitation_id,
                type(exc).__name__,
            )

        values = {"email_status": "failed"}
    else:
        values = {"email_status": "sent", "last_sent_at": timezone.now()}
    # An older callback must never overwrite the outcome of a newer resend.
    OrganizationInvitation.objects.filter(
        pk=invitation_id, token_hash=token_hash).update(**values)


def schedule_invitation(invitation, token):
    transaction.on_commit(
        partial(
            deliver_invitation,
            invitation_id=invitation.pk,
            token=token,
            token_hash=invitation.token_hash,
        ),
        robust=True,
    )


def _deliver_notification(**kwargs):
    try:
        send_templated_email(**kwargs)
    except Exception:
        logger.warning(
            "Organization notification email failed (template=%s)", kwargs["template"])


def schedule_notification(*, to, subject, template, context):
    transaction.on_commit(
        partial(_deliver_notification, to=to, subject=subject,
                template=template, context=context),
        robust=True,
    )
