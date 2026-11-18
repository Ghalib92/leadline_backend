"""The contract between a proposal job and whatever writes the proposal.

apps.proposals builds a ProposalRequest, passes an emitter for progress events, and
saves what comes back. Writers never touch the ORM. PROPOSAL_RUNNER picks one:
"graph" (the LangGraph Proposal Writer) or "fake" (sample text, no external calls).
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from apps.ai.events import RunEventEmitter
from apps.ai.exceptions import AIConfigError

# Progress steps of a proposal job, in order. "checklist" runs for tenders only.
PROPOSAL_STEPS = ("requirements", "checklist", "outline", "evidence", "draft", "review")


@dataclass(frozen=True)
class ProposalRequest:
    proposal_id: str
    organization_id: str
    organization_name: str
    opportunity: dict  # title, type, funder, summary, requirements, deadline, source_url, tender…
    budget: dict
    profile: dict = field(default_factory=dict)
    dossier: dict | None = None
    # Which company documents evidence may come from (KnowledgeScope.to_config()).
    knowledge: dict | None = None
    # Every company document, for a bid's checklist: [{id, title, folder, category,
    # readable}]. Certificates are often scans with no text, so titles matter.
    documents: list = field(default_factory=list)

    @property
    def is_tender(self) -> bool:
        return self.opportunity.get("opportunity_type") == "tender"


@dataclass
class ProposalResult:
    """What a full draft or a section rewrite produced.

    Sections are dicts: key, title, purpose, key_points, evidence_query, target_words,
    content (Markdown), evidence ([{n, document_id, document_title, location, excerpt}]),
    missing_information, and drafted (False when the budget ran out before it).
    """

    sections: list[dict]
    status: Literal["complete", "partial"] = "complete"
    warning: str = ""
    requirements: dict | None = None  # full drafts only
    review: dict | None = None  # full drafts only
    # Tenders: [{key, n, code, requirement, kind, status, documents: [{id, title}],
    # note}] (full drafts and document rechecks). None when not checked.
    checklist: list | None = None
    prompt_versions: dict = field(default_factory=dict)
    models_used: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)


class ProposalWriter(Protocol):
    def draft(self, request: ProposalRequest, emitter: RunEventEmitter) -> ProposalResult: ...

    def rewrite_section(
        self,
        request: ProposalRequest,
        emitter: RunEventEmitter,
        *,
        section: dict,
        requirements: dict,
        instructions: str,
    ) -> ProposalResult:
        """Rewrite one section; the result holds just that section."""

    def check_documents(
        self, request: ProposalRequest, emitter: RunEventEmitter, *, requirements: dict
    ) -> ProposalResult:
        """A tender's checklist again (after documents were added); no sections."""


class SectionRewriteFailed(Exception):
    """A section could not be rewritten. The message is shown to the user."""


_WRITERS: dict[str, ProposalWriter] = {}


def register_writer(name: str, writer: ProposalWriter) -> None:
    _WRITERS[name] = writer


def registered_writers() -> frozenset[str]:
    _load_builtin()
    return frozenset(_WRITERS)


def get_writer(name: str) -> ProposalWriter:
    _load_builtin()
    writer = _WRITERS.get(name)
    if writer is None:
        known = ", ".join(sorted(_WRITERS))
        raise AIConfigError(f"Unknown PROPOSAL_RUNNER {name!r}. Known writers: {known}.")
    return writer


def _load_builtin():
    from apps.ai.graphs import fake_proposal  # noqa: F401 — registers "fake"
    from apps.ai.graphs.proposal import writer  # noqa: F401 — registers "graph"
