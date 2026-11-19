"""Users' private data (company documents and summary, CVs, drafts) goes only to
AI_PRIVATE_DATA_PROVIDERS. Gemini's free tier isn't one: Google uses its prompts to
improve its products. Roles that read only public content may use any provider."""

import pytest
from django.core.checks import run_checks

from apps.ai.agents.dossier import generate_dossier
from apps.ai.config.loader import load_models_file, resolve_role
from apps.ai.exceptions import AIConfigError, RequestTooLarge
from apps.ai.testing import use_scripted_models

PUBLIC_ROLES = {
    "verifier",
    "source_reader",
    "tender_requirements",
    "proposal_requirements",
    "lead_scout",
}


@pytest.fixture
def both(settings):
    settings.GROQ_API_KEY = "gsk"
    settings.GEMINI_API_KEY = "gem"
    settings.AI_FALLBACK_PROVIDER = "gemini"
    settings.AI_FALLBACK_TIERS = {"light": "g-lite", "research": "g-lite", "answer": "g-flash"}


def providers(role):
    return [provider for provider, _ in resolve_role(role).options]


def test_private_roles_stay_off_free_gemini(both):
    for role in set(load_models_file().roles) - PUBLIC_ROLES:
        assert providers(role) == ["groq"], role


def test_public_roles_may_use_gemini(both):
    assert providers("tender_requirements") == ["gemini", "groq"]
    assert providers("verifier") == ["groq", "gemini"]


def test_only_the_roles_that_read_public_content_are_public():
    """A new role is private unless someone decides otherwise."""
    marked = {name for name, spec in load_models_file().roles.items() if spec.public_inputs}
    assert marked == PUBLIC_ROLES


def test_listing_gemini_restores_its_place(both, settings):
    settings.AI_PRIVATE_DATA_PROVIDERS = ["groq", "gemini"]
    assert providers("dossier") == ["gemini", "groq"]
    assert providers("judge") == ["groq", "gemini"]


def test_a_private_role_with_no_private_provider_is_a_config_error(both, settings):
    settings.AI_PRIVATE_DATA_PROVIDERS = []
    with pytest.raises(AIConfigError, match="AI_PRIVATE_DATA_PROVIDERS"):
        resolve_role("judge")
    assert providers("verifier") == ["groq", "gemini"]


def test_the_check_warns_when_no_private_provider_has_a_key(both, settings):
    settings.AI_PRIVATE_DATA_PROVIDERS = ["gemini"]
    settings.GEMINI_API_KEY = ""
    assert "ai.W006" in {message.id for message in run_checks()}


DOSSIER = {"summary": "A software firm in Nairobi."}


def test_a_dossier_too_large_for_the_plan_is_retried_with_shorter_excerpts(monkeypatch, settings):
    lengths = []

    def respond(prompt):
        lengths.append(len(prompt))
        return RequestTooLarge("Request too large") if len(lengths) == 1 else DOSSIER

    use_scripted_models(monkeypatch, settings, {"dossier": respond})
    inputs = {
        "profile": {"name": "Acme"},
        "preferences": {},
        "documents": [{"title": "Report", "category": "report", "excerpt": "x" * 4000}],
    }
    result = generate_dossier(inputs)
    assert result.dossier.summary == "A software firm in Nairobi."
    assert lengths[1] < lengths[0] - 1500  # the excerpt was halved


def test_a_dossier_still_too_large_fails(monkeypatch, settings):
    from apps.ai.exceptions import StructuredOutputError

    use_scripted_models(
        monkeypatch, settings, {"dossier": lambda prompt: RequestTooLarge("too large")}
    )
    inputs = {
        "profile": {},
        "preferences": {},
        "documents": [{"title": "R", "category": "c", "excerpt": "x" * 100}],
    }
    with pytest.raises(StructuredOutputError):
        generate_dossier(inputs)
