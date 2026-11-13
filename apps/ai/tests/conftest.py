import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from apps.ai import llm
from apps.ai.config.loader import load_models_file, load_sources_file
from apps.ai.prompts.loader import load_prompt


@pytest.fixture(autouse=True)
def clear_loader_caches():
    load_models_file.cache_clear()
    load_sources_file.cache_clear()
    load_prompt.cache_clear()
    yield
    load_models_file.cache_clear()
    load_sources_file.cache_clear()
    load_prompt.cache_clear()


@pytest.fixture
def fake_provider(monkeypatch, settings):
    """Route model construction to an in-memory fake; records what was resolved."""
    built = []

    def factory(resolved):
        built.append(resolved)
        return GenericFakeChatModel(messages=iter([AIMessage(content="pong")]))

    monkeypatch.setitem(llm._PROVIDERS, "fake", factory)
    settings.AI_LLM_PROVIDER = "fake"
    return built
