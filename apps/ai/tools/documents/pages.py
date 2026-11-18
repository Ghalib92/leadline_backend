"""Choose which pages of a long tender document a model reads.

Kenyan standard tender documents run to 60-150 pages, most of it the same
instructions in every tender. What differs sits in a few places: the invitation, the
Tender Data Sheet (deadlines, submission, mandatory requirements), the evaluation
criteria and the terms of reference or specifications. Those start with a heading
near the top of a page, so pages are picked by heading first, and the rest by how
many requirement words they hold, until the character budget is used. All in code:
no page text reaches a model before it is picked.

PDF text often loses its spaces ("Othereligibilityandmandatorycriteria"), so
everything is matched with whitespace removed.
"""

import re
from dataclasses import dataclass

from .base import DocumentPage

# Heading → priority (lower is read first). Matched at the start of a short line.
HEADINGS = (
    ("tenderdatasheet", 0),
    ("datasheet", 0),
    ("particularsofappendix", 0),
    ("appendixtoinstructions", 0),
    ("evaluationandqualificationcriteria", 1),
    ("evaluationcriteria", 1),
    ("qualificationcriteria", 1),
    ("thecriteria,sub-criteria", 1),
    ("mandatoryrequirements", 1),
    ("preliminaryevaluation", 1),
    ("technicalevaluation", 1),
    ("termsofreference", 2),
    ("scopeofwork", 2),
    ("scopeofservices", 2),
    ("scheduleofrequirements", 2),
    ("technicalspecifications", 2),
    ("statementofrequirements", 2),
    ("invitationtotender", 3),
    ("tendernotice", 3),
    ("requestforproposal", 3),
)
# Words that mark a page about requirements, with their weight (counted up to 3 times).
WORDS = {
    "mandatory": 3,
    "eligib": 2,
    "evaluationcriteria": 4,
    "passmark": 5,
    "preliminaryexamination": 4,
    "preliminaryevaluation": 4,
    "technicalevaluation": 4,
    "termsofreference": 3,
    "scopeofwork": 4,
    "deliverable": 3,
    "specification": 2,
    "mustsubmit": 3,
    "taxcompliance": 4,
    "certificateofincorporation": 3,
    "certificateofregistration": 3,
    "agpo": 3,
    "tendersecurity": 2,
    "bidsecurity": 2,
    "experience": 1,
    "keyexpert": 2,
    "keypersonnel": 2,
    "points": 1,
    "marks": 1,
    "submissiondeadline": 3,
    "closingdate": 3,
    "clarification": 1,
    "sitevisit": 3,
    "pre-bid": 3,
    "prebid": 3,
}
# Room before a heading for "SECTION 2 (B).", "SECTION V -", "22.2" and the like.
MAX_PREFIX = 14
_SPACE = re.compile(r"\s+")
HEAD_LINES = 6  # a heading sits in a page's first lines
MAX_HEADING_LINE = 110
FOLLOWING_PAGES = 2  # a section runs on after its heading page
MIN_WORD_SCORE = 3


@dataclass(frozen=True)
class PickedPage:
    document: str  # the document's name
    number: int
    text: str


def squash(text: str) -> str:
    return _SPACE.sub("", text.lower())


def heading_priority(text: str) -> int | None:
    """The best heading a page opens with, or None."""
    best = None
    lines = [line for line in text.split("\n") if line.strip()][:HEAD_LINES]
    for line in lines:
        if len(line.strip()) > MAX_HEADING_LINE:
            continue
        flat = squash(line)
        for heading, priority in HEADINGS:
            at = flat.find(heading)
            # The line is the heading (a little more is fine), not running text.
            if 0 <= at <= MAX_PREFIX and len(flat) <= at + len(heading) + 30:
                if best is None or priority < best:
                    best = priority
    return best


def word_score(text: str) -> int:
    flat = squash(text)
    return sum(weight * min(flat.count(word), 3) for word, weight in WORDS.items())


def is_contents_page(text: str) -> bool:
    """A table of contents names every heading; its dot leaders give it away."""
    return text.count("....") >= 5


def pick_pages(documents: list[tuple[str, list[DocumentPage]]], *, max_chars: int):
    """The pages to read from each (name, pages), within `max_chars`, in document
    order. Short documents are read whole; long ones by heading, then by words."""
    total = sum(len(page.text) for _, pages in documents for page in pages)
    if total <= max_chars:
        return [
            PickedPage(name, page.number, page.text)
            for name, pages in documents
            for page in pages
            if page.text.strip()
        ]
    entries = []  # (doc index, page index, page, priority, score)
    for d, (_, pages) in enumerate(documents):
        for p, page in enumerate(pages):
            if is_contents_page(page.text):
                entries.append((d, p, page, None, 0))
            else:
                entries.append((d, p, page, heading_priority(page.text), word_score(page.text)))
    position = {(d, p): i for i, (d, p, *_rest) in enumerate(entries)}
    chosen: set[tuple[int, int]] = set()
    budget = max_chars

    def take(d, p):
        nonlocal budget
        page = documents[d][1][p]
        if (d, p) in chosen or not page.text.strip() or len(page.text) > budget:
            return
        if is_contents_page(page.text):
            return
        chosen.add((d, p))
        budget -= len(page.text)

    anchors = sorted(
        (entry for entry in entries if entry[3] is not None), key=lambda e: (e[3], e[0], e[1])
    )
    for d, p, *_rest in anchors:
        for following in range(p, min(p + 1 + FOLLOWING_PAGES, len(documents[d][1]))):
            take(d, following)
    for d, p, _page, _priority, score in sorted(entries, key=lambda e: -e[4]):
        if score >= MIN_WORD_SCORE:
            take(d, p)
    return [
        PickedPage(documents[d][0], documents[d][1][p].number, documents[d][1][p].text)
        for d, p in sorted(chosen, key=lambda key: position[key])
    ]
