import pytest
from django.contrib.auth.models import AnonymousUser
from rest_framework.test import APIRequestFactory

from ..permissions import IsVerified
from .factories import UserFactory


@pytest.mark.django_db
@pytest.mark.parametrize(("verified", "allowed"), [(True, True), (False, False)])
def test_is_verified(verified, allowed):
    request = APIRequestFactory().get("/")
    request.user = UserFactory(is_verified=verified)
    assert IsVerified().has_permission(request, None) is allowed


def test_is_verified_rejects_anonymous():
    request = APIRequestFactory().get("/")
    request.user = AnonymousUser()
    assert IsVerified().has_permission(request, None) is False
