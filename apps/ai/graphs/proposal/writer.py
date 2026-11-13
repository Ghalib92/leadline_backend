"""The "graph" Proposal Writer."""

from apps.ai.config.loader import load_proposals_file, resolve_role
from apps.ai.exceptions import StructuredOutputError
from apps.ai.graphs.budget import Budget, BudgetExhausted
from apps.ai.graphs.proposal_runner import ProposalResult, SectionRewriteFailed, register_writer
from apps.ai.llm import build_chat_model
from apps.ai.tools.fetch.http import HttpFetcher
from apps.ai.tracing import run_config

from . import nodes
from .builder import build_graph
from .runtime import ProposalRuntime

ROLES = ("proposal_requirements", "proposal_outline", "drafter", "proposal_review")


def _runtime(request, emitter, roles=ROLES) -> ProposalRuntime:
    settings = load_proposals_file()
    runtime = ProposalRuntime(
        request=request,
        emitter=emitter,
        settings=settings,
        budget=Budget(request.budget or settings.budget.model_dump()),
        fetcher=HttpFetcher(),
    )
    # Build the models first: a missing key fails the job before any other work.
    for role in roles:
        resolved = resolve_role(role)
        runtime._models[role] = (build_chat_model(resolved), resolved.structured_output)
        runtime.models_used[role] = resolved.model
    return runtime


def _result(runtime, sections, **extra) -> ProposalResult:
    return ProposalResult(
        sections=sections,
        status="partial" if runtime.warnings else "complete",
        warning=" ".join(dict.fromkeys(runtime.warnings))[:500],
        prompt_versions=dict(runtime.prompt_versions),
        models_used=dict(runtime.models_used),
        usage=runtime.budget.usage(),
        **extra,
    )


class GraphProposalWriter:
    def draft(self, request, emitter):
        runtime = _runtime(request, emitter)
        state = build_graph(runtime).invoke(
            {},
            config=run_config(
                "proposal",
                metadata={
                    "proposal_id": request.proposal_id,
                    "organization_id": request.organization_id,
                },
                tags=["proposal"],
            ),
        )
        return _result(
            runtime,
            state.get("sections", []),
            requirements=state.get("requirements") or {},
            review=state.get("review"),
        )

    def rewrite_section(self, request, emitter, *, section, requirements, instructions):
        runtime = _runtime(request, emitter, roles=("drafter",))
        runtime.emit(
            "draft",
            f"Rewriting “{section['title']}”",
            tool="drafter",
            data={"section": section["key"]},
        )
        section = {**section, "evidence": nodes.find_evidence(runtime, section)}
        try:
            written = nodes.write_section(runtime, section, requirements, instructions)
        except (StructuredOutputError, BudgetExhausted) as exc:
            reason = (
                str(exc) if isinstance(exc, BudgetExhausted) else "the model's answer was unusable"
            )
            message = f"“{section['title']}” could not be rewritten: {reason}."
            raise SectionRewriteFailed(message) from exc
        words = len(written["content"].split())
        runtime.emit(
            "draft",
            f"Rewrote “{section['title']}” ({words} words)",
            data={"section": section["key"], "words": words},
        )
        return _result(runtime, [written])


register_writer("graph", GraphProposalWriter())
