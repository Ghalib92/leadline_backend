"""Proposal graph nodes, plus the single-section rewrite used outside the graph."""

import contextvars
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from html import escape

from apps.ai.exceptions import AIConfigError, StructuredOutputError
from apps.ai.graphs.budget import BudgetExhausted
from apps.ai.schemas.proposal import (
    DraftedSection,
    ProposalOutline,
    ProposalRequirements,
    ProposalReview,
)
from apps.ai.tools.fetch.base import FetchError
from apps.ai.tools.knowledge import search_company_knowledge
from apps.knowledge.scope import KnowledgeScope

from .state import ProposalState

_KEY = re.compile(r"[^a-z0-9_]+")


def _dump(value) -> str:
    return escape(json.dumps(value, ensure_ascii=False, indent=1), quote=False)


def requirements(runtime, state: ProposalState) -> ProposalState:
    opportunity = runtime.request.opportunity
    runtime.emit(
        "requirements",
        f"Reading the call: {opportunity.get('title', '')[:150]}",
        tool="fetch",
        url=opportunity.get("source_url", ""),
    )
    call = _call_text(runtime)
    try:
        found = runtime.ask(
            "proposal_requirements",
            ProposalRequirements,
            today=date.today().isoformat(),
            opportunity=_dump(opportunity),
            url=escape(opportunity.get("source_url", "")),
            call=escape(call[: runtime.settings.max_call_chars], quote=False),
        )
    except StructuredOutputError:
        runtime.warn(
            "requirements",
            "The call's requirements could not be read; writing a "
            "standard proposal structure instead.",
            partial=False,
        )
        found = ProposalRequirements(summary=opportunity.get("summary", ""))
    except BudgetExhausted as exc:
        runtime.warn("requirements", str(exc))
        return {"requirements": {}, "sections": [], "stop": str(exc)}
    runtime.emit(
        "requirements",
        f"Found {len(found.sections)} required section(s) and "
        f"{len(found.evaluation_criteria)} evaluation criteria",
        tool="proposal_requirements",
    )
    return {"requirements": found.model_dump(), "stop": ""}


def _call_text(runtime) -> str:
    """The call's page when it can be read, else what discovery already extracted."""
    opportunity = runtime.request.opportunity
    known = "\n".join(
        f"{label}: {value}"
        for label, value in (
            ("Summary", opportunity.get("summary")),
            ("Eligibility", opportunity.get("eligibility")),
            ("Requirements", "; ".join(opportunity.get("requirements", {}).get("items", []))),
        )
        if value
    )
    url = opportunity.get("source_url", "")
    if not url:
        return known
    try:
        runtime.budget.use_page()
        page = runtime.fetcher.fetch(url)
    except (FetchError, BudgetExhausted) as exc:
        runtime.warn(
            "requirements",
            f"Could not open the call's page ({exc}); using what discovery saved about it.",
            partial=False,
        )
        return known
    return f"{page.text}\n\n{known}"


def outline(runtime, state: ProposalState) -> ProposalState:
    if state.get("stop"):
        return {}
    settings = runtime.settings
    try:
        plan = runtime.ask(
            "proposal_outline",
            ProposalOutline,
            max_sections=settings.max_sections,
            default_words=settings.default_section_words,
            organization=escape(runtime.organization_brief(), quote=False),
            requirements=_dump(state["requirements"]),
        )
    except (StructuredOutputError, BudgetExhausted) as exc:
        message = str(exc) if isinstance(exc, BudgetExhausted) else "The outline failed."
        runtime.warn("outline", message)
        return {"sections": [], "stop": message}
    sections, seen = [], set()
    for item in plan.sections[: settings.max_sections]:
        key = _KEY.sub("_", item.key.lower()).strip("_")[:40] or f"section_{len(sections) + 1}"
        while key in seen:
            key = f"{key}_{len(sections) + 1}"
        seen.add(key)
        sections.append(
            {
                **item.model_dump(),
                "key": key,
                "target_words": item.target_words or settings.default_section_words,
                "content": "",
                "evidence": [],
                "missing_information": [],
                "drafted": False,
            }
        )
    runtime.emit(
        "outline",
        f"Planned {len(sections)} section(s)",
        tool="proposal_outline",
        data={"sections": [s["title"] for s in sections]},
    )
    return {"sections": sections}


def evidence(runtime, state: ProposalState) -> ProposalState:
    if state.get("stop") or not state.get("sections"):
        return {}
    sections, found_any = [], False
    for section in state["sections"]:
        section = {**section, "evidence": find_evidence(runtime, section)}
        found_any = found_any or bool(section["evidence"])
        sections.append(section)
    if not found_any:
        runtime.warn(
            "evidence",
            "No supporting passages in company documents; the draft uses "
            "the profile only. Uploading reports and past proposals helps.",
            partial=False,
        )
    return {"sections": sections}


