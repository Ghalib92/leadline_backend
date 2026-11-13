import pytest
from django.core import mail
from django.urls import reverse
from rest_framework.test import APIClient

from .. import services
from ..models import User, UserProfile
from .factories import DEFAULT_PASSWORD, UserFactory

pytestmark = pytest.mark.django_db


def url(name):
    return reverse(f"v1:authentication:{name}")


def login(client, email, password=DEFAULT_PASSWORD):
    return client.post(url("login"), {"email": email, "password": password}, format="json")


# ---------------------------------------------------------------------------
# Registration and verification
# ---------------------------------------------------------------------------


def test_register_creates_unverified_user_and_emails_a_code(api_client):
    response = api_client.post(
        url("register"),
        {
            "email": "newuser@example.com",
            "password": DEFAULT_PASSWORD,
            "profile": {"first_name": "New", "last_name": "User"},
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert response.data["requires_verification"] is True
    assert "access" not in response.data
    user = User.objects.get(email="newuser@example.com")
    assert not user.is_verified
    assert user.profile.first_name == "New"
    assert user.profile.last_name == "User"
    assert len(mail.outbox) == 1
    assert user.otp_code in mail.outbox[0].body


def test_register_rejects_duplicate_email(api_client, user):
    response = api_client.post(
        url("register"),
        {"email": user.email.upper(), "password": DEFAULT_PASSWORD},
        format="json",
    )
    assert response.status_code == 400
    assert "email" in response.data


def test_register_enforces_password_validators(api_client):
    response = api_client.post(
        url("register"), {"email": "weak@example.com", "password": "123456"}, format="json"
    )
    assert response.status_code == 400
    assert "password" in response.data
    assert not User.objects.filter(email="weak@example.com").exists()


def test_register_still_succeeds_when_email_fails(api_client, monkeypatch):
    def boom(user):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(services, "send_verification_otp", boom)
    response = api_client.post(
        url("register"),
        {"email": "nomail@example.com", "password": DEFAULT_PASSWORD},
        format="json",
    )
    assert response.status_code == 201
    assert "resend" in response.data["detail"]


def test_verify_email_marks_verified_and_returns_a_usable_session(api_client):
    user = UserFactory(is_verified=False)
    code = services.issue_otp(user)

    response = api_client.post(
        url("verify_email"), {"email": user.email, "otp_code": code}, format="json"
    )

    assert response.status_code == 200, response.data
    assert response.data["user"]["is_verified"] is True
    user.refresh_from_db()
    assert user.is_verified
    assert user.otp_code == ""

    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    assert client.get(url("profile")).status_code == 200


def test_verify_email_wrong_code(api_client):
    user = UserFactory(is_verified=False)
    code = services.issue_otp(user)
    wrong = "000000" if code != "000000" else "111111"

    response = api_client.post(
        url("verify_email"), {"email": user.email, "otp_code": wrong}, format="json"
    )

    assert response.status_code == 400
    assert response.data["code"] == "invalid_code"
    user.refresh_from_db()
    assert not user.is_verified


def test_verify_email_unknown_email_looks_like_a_wrong_code(api_client):
    response = api_client.post(
        url("verify_email"), {"email": "nobody@example.com", "otp_code": "123456"}, format="json"
    )
    assert response.status_code == 400
    assert response.data["code"] == "invalid_code"


def test_resend_verification_is_generic(api_client):
    unverified = UserFactory(is_verified=False)

    for email in (unverified.email, "nobody@example.com"):
        response = api_client.post(url("resend_verification"), {"email": email}, format="json")
        assert response.status_code == 200

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [unverified.email]


# ---------------------------------------------------------------------------
# Login, refresh, logout
# ---------------------------------------------------------------------------


def test_login_returns_session_for_verified_user(api_client):
    user = UserFactory()
    user.profile.first_name = "Forinda"
    user.profile.last_name = "Tester"
    user.profile.save()

    response = login(api_client, user.email)

    assert response.status_code == 200, response.data
    assert {"access", "refresh", "user"} <= set(response.data)
    assert response.data["user"]["email"] == user.email
    assert response.data["user"]["first_name"] == "Forinda"
    assert response.data["user"]["last_name"] == "Tester"


def test_login_blocks_unverified_user(api_client):
    user = UserFactory(is_verified=False)
    response = login(api_client, user.email)
    assert response.status_code == 403
    assert response.data["code"] == "email_not_verified"


def test_login_wrong_password(api_client, user):
    response = login(api_client, user.email, "wrong-password")
    assert response.status_code == 401
    assert "access" not in response.data


def test_login_inactive_user(api_client):
    user = UserFactory(is_active=False)
    assert login(api_client, user.email).status_code == 401


def test_refresh_rotates_and_blacklists_the_old_token(api_client, user):
    refresh = login(api_client, user.email).data["refresh"]

    response = api_client.post(url("refresh_token"), {"refresh": refresh}, format="json")
    assert response.status_code == 200
    assert response.data["refresh"] != refresh
    assert "access" in response.data

    reused = api_client.post(url("refresh_token"), {"refresh": refresh}, format="json")
    assert reused.status_code == 401


def test_logout_blacklists_the_refresh_token(api_client, user):
    refresh = login(api_client, user.email).data["refresh"]

    assert api_client.post(url("logout"), {"refresh": refresh}, format="json").status_code == 200
    after = api_client.post(url("refresh_token"), {"refresh": refresh}, format="json")
    assert after.status_code == 401


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


def test_forgot_password_emails_a_code_without_leaking_it(api_client, user):
    response = api_client.post(url("forgot_password"), {"email": user.email}, format="json")
    assert response.status_code == 200
    assert "otp_code" not in response.data
    assert len(mail.outbox) == 1


def test_forgot_password_unknown_email_gets_the_same_response(api_client, user):
    known = api_client.post(url("forgot_password"), {"email": user.email}, format="json")
    unknown = api_client.post(
        url("forgot_password"), {"email": "nobody@example.com"}, format="json"
    )
    assert unknown.status_code == known.status_code == 200
    assert unknown.data["detail"] == known.data["detail"]


def test_reset_password_sets_password_and_signs_out_other_sessions(api_client, user):
    old_refresh = login(api_client, user.email).data["refresh"]
    code = services.issue_otp(user)

    response = api_client.post(
        url("reset_password"),
        {"email": user.email, "otp_code": code, "new_password": "Brand-New-Passw0rd"},
        format="json",
    )

    assert response.status_code == 200, response.data
    assert "access" in response.data
    assert login(api_client, user.email, "Brand-New-Passw0rd").status_code == 200
    stale = api_client.post(url("refresh_token"), {"refresh": old_refresh}, format="json")
    assert stale.status_code == 401


def test_reset_password_wrong_code_leaves_password_alone(api_client, user):
    code = services.issue_otp(user)
    wrong = "000000" if code != "000000" else "111111"

    response = api_client.post(
        url("reset_password"),
        {"email": user.email, "otp_code": wrong, "new_password": "Brand-New-Passw0rd"},
        format="json",
    )

    assert response.status_code == 400
    assert login(api_client, user.email).status_code == 200


# ---------------------------------------------------------------------------
# Signed-in account management
# ---------------------------------------------------------------------------


def test_protected_endpoints_require_auth(api_client):
    assert api_client.get(url("profile")).status_code == 401


def test_profile_get(auth_client, user):
    response = auth_client.get(url("profile"))
    assert response.status_code == 200
    assert response.data["email"] == user.email
    assert response.data["is_verified"] is True


def test_profile_patch_updates_names(auth_client, user):
    response = auth_client.patch(
        url("profile"), {"profile": {"first_name": "Ada", "last_name": "Lovelace"}}, format="json"
    )
    assert response.status_code == 200
    assert response.data["data"]["profile"]["first_name"] == "Ada"
    profile = UserProfile.objects.get(user=user)
    assert (profile.first_name, profile.last_name) == ("Ada", "Lovelace")


def test_profile_patch_cannot_change_account_flags(auth_client, user):
    auth_client.patch(
        url("profile"),
        {"email": "hijack@example.com", "is_superuser": True, "is_verified": False},
        format="json",
    )
    user.refresh_from_db()
    assert user.email != "hijack@example.com"
    assert not user.is_superuser
    assert user.is_verified


def test_change_password(auth_client, user):
    response = auth_client.post(
        url("change_password"),
        {
            "old_password": DEFAULT_PASSWORD,
            "new_password": "Brand-New-Passw0rd",
            "confirm_password": "Brand-New-Passw0rd",
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert "access" in response.data
    user.refresh_from_db()
    assert user.check_password("Brand-New-Passw0rd")


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"old_password": "wrong", "new_password": "Brand-New-Passw0rd"}, "old_password"),
        (
            {"new_password": "Brand-New-Passw0rd", "confirm_password": "mismatch"},
            "confirm_password",
        ),
        ({"new_password": DEFAULT_PASSWORD, "confirm_password": DEFAULT_PASSWORD}, "new_password"),
    ],
)
def test_change_password_rejections(auth_client, user, body, field):
    payload = {"old_password": DEFAULT_PASSWORD, "confirm_password": body.get("new_password")}
    payload.update(body)
    response = auth_client.post(url("change_password"), payload, format="json")
    assert response.status_code == 400
    assert field in response.data
    user.refresh_from_db()
    assert user.check_password(DEFAULT_PASSWORD)


def test_change_email(auth_client, user):
    response = auth_client.post(
        url("change_email"),
        {"new_email": "after@example.com", "password": DEFAULT_PASSWORD},
        format="json",
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.email == "after@example.com"


def test_change_email_requires_correct_password(auth_client, user):
    before = user.email
    response = auth_client.post(
        url("change_email"), {"new_email": "after@example.com", "password": "wrong"}, format="json"
    )
    assert response.status_code == 400
    user.refresh_from_db()
    assert user.email == before


def test_change_email_rejects_taken_address(auth_client):
    other = UserFactory()
    response = auth_client.post(
        url("change_email"), {"new_email": other.email, "password": DEFAULT_PASSWORD}, format="json"
    )
    assert response.status_code == 400
    assert "new_email" in response.data
