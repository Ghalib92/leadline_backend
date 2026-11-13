import pytest
from langchain_groq import ChatGroq

from apps.ai.config.loader import resolve_role
from apps.ai.exceptions import AIConfigError
from apps.ai.llm import build_chat_model, get_chat_model


def test_get_chat_model_builds_the_resolved_role(fake_provider):
    model = get_chat_model("judge")
    assert model.invoke("ping").content == "pong"
    assert [(r.role, r.tier, r.model) for r in fake_provider] == [
        ("judge", "answer", "test-answer")
    ]


def test_unknown_provider_is_a_config_error(settings):
    settings.AI_LLM_PROVIDER = "nowhere"
    with pytest.raises(AIConfigError, match="Unknown AI_LLM_PROVIDER 'nowhere'"):
        get_chat_model("judge")


def test_groq_requires_an_api_key(settings):
    settings.GROQ_API_KEY = ""
    with pytest.raises(AIConfigError, match="GROQ_API_KEY"):
        get_chat_model("judge")


def test_groq_model_receives_role_settings(settings):
    settings.GROQ_API_KEY = "gsk_test"
    model = build_chat_model(resolve_role("drafter"))
    assert isinstance(model, ChatGroq)
    assert model.model_name == "test-answer"
    assert model.max_tokens == 8000
    assert model.reasoning_effort == "medium"
    assert model.request_timeout == 180
    assert model.max_retries == 2
    assert model.groq_api_key.get_secret_value() == "gsk_test"
