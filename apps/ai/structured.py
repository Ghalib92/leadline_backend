"""Structured LLM output: validated against a Pydantic schema and retried once.

Small models occasionally return malformed tool arguments or JSON. One retry fixes most
of those; a second failure is raised, so callers record it instead of storing junk.
Every failed attempt is logged with its cause, since callers show users only a short
message. Rate limits are retried by the provider client itself (`max_retries` in
models.yaml), before an attempt counts as failed here.
"""

import logging
from dataclasses import dataclass

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from apps.ai.exceptions import AIConfigError, RequestTooLarge, StructuredOutputError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    output: T
    usage: dict  # token counts reported by the provider, when it reports them
    model: str = ""  # the model that answered (a fallback's, when one stepped in)


def invoke_structured[T: BaseModel](
    model: BaseChatModel,
    messages,
    schema: type[T],
    *,
    attempts: int = 2,
    config=None,
    method: str | None = None,
) -> StructuredResult[T]:
    """`method` is the role's `structured_output` setting (models.yaml)."""
    if isinstance(messages, str):
        messages = [HumanMessage(messages)]
    options = {"method": method} if method else {}
    runnable = model.with_structured_output(schema, include_raw=True, **options)
    error = None
    last_answer = ""  # the text of the last unusable answer, for the correction
    usage: dict = {}
    for attempt in range(1, attempts + 1):
        # A retry says what went wrong: asking the same again repeats the same mistake
        # (gpt-oss sometimes answers with the JSON schema itself, not the data).
        attempt_messages = (
            [*messages, _correction(error, last_answer)] if _bad_answer(error) else messages
        )
        try:
            result = runnable.invoke(attempt_messages, config=config)
        except AIConfigError:
            raise
        except RequestTooLarge as exc:  # the same request again can't fit either
            error = exc
            _log_failure(schema, attempt, attempts, exc)
            break
        except Exception as exc:  # provider-specific "tool_use_failed" or parse errors
            error = exc
            _log_failure(schema, attempt, attempts, exc)
            continue
        parsed = result.get("parsed")
        _add_usage(usage, getattr(result.get("raw"), "usage_metadata", None) or {})
        if result.get("parsing_error") is None and isinstance(parsed, schema):
            metadata = getattr(result.get("raw"), "response_metadata", None) or {}
            return StructuredResult(
                output=parsed, usage=usage, model=str(metadata.get("model_name") or "")
            )
        error = result.get("parsing_error") or ValueError("The model returned no output.")
        last_answer = str(getattr(result.get("raw"), "content", "") or "")
        _log_failure(schema, attempt, attempts, error, raw=result.get("raw"))
    raise StructuredOutputError(
        f"No valid {schema.__name__} after {attempts} attempts: {error}", usage=usage
    ) from error


def _bad_answer(error) -> bool:
    """The model answered, but not in the required form (not a rate limit or outage)."""
    if error is None:
        return False
    status = getattr(error, "status_code", None)
    return status == 400 or (status is None and "RateLimit" not in type(error).__name__)


def _correction(error, answer: str = "") -> HumanMessage:
    text = f"{error} {answer}"
    if '"properties"' in text and '"description"' in text:
        problem = "you returned the JSON schema itself instead of the data"
    else:
        problem = f"it did not match the required format ({str(error)[:300]})"
    return HumanMessage(
        f"Your previous answer could not be used: {problem}. Answer again with only "
        "the JSON object that fills in the fields, following the rules above."
    )


def _log_failure(schema, attempt, attempts, error, raw=None) -> None:
    """One line per failed attempt: the schema, the error type and message, and why the
    model stopped (e.g. "length" when max_tokens cut the answer off)."""
    metadata = getattr(raw, "response_metadata", None) or {}
    logger.warning(
        "Structured output %s failed (attempt %s of %s): %s: %s%s",
        schema.__name__,
        attempt,
        attempts,
        type(error).__name__,
        str(error)[:500],
        f" [finish_reason={metadata['finish_reason']}]" if "finish_reason" in metadata else "",
    )


def _add_usage(total: dict, usage) -> None:
    """Retries cost tokens too, so usage is summed over attempts."""
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        total[key] = total.get(key, 0) + int(usage.get(key) or 0)
