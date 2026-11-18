"""Proposal graph nodes, plus the single-section rewrite used outside the graph."""

import contextvars
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from html import escape
from urllib.parse import urlsplit

from apps.ai.exceptions import AIConfigError, StructuredOutputError
from apps.ai.graphs.budget import BudgetExhausted
from apps.ai.schemas.proposal import (
    BidChecklist,
    DraftedSection,
    OutlineSection,
    ProposalOutline,
    ProposalRequirements,
    ProposalReview,
    TenderRequirements,
)
from apps.ai.tools.documents import DocumentError, is_document_url, pick_pages
from apps.ai.tools.fetch.base import FetchError
from apps.ai.tools.knowledge import search_company_knowledge
from apps.knowledge.scope import KnowledgeScope

from .state import ProposalState

_KEY = re.compile(r"[^a-z0-9_]+")


def _dump(value) -> str:
    return escape(json.dumps(value, ensure_ascii=False, indent=1), quote=False)


def requirements(runtime, state: ProposalState) -> ProposalState:
    if runtime.request.is_tender:
        return _tender_requirements(runtime)
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


def _known_text(runtime) -> str:
    """What discovery already extracted about the opportunity."""
    opportunity = runtime.request.opportunity
    return "\n".join(
        f"{label}: {value}"
        for label, value in (
            ("Summary", opportunity.get("summary")),
            ("Eligibility", opportunity.get("eligibility")),
            ("Requirements", "; ".join(opportunity.get("requirements", {}).get("items", []))),
        )
        if value
    )


def _call_text(runtime) -> str:
    """The call's page when it can be read, else what discovery already extracted."""
    opportunity = runtime.request.opportunity
    known = _known_text(runtime)
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


# ---------------------------------------------------------------------------
# Tenders: the tender's own documents, and the compliance checklist
# ---------------------------------------------------------------------------

# Which of a tender's documents hold its requirements, best first.
_DOCUMENT_ORDER = (
    ("tender document", "bidding document", "rfp", "request for proposal", "tender doc"),
    ("tor", "terms of reference", "specification", "scope", "requirements"),
    (),  # anything else
    ("notice", "advert", "invitation"),
)
# Requirements not written into the proposal: the checklist and the plan cover them.
_COMPLIANCE_FIELDS = ("mandatory_requirements", "key_dates", "submission", "documents_required")


def _document_rank(name: str, url: str) -> int:
    """By whole words of the name and file name ("storage/" is not a ToR)."""
    filename = urlsplit(url).path.rsplit("/", 1)[-1]
    words = f" {' '.join(re.findall(r'[a-z0-9]+', f'{name} {filename}'.lower()))} "
    for rank, keys in enumerate(_DOCUMENT_ORDER):
        if any(f" {key} " in words for key in keys):
            return rank
    return 2


def _tender_facts(opportunity) -> dict:
    tender = {k: v for k, v in (opportunity.get("tender") or {}).items() if k != "documents"}
    facts = {
        key: opportunity.get(key)
        for key in ("title", "funder", "summary", "eligibility", "deadline", "location")
    }
    return {k: v for k, v in {**facts, "tender": tender}.items() if v not in ("", None, {}, [])}


def _tender_requirements(runtime) -> ProposalState:
    opportunity = runtime.request.opportunity
    settings = runtime.settings.tender
    runtime.emit(
        "requirements",
        f"Reading the tender: {opportunity.get('title', '')[:150]}",
        url=opportunity.get("source_url", ""),
    )
    documents, page_text = _tender_documents(runtime)
    if documents:
        picked = pick_pages(documents, max_chars=settings.max_document_chars)
        text = "\n\n".join(
            f'<page document="{escape(page.document)}" n="{page.number}">\n'
            f"{escape(page.text, quote=False)}\n</page>"
            for page in picked
        )
        runtime.emit(
            "requirements",
            f"Picked {len(picked)} page(s) that hold requirements from "
            f"{len(documents)} document(s)",
            data={"pages": len(picked), "documents": [name for name, _ in documents]},
        )
    else:
        text = escape(page_text[: runtime.settings.max_call_chars], quote=False)
    try:
        found = runtime.ask(
            "tender_requirements",
            TenderRequirements,
            today=date.today().isoformat(),
            tender=_dump(_tender_facts(opportunity)),
            documents=text or "(no documents could be read)",
        )
    except StructuredOutputError:
        runtime.warn(
            "requirements",
            "The tender's requirements could not be read; writing a standard technical "
            "proposal instead. Check the tender document yourself.",
            partial=False,
        )
        found = TenderRequirements(summary=opportunity.get("summary", ""))
    except BudgetExhausted as exc:
        runtime.warn("requirements", str(exc))
        return {"requirements": {}, "sections": [], "stop": str(exc)}
    runtime.emit(
        "requirements",
        f"Found {len(found.mandatory_requirements)} mandatory requirement(s), "
        f"{len(found.sections)} part(s) of the technical proposal and "
        f"{len(found.key_dates)} key date(s)",
        tool="tender_requirements",
    )
    return {"requirements": found.model_dump(), "stop": ""}


