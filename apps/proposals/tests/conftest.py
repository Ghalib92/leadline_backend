import pytest
from django.core import mail
from django.core.cache import cache

from apps.ai.config.loader import load_sources_file
from apps.authentication.tests.factories import UserFactory
from apps.discovery.tests.factories import ready_organization
from apps.organizations.models import OrganizationMembership


@pytest.fixture(autouse=True)
def isolate():
    cache.clear()
    load_sources_file.cache_clear()
    yield
    load_sources_file.cache_clear()


@pytest.fixture
def organization(user):
    organization = ready_organization(user)
    mail.outbox.clear()
    return organization


@pytest.fixture
def member(organization):
    user = UserFactory()
    OrganizationMembership.objects.create(organization=organization, user=user)
    return user
