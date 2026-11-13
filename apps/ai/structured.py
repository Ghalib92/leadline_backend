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
from pydantic import BaseModel

from apps.ai.exceptions import AIConfigError, StructuredOutputError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    output: T
    usage: dict  # token counts reported by the provider, when it reports them


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
    options = {"method": method} if method else {}
    runnable = model.with_structured_output(schema, include_raw=True, **options)
    error = None
    usage: dict = {}
    for attempt in range(1, attempts + 1):
        try:
            result = runnable.invoke(messages, config=config)
        except AIConfigError:
            raise
        except Exception as exc:  # provider-specific "tool_use_failed" or parse errors
            error = exc
            _log_failure(schema, attempt, attempts, exc)
            continue
        parsed = result.get("parsed")
        _add_usage(usage, getattr(result.get("raw"), "usage_metadata", None) or {})
        if result.get("parsing_error") is None and isinstance(parsed, schema):
            return StructuredResult(output=parsed, usage=usage)
        error = result.get("parsing_error") or ValueError("The model returned no output.")
        _log_failure(schema, attempt, attempts, error, raw=result.get("raw"))
    raise StructuredOutputError(
        f"No valid {schema.__name__} after {attempts} attempts: {error}", usage=usage
    ) from error


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