def _tender_documents(runtime) -> tuple[list, str]:
    """([(name, pages)], page text). The tender's listed documents; else its web page,
    and the documents that page links to."""
    opportunity = runtime.request.opportunity
    url = opportunity.get("source_url", "")
    listed = [
        (doc.get("name") or "Document", doc["url"])
        for doc in (opportunity.get("tender") or {}).get("documents", [])
        if doc.get("url")
    ]
    page_text = ""
    if url and is_document_url(url):
        listed.insert(0, (opportunity.get("title") or "Tender document", url))
    elif not listed:
        page_text, links = _tender_page(runtime)
        listed = links
    seen, candidates = set(), []
    for name, link in sorted(listed, key=lambda item: _document_rank(*item)):
        if link not in seen:
            seen.add(link)
            candidates.append((name, link))
    documents = []
    for name, link in candidates[: runtime.settings.tender.max_documents]:
        try:
            runtime.budget.use_page()
        except BudgetExhausted:
            runtime.warn(
                "requirements", "Page budget reached; other documents not read.", partial=False
            )
            break
        runtime.emit("requirements", f"Reading “{name[:120]}”", tool="read_document", url=link)
        try:
            document = runtime.documents.read(link, name=name)
        except DocumentError as exc:
            runtime.warn("requirements", f"Could not read “{name[:120]}”: {exc}", partial=False)
            continue
        runtime.emit(
            "requirements",
            f"Read “{name[:120]}”: {len(document.pages)} page(s)",
            data={"pages": len(document.pages), "chars": document.chars},
        )
        documents.append((document.name, list(document.pages)))
    if not documents and not page_text:
        # Listed documents that couldn't be read: a portal's own page adds nothing
        # (tenders.go.ke builds it in the browser), so use what discovery saved.
        page_text = _known_text(runtime) if listed else _call_text(runtime)
    return documents, page_text


def _tender_page(runtime) -> tuple[str, list]:
    """The tender's web page text and the document links on it."""
    url = runtime.request.opportunity.get("source_url", "")
    if not url:
        return "", []
    try:
        runtime.budget.use_page()
        page = runtime.fetcher.fetch(url)
    except (FetchError, BudgetExhausted) as exc:
        runtime.warn(
            "requirements",
            f"Could not open the tender's page ({exc}); using what discovery saved about it.",
            partial=False,
        )
        return _known_text(runtime), []
    links = [
        (link.text or "Document", link.url) for link in page.links if is_document_url(link.url)
    ]
    return page.text, links


def writing_requirements(requirements: dict) -> dict:
    """What the writing steps see: a tender's compliance lists are left out (the
    checklist covers them), so the draft isn't judged on certificates. Other calls'
    requirements are passed whole."""
    requirements = requirements or {}
    if "mandatory_requirements" not in requirements:
        return requirements
    return {k: v for k, v in requirements.items() if k not in _COMPLIANCE_FIELDS}


def checklist(runtime, state: ProposalState) -> ProposalState:
    if state.get("stop") or not runtime.request.is_tender:
        return {}
    return {"checklist": check_documents(runtime, state.get("requirements") or {})}


def checklist_items(requirements: dict, limit: int) -> list[dict]:
    """Mandatory requirements, then documents asked for that aren't among them."""
    items, seen = [], []
    entries = [
        (r.get("code", ""), r.get("requirement", ""), r.get("kind", "other"))
        for r in requirements.get("mandatory_requirements") or []
    ] + [("", text, "other") for text in requirements.get("documents_required") or []]
    for code, text, kind in entries:
        flat = re.sub(r"[^a-z0-9]+", "", (text or "").lower())
        if not flat or any(flat in other or other in flat for other in seen):
            continue
        seen.append(flat)
        n = len(items) + 1
        items.append({"n": n, "code": code, "requirement": text.strip(), "kind": kind})
        if len(items) >= limit:
            break
    return items


