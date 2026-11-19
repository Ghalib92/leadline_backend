"""Each role's chain of providers (models.yaml `providers`, then the default order),
takeover when one can't answer, resting a rate-limited key, and the daily model check.
No network: Groq's client is patched and Gemini is answered by the fake web."""

import groq
import httpx
import pytest
from django.core import mail
from django.core.checks import run_checks
from django.core.management import call_command
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_groq import ChatGroq

from apps.ai import cooldown
from apps.ai.config.loader import load_models_file, resolve_role
from apps.ai.exceptions import ProviderCooling
from apps.ai.gemini import ChatGemini
from apps.ai.llm import build_chat_model

HOST = "generativelanguage.googleapis.com"


def groq_error(kind, status, headers=None):
    response = httpx.Response(
        status, headers=headers or {}, request=httpx.Request("POST", "https://api.groq.com/x")
    )
    return kind("refused", response=response, body={})


def gemini_text(text):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("apps.ai.gemini.time.sleep", lambda seconds: None)


@pytest.fixture
def both(monkeypatch, settings):
    """Groq and Gemini configured, two keys each. `refusals[key]` is what that Groq key
    raises; `calls` records each Groq call's key."""
    settings.GROQ_API_KEY, settings.GROQ_API_KEY_2 = "gsk_1", "gsk_2"
    settings.GEMINI_API_KEY, settings.GEMINI_API_KEY_2 = "gem_1", "gem_2"
    settings.AI_FALLBACK_PROVIDER = "gemini"
    settings.AI_FALLBACK_TIERS = {"light": "g-lite", "research": "g-lite", "answer": "g-flash"}
    calls, refusals = [], {}

    def generate(self, messages, stop=None, run_manager=None, **kwargs):
        key = self.groq_api_key.get_secret_value()
        calls.append(key)
        if key in refusals:
            raise refusals[key]
        message = AIMessage(f"groq {key}", response_metadata={"model_name": self.model_name})
        return ChatResult(generations=[ChatGeneration(message=message)])

    monkeypatch.setattr(ChatGroq, "_generate", generate)
    return calls, refusals


def gemini_by_key(web, replies):
    """Gemini answers per key: replies[key] is a Response, default "gemini <key>"."""
    seen = []

    def reply(request):
        key = request.headers["x-goog-api-key"]
        seen.append(key)
        return replies.get(key) or gemini_text(f"gemini {key}")

    web.route(HOST, "", reply)
    return seen


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------


def test_a_role_with_preferred_providers_tries_them_first(both):
    assert resolve_role("dossier").options == (
        ("gemini", "g-flash"),
        ("groq", "test-answer"),
    )
    assert resolve_role("judge").options == (("groq", "test-answer"), ("gemini", "g-flash"))
    assert resolve_role("cv_reader").provider == "gemini"


def test_role_providers_can_be_switched_off(both, settings):
    settings.AI_ROLE_PROVIDERS = False
    assert resolve_role("dossier").options[0] == ("groq", "test-answer")


def test_a_provider_without_a_key_is_left_out(both, settings):
    settings.GEMINI_API_KEY = settings.GEMINI_API_KEY_2 = ""
    assert resolve_role("dossier").options == (("groq", "test-answer"),)


def test_a_provider_model_can_be_set_per_provider(both, settings):
    settings.AI_PROVIDER_MODELS = {"groq": {}, "gemini": {"answer": "g-pro"}}
    assert resolve_role("dossier").model == "g-pro"


def test_every_role_resolves_with_both_providers(both):
    for role in load_models_file().roles:
        providers = [provider for provider, _ in resolve_role(role).options]
        assert sorted(providers) == ["gemini", "groq"], role


def test_an_unknown_provider_in_models_yaml_is_an_error(settings, monkeypatch):
    from apps.ai import checks

    models = load_models_file().model_copy(deep=True)
    models.roles["judge"].providers = ["claude"]
    monkeypatch.setattr(checks, "load_models_file", lambda: models)
    assert "ai.E004" in {message.id for message in run_checks()}


# ---------------------------------------------------------------------------
# Takeover
# ---------------------------------------------------------------------------


def test_a_gemini_first_role_goes_to_groq_when_both_gemini_keys_are_spent(both, web):
    calls, _ = both
    spent = httpx.Response(429, json={"error": {"message": "quota"}})
    seen = gemini_by_key(web, {"gem_1": spent, "gem_2": spent})
    assert build_chat_model(resolve_role("dossier")).invoke("x").content == "groq gsk_1"
    assert seen == ["gem_1", "gem_2"]  # no retries: a later option exists
    assert calls == ["gsk_1"]


def test_every_option_in_turn(both, web):
    calls, refusals = both
    refusals["gsk_1"] = refusals["gsk_2"] = groq_error(groq.RateLimitError, 429)
    seen = gemini_by_key(web, {"gem_1": httpx.Response(429, json={"error": {}})})
    assert build_chat_model(resolve_role("judge")).invoke("x").content == "gemini gem_2"
    assert calls == ["gsk_1", "gsk_2"]
    assert seen == ["gem_1", "gem_2"]


def test_an_outage_skips_the_backup_key_of_the_same_provider(both, web):
    """Gemini's 503 ("high demand") is the model's, not the key's."""
    calls, refusals = both
    refusals["gsk_1"] = groq_error(groq.InternalServerError, 503)
    seen = gemini_by_key(web, {})
    assert build_chat_model(resolve_role("judge")).invoke("x").content == "gemini gem_1"
    assert calls == ["gsk_1"]
    assert seen == ["gem_1"]


