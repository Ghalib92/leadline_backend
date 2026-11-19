"""Gemini as a chat model and as the fallback when the primary provider is rate-limited.
No network: Gemini is answered by the fake web; the primary is a scripted model."""

import json

import groq
import httpx
import pytest
from django.core.checks import run_checks

from apps.ai.config.loader import resolve_role
from apps.ai.exceptions import AIConfigError, RequestTooLarge, StructuredOutputError
from apps.ai.gemini import ChatGemini, GeminiError, inline_refs
from apps.ai.graphs.budget import Budget
from apps.ai.graphs.runtime import TaskRuntime
from apps.ai.llm import build_chat_model
from apps.ai.schemas.clients import ClientVerdicts
from apps.ai.structured import invoke_structured
from apps.ai.testing import use_scripted_models

HOST = "generativelanguage.googleapis.com"
VERDICTS = {"verdicts": [{"index": 1, "fit": 30, "reasons": ["A hotel"], "pitch_angle": "A site"}]}


def gemini_reply(payload, *, thought="Let me think…"):
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": thought, "thought": True},
                            {"text": json.dumps(payload)},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 120,
                "candidatesTokenCount": 40,
                "thoughtsTokenCount": 60,
                "totalTokenCount": 220,
            },
        },
    )


def model(**overrides):
    return ChatGemini(
        **{"model": "gemini-test", "api_key": "key", "max_retries": 1, "timeout": 5} | overrides
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("apps.ai.gemini.time.sleep", lambda seconds: None)


def test_structured_output_uses_gemini_json_schema(web):
    seen = []

    def handler(request):
        seen.append(request)
        return gemini_reply(VERDICTS)

    web.route(HOST, "/v1beta/models/gemini-test:generateContent", handler)
    result = invoke_structured(
        model(max_tokens=1000, thinking_budget=512), ["Score them"], ClientVerdicts
    )

    assert result.output.verdicts[0].fit == 30
    assert result.model == "gemini-test"
    assert result.usage == {"input_tokens": 120, "output_tokens": 100, "total_tokens": 220}
    request = seen[0]
    assert request.headers["x-goog-api-key"] == "key"
    body = json.loads(request.content)
    config = body["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["maxOutputTokens"] == 1512  # the answer keeps its room after thinking
    assert config["thinkingConfig"] == {"thinkingBudget": 512}
    assert "$defs" not in json.dumps(config["responseJsonSchema"])  # refs inlined


def test_gemini_retries_outages_then_answers(web):
    replies = iter([httpx.Response(503, json={}), gemini_reply(VERDICTS)])
    web.route(HOST, "", lambda request: next(replies))
    assert invoke_structured(model(), ["Score"], ClientVerdicts).output.verdicts


def test_a_rejected_key_is_a_config_error(web):
    web.route(HOST, "", lambda request: httpx.Response(403, json={"error": {"message": "no"}}))
    with pytest.raises(AIConfigError):
        invoke_structured(model(), ["Score"], ClientVerdicts)


def test_a_rate_limit_after_retries_is_a_gemini_error(web):
    web.route(HOST, "", lambda request: httpx.Response(429, json={"error": {"message": "q"}}))
    with pytest.raises(StructuredOutputError) as error:
        invoke_structured(model(), ["Score"], ClientVerdicts)
    assert isinstance(error.value.__cause__, GeminiError)
    assert error.value.rate_limited


def test_refs_are_inlined():
    schema = inline_refs(ClientVerdicts.model_json_schema())
    assert "$defs" not in schema
    assert schema["properties"]["verdicts"]["items"]["properties"]["fit"]["maximum"] == 40


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


def rate_limit(prompt):
    response = httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com/x"))
    return groq.RateLimitError("Rate limit reached for tokens per day", response=response, body={})


@pytest.fixture
def fallback(settings):
    settings.AI_FALLBACK_PROVIDER = "gemini"
    settings.GEMINI_API_KEY = "key"
    settings.AI_PRIVATE_DATA_PROVIDERS = ["groq", "gemini"]
    settings.AI_FALLBACK_TIERS = {
        "light": "gemini-lite",
        "research": "gemini-flash",
        "answer": "gemini-flash",
    }


def test_a_rate_limited_call_goes_to_the_fallback(monkeypatch, settings, fallback, web):
    use_scripted_models(monkeypatch, settings, {"client_judge": rate_limit})
    web.route(HOST, "/v1beta/models/gemini-flash:generateContent", lambda r: gemini_reply(VERDICTS))
    runtime = TaskRuntime(budget=Budget({"max_tokens": 50_000, "max_wall_seconds": 60}))

    verdicts = runtime.ask("client_judge", ClientVerdicts, organization="o", businesses="b")

    assert verdicts.verdicts[0].fit == 30
    primary = resolve_role("client_judge").model
    assert runtime.models_used["client_judge"] == f"{primary} + gemini-flash"
    assert runtime.budget.tokens == 220


def test_a_bad_answer_is_not_sent_to_the_fallback(monkeypatch, settings, fallback, web):
    use_scripted_models(monkeypatch, settings, {"client_judge": lambda prompt: "not json"})
    with pytest.raises(StructuredOutputError):
        invoke_structured(build_chat_model(resolve_role("client_judge")), ["x"], ClientVerdicts)
    assert web.count(HOST) == 0


def test_no_fallback_without_settings(settings):
    settings.AI_FALLBACK_PROVIDER = ""
    settings.GROQ_API_KEY = "gsk_test"
    assert resolve_role("judge").options == (("groq", "test-answer"),)


def test_fallback_settings_are_checked(settings):
    settings.AI_FALLBACK_PROVIDER = "gemini"
    settings.GEMINI_API_KEY = ""
    settings.AI_FALLBACK_TIERS = {"light": "", "research": "x", "answer": "x"}
    ids = {message.id for message in run_checks()}
    assert {"ai.W004", "ai.W005"} <= ids
    settings.AI_FALLBACK_PROVIDER = "nope"
    assert "ai.W003" in {message.id for message in run_checks()}


def too_large(prompt):
    return RequestTooLarge("Request too large for model on tokens per minute (TPM): Limit 8000")


def test_a_request_too_large_for_groq_goes_to_the_fallback(monkeypatch, settings, fallback, web):
    use_scripted_models(monkeypatch, settings, {"client_judge": too_large})
    web.route(HOST, "/v1beta/models/gemini-flash:generateContent", lambda r: gemini_reply(VERDICTS))
    runtime = TaskRuntime(budget=Budget({"max_tokens": 50_000, "max_wall_seconds": 60}))
    verdicts = runtime.ask("client_judge", ClientVerdicts, organization="o", businesses="b")
    assert verdicts.verdicts[0].fit == 30
    assert runtime.models_used["client_judge"].endswith(" + gemini-flash")


def test_a_request_too_large_is_not_retried_without_a_fallback(monkeypatch, settings):
    models = use_scripted_models(monkeypatch, settings, {"client_judge": too_large})
    with pytest.raises(StructuredOutputError) as error:
        invoke_structured(build_chat_model(resolve_role("client_judge")), ["x"], ClientVerdicts)
    assert isinstance(error.value.__cause__, RequestTooLarge)
    assert len(models["client_judge"].calls) == 1


@pytest.mark.parametrize(
    ("status", "raised"), [(413, RequestTooLarge), (400, groq.BadRequestError)]
)
def test_groq_names_its_request_too_large_refusal(monkeypatch, status, raised):
    from langchain_groq import ChatGroq

    from apps.ai.groq_chat import LeadlineChatGroq

    response = httpx.Response(status, request=httpx.Request("POST", "https://api.groq.com/x"))
    error_class = groq.BadRequestError if status == 400 else groq.APIStatusError

    def refuse(self, *args, **kwargs):
        raise error_class("Request too large", response=response, body={})

    monkeypatch.setattr(ChatGroq, "_generate", refuse)
    model = LeadlineChatGroq(model="m", api_key="gsk_test", max_retries=0)
    with pytest.raises(raised):
        model.invoke("hello")