def check_documents(runtime, requirements: dict) -> list[dict]:
    """Each requirement marked ready, check, missing or prepare, against the
    organization's documents. Document IDs come only from the organization's list."""
    settings = runtime.settings.tender
    items = checklist_items(requirements, settings.checklist_items)
    if not items:
        runtime.emit("checklist", "The tender lists no mandatory requirements to check.")
        return []
    documents = runtime.request.documents
    runtime.emit(
        "checklist",
        f"Checking {len(items)} requirement(s) against your {len(documents)} document(s)",
        tool="bid_checklist",
    )
    passages = _checklist_passages(runtime, items, settings.passages_per_item)
    verdicts = {}
    try:
        answer = runtime.ask(
            "bid_checklist",
            BidChecklist,
            organization=escape(runtime.organization_brief(), quote=False),
            documents=_document_list(documents),
            requirements=_requirement_list(items, passages),
        )
        verdicts = {verdict.n: verdict for verdict in answer.items}
    except StructuredOutputError:
        runtime.warn(
            "checklist",
            "Your documents could not be checked automatically; each requirement is "
            "marked for you to check.",
            partial=False,
        )
    except BudgetExhausted as exc:
        runtime.warn("checklist", str(exc))
    titles = {doc["id"]: doc["title"] for doc in documents}
    entries = []
    for item in items:
        verdict = verdicts.get(item["n"])
        status = verdict.status if verdict else _default_status(item["kind"])
        found = [
            {"id": doc_id, "title": titles[doc_id]}
            for doc_id in dict.fromkeys(verdict.document_ids if verdict else [])
            if doc_id in titles
        ][:3]
        if status == "ready" and not found:
            status = "check"  # never "ready" without a document to show for it
        entries.append(
            {
                **item,
                "key": f"r{item['n']}",
                "status": status,
                "documents": found,
                "note": (verdict.note.strip() if verdict else "")[:300],
            }
        )
    counts = {
        status: sum(1 for entry in entries if entry["status"] == status)
        for status in ("ready", "check", "missing", "prepare")
    }
    runtime.emit(
        "checklist",
        f"{counts['ready']} ready, {counts['check']} to check, {counts['missing']} missing, "
        f"{counts['prepare']} to prepare for this bid",
        data=counts,
    )
    return entries


def _default_status(kind: str) -> str:
    return "prepare" if kind in ("form", "security") else "check"


def _checklist_passages(runtime, items, limit) -> dict[int, list]:
    """Company passages per requirement (not for forms, made for the bid). The
    checklist reads every document: a certificate kept in another folder still counts."""
    found = {}
    if limit == 0:
        return found
    for item in items:
        if item["kind"] in ("form", "security"):
            continue
        try:
            runtime.budget.use_search()
        except BudgetExhausted:
            break
        found[item["n"]] = search_company_knowledge(
            runtime.request.organization_id,
            item["requirement"][:300],
            scope=KnowledgeScope.all(),
            limit=limit,
        )
    if found:
        runtime.emit(
            "checklist",
            f"Searched your documents for {len(found)} requirement(s)",
            tool="search_company_knowledge",
        )
    return found


def _document_list(documents) -> str:
    if not documents:
        return "(The organization has uploaded no documents.)"
    lines = []
    for doc in documents:
        unreadable = "" if doc.get("readable", True) else ' text="not readable"'
        lines.append(
            f'<document id="{escape(doc["id"])}" folder="{escape(doc.get("folder", ""))}" '
            f'category="{escape(doc.get("category", ""))}"{unreadable}>'
            f"{escape(doc['title'], quote=False)}</document>"
        )
    return "\n".join(lines)


def _requirement_list(items, passages) -> str:
    blocks = []
    for item in items:
        hits = "".join(
            f'\n<passage document_id="{escape(hit.document_id)}" '
            f'source="{escape(hit.document_title)}">{escape(hit.text[:600], quote=False)}'
            "</passage>"
            for hit in passages.get(item["n"], [])
        )
        blocks.append(
            f'<requirement n="{item["n"]}" code="{escape(item["code"])}" kind="{item["kind"]}">'
            f"\n{escape(item['requirement'], quote=False)}{hits}\n</requirement>"
        )
    return "\n".join(blocks)


def outline(runtime, state: ProposalState) -> ProposalState:
    if state.get("stop"):
        return {}
    settings = runtime.settings
    try:
        plan = runtime.ask(
            "proposal_outline",
            ProposalOutline,
            opportunity_type=runtime.request.opportunity.get("opportunity_type") or "not stated",
            max_sections=settings.max_sections,
            default_words=settings.default_section_words,
            organization=escape(runtime.organization_brief(), quote=False),
            requirements=_dump(writing_requirements(state["requirements"])),
        )
    except BudgetExhausted as exc:
        runtime.warn("outline", str(exc))
        return {"sections": [], "stop": str(exc)}
    except StructuredOutputError:
        plan = _outline_from_requirements(state["requirements"])
        if plan is None:
            runtime.warn("outline", "The outline failed.")
            return {"sections": [], "stop": "The outline failed."}
        runtime.warn(
            "outline",
            "The AI couldn't plan the sections, so they follow the call's own list.",
            partial=False,
        )
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


def _outline_from_requirements(requirements: dict) -> ProposalOutline | None:
    """The call's own sections as the plan, when the outline step fails."""
    required = (requirements or {}).get("sections") or []
    if not required:
        return None
    return ProposalOutline(
        sections=[
            OutlineSection(
                key=_KEY.sub("_", item["title"].lower()).strip("_")[:40] or "section",
                title=item["title"],
                purpose=item.get("guidance", ""),
                evidence_query=item["title"],
                target_words=item.get("word_limit") or 0,
            )
            for item in required
        ]
    )


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
                pool.submit(
                    work,
                    write_section,
                    runtime,
                    section,
                    writing_requirements(state["requirements"]),
                    "",
                )
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
            opportunity_type=runtime.request.opportunity.get("opportunity_type") or "not stated",
            organization=escape(runtime.organization_brief(), quote=False),
            requirements=_dump(writing_requirements(state["requirements"])),
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