def test_only_the_last_option_retries(both):
    built = build_chat_model(resolve_role("judge"))
    groq_1, groq_2 = built.runnable.runnable, built.runnable.fallbacks[0]
    gemini_1, gemini_2 = built.fallbacks[0].runnable, built.fallbacks[0].fallbacks[0]
    assert [m.max_retries for m in (groq_1, groq_2, gemini_1)] == [0, 0, 0]
    assert all(m.has_backup for m in (groq_1, groq_2, gemini_1))
    assert gemini_2.max_retries == 2 and not gemini_2.has_backup
    assert gemini_2.key_name == "GEMINI_API_KEY_2"


# ---------------------------------------------------------------------------
# Resting a rate-limited key
# ---------------------------------------------------------------------------


def test_a_rate_limited_key_is_skipped_on_the_next_call(both):
    calls, refusals = both
    refusals["gsk_1"] = groq_error(groq.RateLimitError, 429, {"retry-after": "120"})
    model = build_chat_model(resolve_role("judge"))
    assert model.invoke("x").content == "groq gsk_2"
    del refusals["gsk_1"]  # even if it would answer now, it rests as Groq asked
    assert model.invoke("x").content == "groq gsk_2"
    assert calls == ["gsk_1", "gsk_2", "gsk_2"]


def test_the_last_option_is_called_even_while_resting(settings, monkeypatch):
    settings.GROQ_API_KEY = "gsk_only"
    cooldown.cool("groq", "gsk_only", "test-answer", "GROQ_API_KEY", 600)
    monkeypatch.setattr(
        ChatGroq,
        "_generate",
        lambda self, *a, **k: ChatResult(generations=[ChatGeneration(message=AIMessage("ok"))]),
    )
    assert build_chat_model(resolve_role("judge")).invoke("x").content == "ok"


def test_gemini_rests_a_key_for_its_retry_delay(web, monkeypatch):
    rested = []
    monkeypatch.setattr(cooldown, "cool", lambda *args: rested.append(args))
    body = {
        "error": {
            "message": "quota",
            "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}],
        }
    }
    web.route(HOST, "", lambda r: httpx.Response(429, json=body))
    model = ChatGemini(model="g", api_key="k", key_name="GEMINI_API_KEY", max_retries=0)
    with pytest.raises(Exception, match="429"):
        model.invoke("x")
    assert rested == [("gemini", "k", "g", "GEMINI_API_KEY", 37.0)]


def test_a_resting_gemini_key_is_skipped_without_a_call(web):
    cooldown.cool("gemini", "k", "g", "GEMINI_API_KEY", 60)
    model = ChatGemini(model="g", api_key="k", has_backup=True)
    with pytest.raises(ProviderCooling):
        model.invoke("x")
    assert web.count(HOST) == 0


def test_rest_is_capped(settings, monkeypatch):
    settings.AI_COOLDOWN_MAX_SECONDS = 900
    timeouts = []
    monkeypatch.setattr(cooldown.cache, "set", lambda key, value, timeout: timeouts.append(timeout))
    cooldown.cool("groq", "k", "m", "GROQ_API_KEY", 86_400)
    cooldown.cool("groq", "k", "m", "GROQ_API_KEY", None)
    assert timeouts == [900, cooldown.DEFAULT_SECONDS]


# ---------------------------------------------------------------------------
# The model check
# ---------------------------------------------------------------------------


def test_ai_ping_checks_each_provider_tier_and_key(both, web, capsys):
    calls, _ = both
    gemini_by_key(web, {})
    call_command("ai_ping", tiers=["answer"])
    out = capsys.readouterr().out
    assert "GROQ_API_KEY_2" in out and "GEMINI_API_KEY_2" in out
    assert "All 4 checks passed." in out
    assert calls == ["gsk_1", "gsk_2"]


def test_ai_ping_lists_each_roles_order(both, capsys):
    call_command("ai_ping", roles=True)
    out = capsys.readouterr().out
    assert "dossier" in out and "gemini:g-flash  ->  groq:test-answer" in out


def test_the_daily_check_emails_when_a_key_or_model_is_broken(both, web, settings):
    from apps.ai.tasks import check_models_daily

    settings.AI_ALERT_EMAILS = ["ops@example.com"]
    _, refusals = both
    refusals["gsk_2"] = groq_error(groq.AuthenticationError, 401)
    gemini_by_key(web, {"gem_1": httpx.Response(404, json={"error": {"message": "retired"}})})
    check_models_daily()
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert "light/groq/GROQ_API_KEY_2" in body and "light/gemini/GEMINI_API_KEY" in body
    assert "gsk_" not in body  # never the key itself


def test_the_daily_check_stays_quiet_about_rate_limits(both, web, settings):
    from apps.ai.tasks import check_models_daily

    settings.AI_ALERT_EMAILS = ["ops@example.com"]
    _, refusals = both
    refusals["gsk_1"] = groq_error(groq.RateLimitError, 429)
    gemini_by_key(web, {})
    check_models_daily()
    assert mail.outbox == []


def test_the_daily_check_is_scheduled():
    from config.temporal import timers

    assert "ai-model-check" in timers()
