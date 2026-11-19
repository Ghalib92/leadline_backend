"""Backup API keys (GROQ_API_KEY_2, GEMINI_API_KEY_2): a call the first key can't make,
for its rate limit or because the key was rejected, is made with the backup key on the
same model, before the fallback provider. No network: Groq's client is patched and
Gemini is answered by the fake web."""

import groq
import httpx
import pytest
from django.core.checks import run_checks
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_groq import ChatGroq

from apps.ai.config.loader import provider_keys, resolve_role
from apps.ai.exceptions import KeyRejected, RequestTooLarge
from apps.ai.llm import build_chat_model

HOST = "generativelanguage.googleapis.com"


def groq_error(kind, status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.groq.com/x"))
    return kind("refused", response=response, body={})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("apps.ai.gemini.time.sleep", lambda seconds: None)


@pytest.fixture
def groq_keys(monkeypatch, settings):
    """Two Groq keys; `refusals[key]` is what that key's calls raise. Records the key
    of every call."""
    settings.GROQ_API_KEY = "gsk_first"
    settings.GROQ_API_KEY_2 = "gsk_second"
    calls, refusals = [], {}

    def generate(self, messages, stop=None, run_manager=None, **kwargs):
        key = self.groq_api_key.get_secret_value()
        calls.append(key)
        if key in refusals:
            raise refusals[key]
        message = AIMessage(f"answer from {key}", response_metadata={"model_name": self.model_name})
        return ChatResult(generations=[ChatGeneration(message=message)])

    monkeypatch.setattr(ChatGroq, "_generate", generate)
    return calls, refusals


@pytest.fixture
def gemini_keys(settings):
    settings.AI_FALLBACK_PROVIDER = "gemini"
    settings.GEMINI_API_KEY = "gem_first"
    settings.AI_PRIVATE_DATA_PROVIDERS = ["groq", "gemini"]
    settings.GEMINI_API_KEY_2 = "gem_second"
    settings.AI_FALLBACK_TIERS = {"light": "g-lite", "research": "g-flash", "answer": "g-flash"}


def gemini_text(text):
    return httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": text}]}}], "usageMetadata": {}}
    )


def model(role="judge"):
    return build_chat_model(resolve_role(role))


@pytest.mark.parametrize(
    "refusal",
    [
        groq_error(groq.RateLimitError, 429),
        groq_error(groq.AuthenticationError, 401),
        groq_error(groq.PermissionDeniedError, 403),
    ],
)
def test_groq_uses_the_backup_key_when_the_first_cannot(groq_keys, refusal):
    calls, refusals = groq_keys
    refusals["gsk_first"] = refusal
    assert model().invoke("ping").content == "answer from gsk_second"
    assert calls == ["gsk_first", "gsk_second"]


def test_the_backup_key_is_not_used_while_the_first_works(groq_keys):
    calls, _ = groq_keys
    assert model().invoke("ping").content == "answer from gsk_first"
    assert calls == ["gsk_first"]


def test_both_groq_keys_exhausted_goes_to_gemini(groq_keys, gemini_keys, web):
    calls, refusals = groq_keys
    refusals["gsk_first"] = groq_error(groq.RateLimitError, 429)
    refusals["gsk_second"] = groq_error(groq.RateLimitError, 429)
    web.route(HOST, "/v1beta/models/g-flash:generateContent", lambda r: gemini_text("gemini"))
    assert model().invoke("ping").content == "gemini"
    assert calls == ["gsk_first", "gsk_second"]


def test_a_request_too_large_skips_the_backup_groq_key(groq_keys, gemini_keys, web):
    """Both keys are on the same plan, so the backup would refuse the size too."""
    calls, refusals = groq_keys
    refusals["gsk_first"] = RequestTooLarge("Request too large")
    web.route(HOST, "/v1beta/models/g-flash:generateContent", lambda r: gemini_text("gemini"))
    assert model().invoke("ping").content == "gemini"
    assert calls == ["gsk_first"]


INVALID_KEY = {
    "error": {
        "code": 400,
        "message": "API key not valid. Please pass a valid API key.",
        "details": [{"reason": "API_KEY_INVALID"}],
    }
}


@pytest.mark.parametrize(
    "refusal",
    [
        (429, {"error": {"message": "quota"}}),
        (403, {"error": {"message": "blocked"}}),
        (400, INVALID_KEY),  # how Gemini answers a wrong key
    ],
)
def test_gemini_uses_its_backup_key(groq_keys, gemini_keys, web, refusal):
    calls, refusals = groq_keys
    refusals["gsk_first"] = refusals["gsk_second"] = groq_error(groq.RateLimitError, 429)
    seen = []

    def reply(request):
        key = request.headers["x-goog-api-key"]
        seen.append(key)
        if key == "gem_first":
            return httpx.Response(refusal[0], json=refusal[1])
        return gemini_text("second gemini key")

    web.route(HOST, "/v1beta/models/g-flash:generateContent", reply)
    assert model().invoke("ping").content == "second gemini key"
    assert seen[-1] == "gem_second" and set(seen) == {"gem_first", "gem_second"}


def test_every_key_rejected_is_a_config_error(settings, web, gemini_keys):
    settings.AI_LLM_PROVIDER = "gemini"
    settings.AI_MODEL_TIERS = {"light": "g-lite", "research": "g-flash", "answer": "g-flash"}
    web.route(HOST, "", lambda r: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(KeyRejected):
        model().invoke("ping")
    assert web.count(HOST) == 2


def test_a_backup_key_alone_is_enough(settings):
    settings.GROQ_API_KEY = ""
    settings.GROQ_API_KEY_2 = "gsk_second"
    built = model()
    assert isinstance(built, ChatGroq)
    assert built.groq_api_key.get_secret_value() == "gsk_second"
    assert "ai.W002" not in {message.id for message in run_checks()}


def test_the_same_key_twice_is_used_once(settings):
    settings.GROQ_API_KEY = settings.GROQ_API_KEY_2 = "gsk_same"
    assert isinstance(model(), ChatGroq)
    assert provider_keys("groq") == [("GROQ_API_KEY", "gsk_same")]
