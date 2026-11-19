"""Dossier agent: summarises an organization's profile, preferences and documents.

It returns the dossier and never saves it: apps.knowledge stores the result.
"""

import json
from dataclasses import dataclass
from html import escape

from langchain_core.messages import HumanMessage

from apps.ai.config.loader import resolve_role
from apps.ai.events import NullEmitter, RunEvent, RunEventEmitter
from apps.ai.exceptions import RequestTooLarge, StructuredOutputError
from apps.ai.llm import build_chat_model
from apps.ai.prompts import load_prompt
from apps.ai.schemas.dossier import Dossier
from apps.ai.structured import invoke_structured

ROLE = "dossier"


@dataclass(frozen=True)
class DossierResult:
    dossier: Dossier
    prompt_id: str
    model: str
    usage: dict


def generation_signature() -> dict:
    """The prompt and model a generation would use now. Part of the dossier's source hash,
    so a new prompt version or model triggers regeneration."""
    return {"prompt_id": load_prompt(ROLE).id, "model": resolve_role(ROLE).model}


def generate_dossier(inputs: dict, *, emitter: RunEventEmitter | None = None) -> DossierResult:
    """`inputs` is apps.knowledge.selectors.dossier_inputs(): profile, preferences and
    document excerpts. When the request is too large for the model's plan (Groq's free
    tier caps one request), the excerpts are shortened and it is asked again."""
    emitter = emitter or NullEmitter()
    prompt = load_prompt(ROLE)
    resolved = resolve_role(ROLE)
    emitter.emit(
        RunEvent(
            step="profile",
            message=f"Summarising the profile and {len(inputs['documents'])} document(s)",
            tool="dossier",
        )
    )
    documents = inputs["documents"]
    for attempt in range(SHORTER_ATTEMPTS + 1):
        try:
            result = _summarise(prompt, resolved, inputs, documents)
            break
        except StructuredOutputError as exc:
            if attempt == SHORTER_ATTEMPTS or not isinstance(exc.__cause__, RequestTooLarge):
                raise
            documents = _shorter(documents)
            emitter.emit(
                RunEvent(
                    step="profile",
                    message="Too long for the model in one request; using shorter excerpts",
                    tool="dossier",
                    level="warning",
                )
            )
    emitter.emit(RunEvent(step="profile", message="Company dossier ready", tool="dossier"))
    return DossierResult(
        dossier=result.output,
        prompt_id=prompt.id,
        model=resolved.model
        if result.model in ("", resolved.model)
        else f"{resolved.model} + {result.model}",
        usage=result.usage,
    )


SHORTER_ATTEMPTS = 2  # each halves every excerpt


def _summarise(prompt, resolved, inputs, documents):
    text = prompt.render(
        profile=json.dumps(inputs["profile"], indent=2, ensure_ascii=False),
        preferences=json.dumps(inputs["preferences"], indent=2, ensure_ascii=False),
        documents=format_documents(documents),
    )
    return invoke_structured(
        build_chat_model(resolved),
        [HumanMessage(text)],
        Dossier,
        method=resolved.structured_output,
    )


def _shorter(documents: list[dict]) -> list[dict]:
    return [{**item, "excerpt": item["excerpt"][: len(item["excerpt"]) // 2]} for item in documents]


def format_documents(documents: list[dict]) -> str:
    if not documents:
        return "(no processed documents)"
    # Escaping keeps document text from closing its own tag and posing as instructions.
    return "\n\n".join(
        f'<document title="{escape(item["title"])}" category="{escape(item["category"])}">\n'
        f"{escape(item['excerpt'], quote=False)}\n</document>"
        for item in documents
    )
