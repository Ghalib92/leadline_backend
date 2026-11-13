"""What every agent graph's runtime shares: events, warnings and budgeted model calls."""

import json
import threading
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage

from apps.ai.config.loader import resolve_role
from apps.ai.events import NullEmitter, RunEvent
from apps.ai.exceptions import StructuredOutputError
from apps.ai.graphs.budget import Budget
from apps.ai.llm import build_chat_model
from apps.ai.prompts import load_prompt
from apps.ai.structured import invoke_structured


class AgentRuntimeMixin:
    """For dataclass runtimes with these fields: emitter, budget, warnings,
    prompt_versions, models_used, _models (dict) and _lock (threading.Lock).

    A runtime may also have `pinned_prompts` ({role: version}), used by evals to run an
    older or newer prompt version than the latest.
    """

    def prompt_for(self, role: str):
        version = (getattr(self, "pinned_prompts", None) or {}).get(role)
        return load_prompt(role, version)

    def emit(self, step: str, message: str, **extra) -> None:
        self.emitter.emit(RunEvent(step=step, message=message[:500], **extra))

    def warn(self, step: str, message: str, *, partial: bool = True, **extra) -> None:
        """A warning event; `partial` ones also mark the result as incomplete."""
        self.emit(step, message, level="warning", **extra)
        if partial:
            with self._lock:
                self.warnings.append(message)

    def ask(self, role: str, schema, **prompt_values):
        """One structured call for an agent role, within the token budget. The role's
        prompt is apps/ai/prompts/<role>/ (latest version)."""
        self.budget.check_tokens()
        prompt = self.prompt_for(role)
        with self._lock:
            if role not in self._models:
                resolved = resolve_role(role)
                self._models[role] = (build_chat_model(resolved), resolved.structured_output)
                self.models_used[role] = resolved.model
            model, method = self._models[role]
            self.prompt_versions[role] = prompt.id
        text = prompt.render(**prompt_values)
        config = {"run_name": role, "tags": [role, f"prompt:{prompt.id}"]}
        try:
            result = invoke_structured(
                model, [HumanMessage(text)], schema, config=config, method=method
            )
        except StructuredOutputError as exc:
            self.budget.add_tokens(exc.usage)
            raise
        self.budget.add_tokens(result.usage)
        return result.output


def organization_brief(name: str, profile: dict, dossier: dict | None) -> str:
    """The organization as agents see it: profile facts plus the dossier, as JSON."""
    brief = {"name": name, **profile}
    if dossier:
        brief["dossier"] = dossier
    return json.dumps(
        {key: value for key, value in brief.items() if value not in ("", None, [], {})},
        ensure_ascii=False,
        indent=1,
    )


@dataclass
class TaskRuntime(AgentRuntimeMixin):
    """For agent calls outside a graph (preference advice, evals): a budget, optional
    pinned prompt versions, and the organization the agents reason about."""

    budget: Budget
    organization_name: str = ""
    profile: dict = field(default_factory=dict)
    dossier: dict | None = None
    pinned_prompts: dict = field(default_factory=dict)
    emitter: object = field(default_factory=NullEmitter)
    settings: object = None  # agents that read settings (verifier: max_page_chars)
    warnings: list[str] = field(default_factory=list)
    prompt_versions: dict = field(default_factory=dict)
    models_used: dict = field(default_factory=dict)
    _models: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def organization_brief(self) -> str:
        return organization_brief(self.organization_name, self.profile, self.dossier)
