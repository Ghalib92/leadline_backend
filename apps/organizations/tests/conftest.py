import pytest
from django.core import mail
from django.core.cache import cache

from apps.authentication.tests.factories import UserFactory

from .. import services
from ..models import OrganizationMembership


@pytest.fixture(autouse=True)
def clear_throttles():
    cache.clear()


@pytest.fixture
def organization(user):
    organization = services.create_organization(actor=user, name="Acme Africa", country="KE")
    mail.outbox.clear()
    return organization


@pytest.fixture
def membership(organization):
    return OrganizationMembership.objects.create(organization=organization, user=UserFactory())