import pytest
from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from rest_framework.test import APIClient

from apps.ai.config.loader import load_models_file, load_outreach_file
from apps.ai.prompts.loader import load_prompt
from apps.authentication.tests.factories import UserFactory
from apps.discovery.tests.factories import days, make_opportunity, ready_organization
from apps.organizations.models import OrganizationMembership


@pytest.fixture(autouse=True)
def isolate():
    cache.clear()
    for loader in (load_outreach_file, load_models_file, load_prompt):
        loader.cache_clear()
    yield
    for loader in (load_outreach_file, load_models_file, load_prompt):
        loader.cache_clear()


@pytest.fixture
def organization(user):
    """`user` (the `auth_client`) is its admin."""
    organization = ready_organization(user)
    mail.outbox.clear()
    return organization


@pytest.fixture
def member(organization):
    user = UserFactory()
    OrganizationMembership.objects.create(organization=organization, user=user)
    return user


@pytest.fixture
def member_client(member):
    return as_user(member)


@pytest.fixture
def opportunity(organization):
    """Approved: contacts and outreach are for opportunities being pursued."""
    return make_opportunity(
        organization, title="Youth Data Fund", status="pursuing", deadline=days(30)
    )


def as_user(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def url(name, organization, **kwargs):
    return reverse(f"v1:outreach:{name}", kwargs={"organization_id": organization.pk, **kwargs})