def find_evidence(runtime, section) -> list[dict]:
    limit = runtime.settings.evidence_per_section
    if limit == 0:
        return []
    try:
        runtime.budget.use_search()
    except BudgetExhausted:
        return []  # writing without evidence beats not writing
    query = section.get("evidence_query") or section["title"]
    hits = search_company_knowledge(
        runtime.request.organization_id,
        query,
        scope=KnowledgeScope.from_config(runtime.request.knowledge),
        limit=limit,
        emitter=runtime.emitter,
        step="evidence",
    )
    return [
        {
            "n": number,
            "document_id": hit.document_id,
            "document_title": hit.document_title,
            "location": ", ".join(f"{k} {v}" for k, v in hit.metadata.items()),
            "excerpt": hit.text,
        }
        for number, hit in enumerate(hits, start=1)
    ]


def draft(runtime, state: ProposalState) -> ProposalState:
    if state.get("stop") or not state.get("sections"):
        return {}
    sections = {section["key"]: section for section in state["sections"]}
    stop = ""
    workers = min(runtime.settings.max_concurrency, len(sections))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for section in state["sections"]:
            runtime.emit(
                "draft",
                f"Writing “{section['title']}”",
                tool="drafter",
                data={"section": section["key"]},
            )
            work = contextvars.copy_context().run
            futures[
                pool.submit(work, write_section, runtime, section, state["requirements"], "")
            ] = section["key"]
        try:
            for future in as_completed(futures, timeout=max(1, runtime.budget.seconds_left())):
                key = futures[future]
                try:
                    sections[key] = future.result()
                except AIConfigError:
                    raise
                except BudgetExhausted as exc:
                    stop = stop or str(exc)
                except StructuredOutputError:
                    runtime.warn("draft", f"“{sections[key]['title']}” could not be written.")
                    continue
                if sections[key]["drafted"]:
                    words = len(sections[key]["content"].split())
                    runtime.emit(
                        "draft",
                        f"Wrote “{sections[key]['title']}” ({words} words)",
                        data={"section": key, "words": words},
                    )
        except TimeoutError:
            for future in futures:
                future.cancel()
            stop = stop or f"Time limit reached ({runtime.budget.limits['max_wall_seconds']} s)."
    if stop:
        missing = [s["title"] for s in sections.values() if not s["drafted"]]
        runtime.warn("draft", f"{stop} Not written yet: {', '.join(missing)}.")
    return {"sections": [sections[s["key"]] for s in state["sections"]], "stop": stop}


def write_section(runtime, section, requirements, instructions) -> dict:
    """One section, written with its evidence. Raises BudgetExhausted or
    StructuredOutputError; used by the graph and for single-section rewrites."""
    evidence = section.get("evidence") or []
    passages = "\n\n".join(
        f'<passage n="{item["n"]}" source="{escape(item["document_title"])}'
        f'{(" (" + escape(item["location"]) + ")") if item["location"] else ""}">\n'
        f"{escape(item['excerpt'], quote=False)}\n</passage>"
        for item in evidence
    )
    written = runtime.ask(
        "drafter",
        DraftedSection,
        title=escape(section["title"], quote=False),
        purpose=escape(section.get("purpose", ""), quote=False),
        key_points=escape("; ".join(section.get("key_points", [])), quote=False),
        words=section.get("target_words") or runtime.settings.default_section_words,
        instructions=escape(instructions.strip(), quote=False) or "None.",
        requirements=_dump(requirements),
        organization=escape(runtime.organization_brief(), quote=False),
        evidence=passages or "(no passages found)",
    )
    return {
        **section,
        "content": written.content.strip(),
        "missing_information": written.missing_information,
        "drafted": True,
    }


def review(runtime, state: ProposalState) -> ProposalState:
    drafted = [s for s in state.get("sections", []) if s["drafted"]]
    if state.get("stop") or not drafted:
        return {"review": None}
    text = "\n\n".join(f"## {s['title']} [{s['key']}]\n{s['content']}" for s in drafted)
    try:
        found = runtime.ask(
            "proposal_review",
            ProposalReview,
            requirements=_dump(state["requirements"]),
            draft=escape(text, quote=False),
        )
    except (StructuredOutputError, BudgetExhausted):
        runtime.warn("review", "The draft could not be checked automatically.", partial=False)
        return {"review": None}
    runtime.emit(
        "review",
        f"Readiness {found.score}/100; {len(found.issues)} issue(s) to fix",
        tool="proposal_review",
        data={"score": found.score},
    )
    return {"review": found.model_dump()}
