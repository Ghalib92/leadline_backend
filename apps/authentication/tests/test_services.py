from datetime import timedelta

import pytest
from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from .. import services
from .factories import UserFactory

pytestmark = pytest.mark.django_db


def test_issue_otp_is_six_digits_with_expiry():
    user = UserFactory()
    code = services.issue_otp(user)
    user.refresh_from_db()
    assert len(code) == 6 and code.isdigit()
    assert user.otp_code == code
    assert user.otp_expiry > timezone.now()


def test_consume_otp_spends_the_code():
    user = UserFactory()
    code = services.issue_otp(user)
    assert services.consume_otp(user, code)
    assert not services.consume_otp(user, code)
    user.refresh_from_db()
    assert user.otp_code == ""


def test_consume_otp_rejects_expired_code():
    user = UserFactory()
    code = services.issue_otp(user)
    user.otp_expiry = timezone.now() - timedelta(seconds=1)
    user.save(update_fields=["otp_expiry"])
    assert not services.consume_otp(user, code)


@override_settings(OTP_MAX_ATTEMPTS=3)
def test_code_is_burned_after_max_wrong_attempts():
    user = UserFactory()
    code = services.issue_otp(user)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(3):
        assert not services.consume_otp(user, wrong)
    # Even the right code no longer works: guessing has to start over.
    assert not services.consume_otp(user, code)


def test_new_code_resets_the_attempt_count():
    user = UserFactory()
    services.issue_otp(user)
    services.consume_otp(user, "not-it")
    services.issue_otp(user)
    user.refresh_from_db()
    assert user.otp_attempts == 0


def test_verification_email_contains_the_code():
    user = UserFactory(is_verified=False)
    services.send_verification_otp(user)
    user.refresh_from_db()
    assert len(mail.outbox) == 1
    assert user.otp_code in mail.outbox[0].body


def test_issue_session_shape():
    user = UserFactory()
    session = services.issue_session(user)
    assert set(session) == {"refresh", "access", "user"}
    assert session["user"]["email"] == user.email
    user.refresh_from_db()
    assert user.last_login is not None


def test_change_password_revokes_existing_sessions():
    user = UserFactory()
    services.issue_session(user)
    services.issue_session(user)
    services.change_password(user, "An0ther-Str0ng-One")
    assert BlacklistedToken.objects.filter(token__user=user).count() == 2
    assert OutstandingToken.objects.filter(user=user).count() == 2
