import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.ai import checks
from apps.ai.events import ListEmitter, NullEmitter, RunEvent


def check_ids():
    return [message.id for message in checks.ai_configuration(None)]


def test_configured_settings_pass(settings):
    settings.GROQ_API_KEY = "gsk_test"
    assert check_ids() == []


def test_missing_key_and_model_are_warnings(settings):
    settings.GROQ_API_KEY = ""
    settings.AI_MODEL_TIERS = {**settings.AI_MODEL_TIERS, "light": ""}
    assert sorted(check_ids()) == ["ai.W001", "ai.W002"]


def test_unknown_provider_is_an_error(settings):
    settings.AI_LLM_PROVIDER = "nowhere"
    assert "ai.E002" in check_ids()


def test_broken_models_file_is_an_error(monkeypatch):
    from apps.ai.exceptions import AIConfigError

    def broken():
        raise AIConfigError("models.yaml is invalid")

    monkeypatch.setattr(checks, "load_models_file", broken)
    assert check_ids() == ["ai.E001"]


def test_ai_ping_reports_each_tier(fake_provider, capsys):
    call_command("ai_ping")
    out = capsys.readouterr().out
    for tier in ("light", "research", "answer"):
        assert f"test-{tier}" in out
    assert "All 3 checks passed." in out
    assert {r.tier for r in fake_provider} == {"light", "research", "answer"}


def test_ai_ping_fails_loudly(settings, monkeypatch):
    from apps.ai import llm

    def failing(resolved):
        raise RuntimeError("401 invalid api key")

    monkeypatch.setitem(llm._PROVIDERS, "fake", failing)
    settings.AI_LLM_PROVIDER = "fake"
    with pytest.raises(CommandError, match="1 check\\(s\\) failed: light/fake/-"):
        call_command("ai_ping", tiers=["light"])


def test_emitters():
    emitter = ListEmitter()
    event = RunEvent(step="search", message="Searching grants.gov", tool="grants_gov")
    emitter.emit(event)
    NullEmitter().emit(event)
    assert emitter.events == [event]
    assert event.level == "info" and event.at is not None
