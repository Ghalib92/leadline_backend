import pytest
from django.core import mail
from django.core.cache import cache

from apps.ai import llm
from apps.ai.config.loader import load_models_file
from apps.ai.prompts.loader import load_prompt
from apps.ai.testing import fake_chat_model
from apps.authentication.tests.factories import UserFactory
from apps.knowledge import embeddings
from apps.organizations import services as organization_services
from apps.organizations.models import OrganizationMembership

from .files import make_docx

PROFILE = {
    "organization_type": "ngo",
    "description": "Acme Africa trains young people in data skills.",
    "mission": "Close the data skills gap in East Africa.",
    "sectors": ["education"],
}

DOSSIER = {
    "summary": "Acme Africa is a Kenyan NGO that trains young people in data skills.",
    "organization_type": "NGO",
    "mission": "Close the data skills gap in East Africa.",
    "focus_areas": ["education", "digital skills"],
    "search_keywords": ["youth data skills grant"],
    "gaps": ["No audited accounts uploaded"],
}


@pytest.fixture(autouse=True)
def isolate_caches():
    cache.clear()
    load_models_file.cache_clear()
    load_prompt.cache_clear()
    embeddings._build.cache_clear()
    yield
    embeddings._build.cache_clear()


@pytest.fixture
def organization(user):
    organization = organization_services.create_organization(
        actor=user, name="Acme Africa", country="KE"
    )
    mail.outbox.clear()
    return organization


@pytest.fixture
def member(organization):
    user = UserFactory()
    OrganizationMembership.objects.create(organization=organization, user=user)
    return user


@pytest.fixture
def dossier_model(monkeypatch, settings):
    """Answer dossier calls with queued replies; returns the queue function and models."""
    models = []

    def queue(*replies):
        model = fake_chat_model(*replies)
        models.append(model)
        return model

    def factory(resolved):
        if not models:
            raise AssertionError(f"Unexpected model call for role {resolved.role}")
        return models.pop(0)

    monkeypatch.setitem(llm._PROVIDERS, "fake", factory)
    settings.AI_LLM_PROVIDER = "fake"
    queue.pending = models
    return queue


def docx_bytes(*paragraphs):
    return make_docx(list(paragraphs))
