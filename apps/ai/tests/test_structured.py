import pytest
from pydantic import BaseModel

from apps.ai.exceptions import StructuredOutputError
from apps.ai.structured import invoke_structured
from apps.ai.testing import fake_chat_model


class Verdict(BaseModel):
    score: int


def test_valid_output_on_the_first_try():
    model = fake_chat_model({"score": 7})
    result = invoke_structured(model, "rate it", Verdict)
    assert result.output == Verdict(score=7)
    assert len(model.calls) == 1


def test_invalid_output_is_retried_once():
    model = fake_chat_model('{"score": "high"}', {"score": 3})
    assert invoke_structured(model, "rate it", Verdict).output.score == 3
    assert len(model.calls) == 2


def test_gives_up_after_the_retry():
    model = fake_chat_model("nope", "still nope", {"score": 1})
    with pytest.raises(StructuredOutputError, match="No valid Verdict after 2 attempts"):
        invoke_structured(model, "rate it", Verdict)
    assert len(model.calls) == 2


def test_the_structured_output_method_is_passed_through():
    seen = {}
    model = fake_chat_model({"score": 2})
    original = model.with_structured_output

    def spy(schema, **kwargs):
        seen.update(kwargs)
        return original(schema, **kwargs)

    object.__setattr__(model, "with_structured_output", spy)
    invoke_structured(model, "rate it", Verdict, method="json_schema")
    assert seen == {"include_raw": True, "method": "json_schema"}


def test_usage_is_summed_over_retries():
    model = fake_chat_model("nope", {"score": 1})
    result = invoke_structured(model, "rate it", Verdict)
    assert set(result.usage) == {"input_tokens", "output_tokens", "total_tokens"}


def test_each_failed_attempt_is_logged_with_its_cause(caplog):
    model = fake_chat_model('{"score": "high"}', {"score": 3})
    with caplog.at_level("WARNING", logger="apps.ai.structured"):
        invoke_structured(model, "rate it", Verdict)
    [record] = caplog.records
    assert "Structured output Verdict failed (attempt 1 of 2)" in record.getMessage()


def test_a_brief_with_a_misspelt_meets_is_kept():
    from apps.ai.schemas.discovery import OpportunityBrief

    brief = OpportunityBrief.model_validate(
        {
            "what_it_is": "A system.",
            "why_it_fits": "Software.",
            "eligibility_checklist": [{"criterion": "Capacity", "meeds": True, "notes": ""}],
            "documents_needed": [],
            "deadline_plan": "",
            "effort_estimate": "high",
            "next_step": "Read the tender.",
        }
    )
    assert brief.eligibility_checklist[0].meets is False


def test_a_retry_says_what_went_wrong():
    """gpt-oss sometimes answers with the schema itself; asking the same again repeats it."""
    schema_echo = '{"description": "", "properties": {"score": {"type": "integer"}}}'
    model = fake_chat_model(schema_echo, {"score": 4})
    assert invoke_structured(model, "rate it", Verdict).output.score == 4
    retry = model.calls[1]
    assert retry[0].content == "rate it"
    assert "returned the JSON schema itself instead of the data" in retry[-1].content


def test_a_rate_limit_is_retried_without_a_correction():
    import httpx
    from groq import RateLimitError

    response = httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com/x"))
    calls = []

    class Flaky:
        def with_structured_output(self, schema, **kwargs):
            from langchain_core.runnables import RunnableLambda

            def run(messages):
                calls.append(messages)
                if len(calls) == 1:
                    raise RateLimitError("slow down", response=response, body={})
                return {"raw": None, "parsed": Verdict(score=2), "parsing_error": None}

            return RunnableLambda(run)

    assert invoke_structured(Flaky(), "rate it", Verdict).output.score == 2
    assert len(calls[1]) == 1  # the same request, no "previous answer" message
