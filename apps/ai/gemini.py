"""Google Gemini as a LangChain chat model, over its REST API (no SDK needed).

Used as the fallback provider (AI_FALLBACK_PROVIDER=gemini): when the primary provider
is rate-limited or down, the same structured call goes here. Structured outputs use
Gemini's JSON-schema response format. Built only by apps.ai.llm.
"""

import json
import time

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from apps.ai import cooldown
from apps.ai.exceptions import KeyRejected
from apps.ai.tools.http import get_http_client

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class GeminiError(Exception):
    """Gemini refused or failed. `status_code` 429 means its rate limit."""

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GeminiRateLimited(GeminiError):
    """Gemini's rate limit or daily cap for this key (HTTP 429)."""


class ChatGemini(BaseChatModel):
    model: str
    api_key: str
    temperature: float | None = None
    max_tokens: int | None = None
    thinking_budget: int | None = None  # tokens the model may think before answering
    timeout: float = 60
    max_retries: int = 2
    key_name: str = "GEMINI_API_KEY"  # for logs; never the key itself
    has_backup: bool = False  # a later option exists: rest this key when limited

    @property
    def _llm_type(self) -> str:
        return "gemini"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self.has_backup:
            cooldown.skip_if_cooling("gemini", self.api_key, self.model, self.key_name)
        data = self._post(self._body(messages, kwargs.get("response_json_schema")))
        candidate = (data.get("candidates") or [{}])[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        usage = data.get("usageMetadata") or {}
        output = int(usage.get("candidatesTokenCount") or 0)
        output += int(usage.get("thoughtsTokenCount") or 0)
        message = AIMessage(
            content=text,
            usage_metadata={
                "input_tokens": int(usage.get("promptTokenCount") or 0),
                "output_tokens": output,
                "total_tokens": int(usage.get("totalTokenCount") or 0),
            },
            response_metadata={
                "model_name": self.model,
                "finish_reason": candidate.get("finishReason", ""),
            },
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def with_structured_output(self, schema, *, include_raw: bool = False, **kwargs):
        """JSON matching `schema` (a Pydantic model); `method` is ignored."""
        bound = self.bind(response_json_schema=inline_refs(schema.model_json_schema()))

        def parse(message):
            try:
                parsed, error = schema.model_validate_json(message.content or ""), None
            except ValidationError as exc:
                parsed, error = None, exc
            if include_raw:
                return {"raw": message, "parsed": parsed, "parsing_error": error}
            if error is not None:
                raise error
            return parsed

        return bound | RunnableLambda(parse)

    # -- internals ---------------------------------------------------------------------

    def _body(self, messages, response_schema) -> dict:
        system = [str(m.content) for m in messages if isinstance(m, SystemMessage)]
        contents = [
            {
                "role": "model" if isinstance(m, AIMessage) else "user",
                "parts": [{"text": str(m.content)}],
            }
            for m in messages
            if not isinstance(m, SystemMessage)
        ]
        config: dict = {}
        if self.temperature is not None:
            config["temperature"] = self.temperature
        if self.max_tokens:
            # Thinking counts against the output limit, so the answer keeps its room.
            config["maxOutputTokens"] = self.max_tokens + (self.thinking_budget or 0)
        if self.thinking_budget is not None:
            config["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
        if response_schema:
            config["responseMimeType"] = "application/json"
            config["responseJsonSchema"] = response_schema
        body = {"contents": contents, "generationConfig": config}
        if system:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
        return body

    def _post(self, body: dict) -> dict:
        url = ENDPOINT.format(model=self.model)
        headers = {"x-goog-api-key": self.api_key}
        for attempt in range(self.max_retries + 1):
            try:
                with get_http_client(timeout=self.timeout) as client:
                    response = client.post(url, json=body, headers=headers)
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    time.sleep(2**attempt)
                    continue
                raise GeminiError(f"Gemini could not be reached ({type(exc).__name__}).") from exc
            if response.status_code in RETRY_STATUSES and attempt < self.max_retries:
                time.sleep(2**attempt)
                continue
            # A wrong key is a 400 with reason API_KEY_INVALID; a revoked or blocked one
            # is a 401 or 403.
            if response.status_code in (401, 403) or "API_KEY_INVALID" in response.text:
                raise KeyRejected("A GEMINI_API_KEY was rejected.")
            if response.status_code >= 400:
                if response.status_code == 429:
                    cooldown.cool(
                        "gemini", self.api_key, self.model, self.key_name, _retry_delay(response)
                    )
                error = GeminiRateLimited if response.status_code == 429 else GeminiError
                raise error(
                    f"Gemini returned HTTP {response.status_code}: {_error(response)}",
                    status_code=response.status_code,
                )
            return response.json()
        raise GeminiError("Gemini failed after retries.")  # pragma: no cover


def inline_refs(schema: dict) -> dict:
    """The schema with every "$ref" to "$defs" replaced by the definition itself, which
    Gemini's response schema handles more reliably. Our schemas are not recursive."""
    definitions = schema.get("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                return resolve(definitions[name])
            return {key: resolve(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


def _retry_delay(response) -> float | None:
    """Gemini's suggested wait ("retryDelay": "37s"), when it gave one."""
    try:
        details = response.json().get("error", {}).get("details", [])
        delay = next(d["retryDelay"] for d in details if "retryDelay" in d)
        return float(str(delay).removesuffix("s"))
    except (ValueError, AttributeError, StopIteration, KeyError, TypeError):
        return None


def _error(response) -> str:
    try:
        return json.dumps(response.json().get("error", {}).get("message", ""))[:300]
    except ValueError:
        return response.text[:300]
