"""
Authentication flows: registration, OTP email codes, sessions and credential
changes. Views stay thin and call into here.
"""

import secrets
import string
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.common.emails import send_templated_email

from .models import User, UserProfile

# ---------------------------------------------------------------------------
# One-time codes
# ---------------------------------------------------------------------------


def generate_otp(length=6):
    return "".join(secrets.choice(string.digits) for _ in range(length))


@transaction.atomic
def issue_otp(user):
    """Generate a fresh OTP, persist it on `user`, return the code."""
    locked = User.objects.select_for_update().get(pk=user.pk)
    locked.otp_code = generate_otp()
    locked.otp_expiry = timezone.now() + timedelta(minutes=settings.OTP_EXPIRY_MINUTES)
    locked.otp_attempts = 0
    locked.save(update_fields=["otp_code", "otp_expiry", "otp_attempts"])
    # A concurrent email change must not deliver a live code to a stale address.
    user.email = locked.email
    user.otp_code, user.otp_expiry, user.otp_attempts = locked.otp_code, locked.otp_expiry, 0
    return user.otp_code


def _clear_otp(user):
    user.otp_code = ""
    user.otp_expiry = None
    user.otp_attempts = 0


def consume_otp(user, code):
    """
    True if `code` is the user's live OTP, which is then spent.

    A six-digit code is only safe while guesses are capped, so every miss is
    counted and the code is burned after OTP_MAX_ATTEMPTS. The row is locked so
    parallel guesses cannot all read the same attempt count.
    """
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        fields = ["otp_code", "otp_expiry", "otp_attempts"]

        if not locked.otp_code or not locked.otp_expiry or timezone.now() > locked.otp_expiry:
            return False

        if not secrets.compare_digest(locked.otp_code, code):
            locked.otp_attempts += 1
            if locked.otp_attempts >= settings.OTP_MAX_ATTEMPTS:
                _clear_otp(locked)
            locked.save(update_fields=fields)
            return False

        _clear_otp(locked)
        locked.save(update_fields=fields)

    _clear_otp(user)
    return True


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------


def send_verification_otp(user):
    code = issue_otp(user)
    send_templated_email(
        to=user.email,
        subject=f"Verify your {settings.PRODUCT_NAME} account",
        template="authentication/emails/verification",
        context={
            "recipient_name": user.get_short_name(),
            "code": code,
            "expiry_minutes": settings.OTP_EXPIRY_MINUTES,
        },
    )


def send_password_reset_otp(user):
    code = issue_otp(user)
    send_templated_email(
        to=user.email,
        subject=f"Reset your {settings.PRODUCT_NAME} password",
        template="authentication/emails/password_reset",
        context={
            "recipient_name": user.get_short_name(),
            "code": code,
            "expiry_minutes": settings.OTP_EXPIRY_MINUTES,
        },
    )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def user_payload(user):
    """The `user` block every auth response carries."""
    profile = getattr(user, "profile", None)
    return {
        "id": str(user.id),
        "email": user.email,
        "is_verified": user.is_verified,
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "first_name": profile.first_name if profile else "",
        "last_name": profile.last_name if profile else "",
    }


def issue_session(user):
    """
    Tokens plus the user block — the same shape `POST /auth/login/` returns.

    Only call this once the request has established identity: a correct
    password, or an emailed OTP that matched and had not expired.
    """
    refresh = RefreshToken.for_user(user)
    update_last_login(None, user)
    return {
        "refresh": str(refresh),
        "access": str(refresh.access_token),
        "user": user_payload(user),
    }


def revoke_sessions(user):
    """Blacklist every outstanding refresh token, signing the user out everywhere."""
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in OutstandingToken.objects.filter(user=user)],
        ignore_conflicts=True,
    )


# ---------------------------------------------------------------------------
# Account flows
# ---------------------------------------------------------------------------


def register_user(*, email, password, profile=None):
    """Create the account and profile. Sending the verification code is separate."""
    with transaction.atomic():
        user = User.objects.create_user(email=email, password=password)
        if profile:
            UserProfile.objects.filter(user=user).update(**profile)
    return User.objects.select_related("profile").get(pk=user.pk)


@transaction.atomic
def verify_email(user, code):
    if not consume_otp(user, code):
        return False
    user.is_verified = True
    user.save(update_fields=["is_verified"])
    return True


@transaction.atomic
def reset_password(user, code, new_password):
    """
    Set a new password with an emailed OTP. Resetting also verifies the email,
    since the code proves the same thing, and signs out every other session.
    """
    if not consume_otp(user, code):
        return False
    user.set_password(new_password)
    user.is_verified = True
    user.save(update_fields=["password", "is_verified"])
    revoke_sessions(user)
    return True


def change_password(user, new_password):
    user.set_password(new_password)
    user.save(update_fields=["password"])
    revoke_sessions(user)


@transaction.atomic
def change_email(user, new_email):
    # Organization invitations trust a verified mailbox, so the previous
    # address's proof and OTP must not carry over to a newly supplied address.
    locked = User.objects.select_for_update().get(pk=user.pk)
    locked.email = User.objects.normalize_email(new_email)
    locked.is_verified = False
    _clear_otp(locked)
    locked.save(
        update_fields=[
            "email",
            "is_verified",
            "otp_code",
            "otp_expiry",
            "otp_attempts",
            "updated_at",
        ]
    )
    revoke_sessions(locked)
    user.email, user.is_verified = locked.email, False
    _clear_otp(user)