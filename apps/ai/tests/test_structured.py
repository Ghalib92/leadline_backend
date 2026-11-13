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
