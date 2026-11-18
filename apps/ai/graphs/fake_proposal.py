"""A stand-in Proposal Writer: realistic progress and sample text, no external calls.

Lets the proposal editor be built and demoed without API keys. All text is plainly
sample text, marked "[Sample]".
"""

import time

from django.conf import settings

from apps.ai.events import RunEvent
from apps.ai.graphs.proposal_runner import ProposalResult, register_writer

SECTIONS = [
    ("summary", "Executive summary", 200),
    ("problem", "Problem and need", 350),
    ("approach", "Approach and activities", 450),
    ("results", "Expected results and monitoring", 300),
    ("team", "Team and track record", 250),
    ("budget", "Budget overview", 200),
]


def _pause():
    if settings.PROPOSAL_FAKE_STEP_SECONDS > 0:
        time.sleep(settings.PROPOSAL_FAKE_STEP_SECONDS)


def _section(request, key, title, words, instructions=""):
    opportunity = request.opportunity
    call = opportunity.get("title", "this call")
    funder = opportunity.get("funder") or "the funder"
    note = f" (as asked: {instructions.strip()[:120]})" if instructions.strip() else ""
    content = (
        f"[Sample] {request.organization_name} proposes work that answers "
        f"“{call}” from {funder}"
        f"{note}.\n\n"
        "- Sample point grounded in the organization profile [1]\n"
        "- [TODO: add a figure from your latest annual report]\n\n"
        "This is sample text from the test writer, not a real draft."
    )
    return {
        "key": key,
        "title": title,
        "purpose": f"Sample purpose of the {title.lower()} section.",
        "key_points": ["Sample point"],
        "evidence_query": title.lower(),
        "target_words": words,
        "content": content,
        "evidence": [
            {
                "n": 1,
                "document_id": "",
                "document_title": "Organization profile (sample)",
                "location": "",
                "excerpt": "Sample evidence passage.",
            }
        ],
        "missing_information": ["A figure from your latest annual report"],
        "drafted": True,
    }


class FakeProposalWriter:
    def draft(self, request, emitter):
        def emit(step, message, **extra):
            emitter.emit(RunEvent(step=step, message=message, **extra))

        emit("requirements", f"Reading the call: {request.opportunity.get('title', '')[:120]}")
        _pause()
        emit("requirements", "Found 6 sections to write and 4 evaluation criteria")
        checklist = None
        if request.is_tender:
            emit("checklist", "Checking 3 requirements against your documents")
            _pause()
            checklist = _checklist(request)
        emit("outline", "Planned 6 sections", tool="proposal_outline")
        _pause()
        sections = []
        for key, title, words in SECTIONS:
            emit(
                "evidence",
                f"Searching company documents for “{title.lower()}”",
                tool="search_company_knowledge",
            )
            emit("draft", f"Writing “{title}”", tool="drafter", data={"section": key})
            _pause()
            sections.append(_section(request, key, title, words))
        emit("review", "Checking the draft against the call", tool="proposal_review")
        _pause()
        emit("review", "Readiness 62/100; 3 issues to fix", data={"score": 62})
        return ProposalResult(
            sections=sections,
            requirements={
                "summary": "[Sample] requirements from the test writer.",
                "sections": [
                    {"title": title, "guidance": "", "word_limit": words}
                    for _, title, words in SECTIONS
                ],
                "evaluation_criteria": [
                    "Relevance (30%)",
                    "Feasibility (30%)",
                    "Impact (25%)",
                    "Value for money (15%)",
                ],
                "eligibility": [],
                "documents_required": ["Budget", "Registration certificate"],
                "budget_rules": [],
                "format_rules": [],
                "deadline": "",
            },
            review={
                "score": 62,
                "strengths": ["Sample strength"],
                "issues": [
                    {
                        "section": "budget",
                        "issue": "Sample issue",
                        "suggestion": "Add the cost breakdown.",
                    }
                ],
                "missing_requirements": ["Registration certificate"],
            },
            checklist=checklist,
        )

    def rewrite_section(self, request, emitter, *, section, requirements, instructions):
        emitter.emit(
            RunEvent(
                step="draft",
                message=f"Rewriting “{section['title']}”",
                tool="drafter",
                data={"section": section["key"]},
            )
        )
        _pause()
        rewritten = _section(
            request,
            section["key"],
            section["title"],
            section.get("target_words", 300),
            instructions,
        )
        return ProposalResult(sections=[rewritten])

    def check_documents(self, request, emitter, *, requirements):
        emitter.emit(RunEvent(step="checklist", message="Checking 3 requirements"))
        _pause()
        return ProposalResult(sections=[], checklist=_checklist(request))


def _checklist(request):
    """Sample verdicts; the first company document, if any, covers registration."""
    first = request.documents[:1]
    registered = "ready" if first else "missing"
    items = [
        ("MR1", "[Sample] Certificate of incorporation", "certificate", registered),
        ("MR2", "[Sample] Valid tax compliance certificate", "certificate", "missing"),
        ("MR3", "[Sample] Signed form of tender", "form", "prepare"),
    ]
    return [
        {
            "key": f"r{n}",
            "n": n,
            "code": code,
            "requirement": text,
            "kind": kind,
            "status": status,
            "documents": [{"id": d["id"], "title": d["title"]} for d in first]
            if status == "ready"
            else [],
            "note": "",
        }
        for n, (code, text, kind, status) in enumerate(items, start=1)
    ]


register_writer("fake", FakeProposalWriter())
