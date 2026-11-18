"""Fakes for tests in any app. Tests never call a real model provider or the network."""

import json
import threading
from collections.abc import Callable

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import ConfigDict, Field, ValidationError


def _as_text(reply) -> str:
    if hasattr(reply, "model_dump_json"):
        return reply.model_dump_json()
    return reply if isinstance(reply, str) else json.dumps(reply)


class _StructuredOutput:
    """with_structured_output for fakes: validates each reply's JSON content."""

    def with_structured_output(self, schema, *, include_raw=False, **kwargs):
        def parse(message):
            try:
                parsed, error = schema.model_validate_json(message.content), None
            except ValidationError as exc:
                parsed, error = None, exc
            if include_raw:
                return {"raw": message, "parsed": parsed, "parsing_error": error}
            if error is not None:
                raise error
            return parsed

        return self | RunnableLambda(parse)


class FakeChatModel(_StructuredOutput, GenericFakeChatModel):
    """Replies with queued messages and records the prompts it received."""

    calls: list = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def fake_chat_model(*replies) -> FakeChatModel:
    """Each reply is a string, or a dict/Pydantic model sent as JSON."""
    return FakeChatModel(messages=iter(AIMessage(content=_as_text(reply)) for reply in replies))


class ScriptedChatModel(_StructuredOutput, BaseChatModel):
    """Answers each call with `respond(prompt_text)`; thread-safe, records prompts.

    Replies report usage_metadata, so token budgets can be tested.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    respond: Callable[[str], object]
    tokens_per_call: int = 100
    calls: list = Field(default_factory=list)
    lock: object = Field(default_factory=threading.Lock)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = "\n".join(str(message.content) for message in messages)
        with self.lock:
            self.calls.append(text)
        reply = self.respond(text)
        if isinstance(reply, Exception):
            raise reply
        message = AIMessage(
            content=_as_text(reply),
            usage_metadata={
                "input_tokens": self.tokens_per_call // 2,
                "output_tokens": self.tokens_per_call // 2,
                "total_tokens": self.tokens_per_call,
            },
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def use_scripted_models(monkeypatch, settings, responders: dict, *, tokens_per_call=100):
    """Route every model build to ScriptedChatModel by role. Returns {role: model}."""
    from apps.ai import llm

    models = {}

    def factory(resolved):
        respond = responders.get(resolved.role)
        if respond is None:
            raise AssertionError(f"No scripted replies for role {resolved.role!r}")
        model = ScriptedChatModel(respond=respond, tokens_per_call=tokens_per_call)
        models[resolved.role] = model
        return model

    monkeypatch.setitem(llm._PROVIDERS, "scripted", factory)
    settings.AI_LLM_PROVIDER = "scripted"
    return models


class Web:
    """A fake internet for tests: register handlers by host (and optional path prefix).

    Requests to anything unregistered fail the test instead of leaving the machine.
    """

    def __init__(self):
        self.routes: list[tuple[str, str, Callable]] = []
        self.requests: list[httpx.Request] = []

    def route(self, host: str, path: str = "", handler=None):
        def register(function):
            self.routes.append((host, path, function))
            return function

        return register(handler) if handler else register

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for host, path, handler in self.routes:
            if request.url.host == host and request.url.path.startswith(path):
                return handler(request)
        raise AssertionError(f"Unexpected network request in a test: {request.url}")

    def count(self, host: str) -> int:
        return sum(1 for request in self.requests if request.url.host == host)


def make_pdf(pages: list[str]) -> bytes:
    """A real PDF with one page per string (Helvetica, one line per text line), for
    tests that download documents. pypdf reads the text back."""

    def escape_pdf(line: str) -> str:
        return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        None,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    kids = []
    for text in pages:
        lines = " T* ".join(f"({escape_pdf(line)}) Tj" for line in text.split("\n"))
        stream = f"BT /F1 10 Tf 14 TL 40 800 Td {lines} ET".encode("latin-1", "replace")
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n{stream.decode('latin-1')}\nendstream"
        )
        content = len(objects)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {content} 0 R "
            "/Resources << /Font << /F1 3 0 R >> >> >>"
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)
