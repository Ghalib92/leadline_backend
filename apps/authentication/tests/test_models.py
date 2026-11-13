import pytest
from django.contrib.auth import authenticate

from ..models import User, UserProfile

pytestmark = pytest.mark.django_db


def test_create_user_creates_a_profile():
    user = User.objects.create_user(email="a@example.com", password="x")
    assert UserProfile.objects.filter(user=user).exists()
    assert not user.is_verified
    assert not user.is_staff


def test_create_superuser_is_verified_staff():
    user = User.objects.create_superuser(email="root@example.com", password="x")
    assert user.is_staff and user.is_superuser and user.is_verified


def test_create_user_requires_email():
    with pytest.raises(ValueError):
        User.objects.create_user(email="", password="x")


def test_full_name_prefers_profile_name():
    user = User.objects.create_user(email="name@example.com", password="x")
    UserProfile.objects.filter(user=user).update(first_name="Grace", last_name="Hopper")
    user.refresh_from_db()
    assert user.get_full_name() == "Grace Hopper"


def test_full_name_falls_back_to_email():
    user = User.objects.create_user(email="noname@example.com", password="x")
    assert user.get_full_name() == "noname@example.com"


def test_whitespace_and_case_stripped_on_save():
    user = User.objects.create_user(email="  Admin@Leadline.LOCAL  ", password="x")
    user.refresh_from_db()
    assert user.email == "admin@leadline.local"


def test_login_lookup_is_normalised_too():
    User.objects.create_user(email="login@example.com", password="pw-123-abc")
    assert authenticate(email=" Login@EXAMPLE.com ", password="pw-123-abc") is not None


def test_manually_dirtied_email_is_cleaned_on_next_save():
    user = User.objects.create_user(email="dirty@example.com", password="pw-123-abc")
    User.objects.filter(pk=user.pk).update(email=" dirty@example.com ")
    user.refresh_from_db()
    user.save()
    assert authenticate(email="dirty@example.com", password="pw-123-abc") is not None
