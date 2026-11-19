"""Application writer: a cover letter and a tailored CV for an approved job, from the job
seeker's own CV and the posting, with honest notes on the gaps. It never applies; the
person edits both and applies themselves.

Models stretch a CV toward a posting: a list of tools under a job becomes "designed
and maintained PostgreSQL schemas, optimised queries"; 4 years becomes 6. Three checks
catch that. In code: years of experience above the CV's, and CV lines most of whose
words the CV never uses. For the letter, whose prose word checks can't judge, a second
model (the checker) quotes the sentences the CV doesn't support. Any of them sends the
draft back once with what to fix; what is still unsupported after that is returned for
the person to check."""

import json
import logging
import re
from dataclasses import dataclass
from html import escape

from apps.ai.exceptions import StructuredOutputError
from apps.ai.schemas.jobs import JobApplicationDraft, LetterCheck

logger = logging.getLogger(__name__)
ROLE = "application_writer"
CHECKER = "application_checker"
# "6 years of experience", "over 5 years' backend experience", "7+ years as a developer".
_YEARS = re.compile(
    r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)['’]?\s+(?:of\s+)?(?:[\w-]+\s+){0,3}?"
    r"(?:experience|as\s+an?\b)",
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-z][a-z0-9+#]*")
# Words a faithful CV line uses whatever the CV says.
_COMMON = frozenset(
    "with using from that this have their your years year experience work worked "
    "working built build building developed develop across including based role roles "
    "team also over into within present current currently".split()
)
# A line is unsupported when this many of its words, and this share, are not the CV's.
MIN_NEW_WORDS = 3
MIN_NEW_SHARE = 0.5
MAX_UNVERIFIED = 8
# Names a model can get wrong ("M-Pay" for M-Pesa) or invent ("GraphQL", "AWS"): a word
# with a capital and a hyphen (M-Pesa), an acronym (AWS, EC2), or a capital inside it
# (PesaLink, GraphQL). Each must appear in what the person wrote.
_NAME = re.compile(
    r"(?<![\w-])(?:[A-Z][A-Za-z0-9]*[-‑–][A-Za-z0-9]+|[A-Z]{2,}[0-9]*s?"
    r"|[a-z]*[A-Z][a-z]+[A-Z]\w*|[A-Z][a-z]*[0-9]+)(?!\w)"
)
_GENERIC_NAMES = frozenset({"cv", "it", "hr", "ok"})
_HYPHENS = re.compile(r"[-‑–—]")
# A CV lists what the person has; lines like "Not yet: Kubernetes" are dropped.
_LACKING = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:\*\*)?(?:not yet|no experience (?:with|in)|currently learning"
    r"|still learning)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class WrittenApplication:
    draft: JobApplicationDraft
    unverified: list[str]  # CV lines and letter sentences the CV doesn't support
    rewritten: bool = False


def write_application(
    runtime, *, candidate: dict, job: dict, instructions: str, letter_reads: int = 2
):
    """The application, rewritten once when it claims more years than the CV shows or
    has CV lines or letter sentences the CV doesn't support; what is still unsupported
    is returned for the person to check. The checker reads the letter `letter_reads`
    times (it misses a stretch about one read in three; two reads rarely both do)."""
    evidence = evidence_text(candidate)
    # The letter may also name the employer and what the posting asks for.
    letter_evidence = f"{evidence} {_data(job)}"

    def letter_findings(draft):
        checked = check_letter(runtime, candidate, draft.cover_letter, reads=letter_reads)
        named = unknown_name_sentences(draft.cover_letter, letter_evidence)
        return [*checked, *(sentence for sentence in named if sentence not in checked)]

    draft = _without_lacking(_ask(runtime, candidate, job, instructions))
    letter = letter_findings(draft)
    corrections = _corrections(draft, evidence, candidate.get("years_experience"), letter)
    if corrections:
        logger.info("Application draft sent back: %s", " | ".join(corrections))
        prompt = "\n".join([instructions.strip(), *corrections])
        draft = _without_lacking(_ask(runtime, candidate, job, prompt))
        letter = letter_findings(draft)
    unverified = [*unsupported_lines(draft.cv, evidence), *letter][:MAX_UNVERIFIED]
    return WrittenApplication(draft=draft, unverified=unverified, rewritten=bool(corrections))


def _without_lacking(draft: JobApplicationDraft) -> JobApplicationDraft:
    """The tailored CV without lines listing what the person doesn't have yet, and
    without a heading left with nothing under it."""
    lines = [line for line in draft.cv.splitlines() if not _LACKING.match(line)]
    kept = []
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            rest = lines[index + 1 :]
            following = next((later for later in rest if later.strip()), "")
            if (
                not following
                or following.lstrip().startswith("#")
                and _level(following) <= _level(line)
            ):
                continue
        kept.append(line)
    return draft.model_copy(update={"cv": "\n".join(kept).strip()})


def _level(heading: str) -> int:
    return len(heading.lstrip()) - len(heading.lstrip().lstrip("#"))


def evidence_text(candidate: dict) -> str:
    """What the person says about themselves, for the CV line check: their CV, summary
    and job preferences ("" without a CV: nothing can be checked)."""
    if not candidate.get("cv"):
        return ""
    preferences = _data(candidate.get("job_preferences") or {})
    return " ".join([candidate["cv"], candidate.get("about", ""), preferences])


def check_letter(runtime, candidate: dict, letter: str, *, reads: int = 1) -> list[str]:
    """The letter's sentences the checker finds unsupported by the CV in any of its
    reads, kept only when they really are in the letter (the checker can misquote
    too). A failed read finds nothing: the letter is still shown, its CV lines checked."""
    if not letter.strip() or not candidate.get("cv"):
        return []
    # The evidence only: job preferences are wishes, and read as support.
    evidence = {key: candidate[key] for key in ("name", "about", "cv") if candidate.get(key)}
    flat, found = _flat(letter), []
    for _ in range(reads):
        try:
            check = runtime.ask(
                CHECKER, LetterCheck, candidate=_data(evidence), letter=escape(letter, quote=False)
            )
        except StructuredOutputError:
            logger.warning("The letter could not be checked", exc_info=True)
            continue
        for quote in check.unsupported:
            quote = quote.strip()
            if quote and _flat(quote) in flat and quote not in found:
                found.append(quote)
    return found


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


def _corrections(draft, cv, years, letter_sentences=()) -> list[str]:
    corrections = []
    claimed = claimed_years(draft)
    if years is not None and claimed > years:
        corrections.append(
            f"Your draft claims {claimed} years of experience; the CV shows {years}. "
            f"Say {years} years, or describe the experience without a number."
        )
    unsupported = unsupported_lines(draft.cv, cv)
    if unsupported:
        listed = "\n".join(f"- {line}" for line in unsupported)
        corrections.append(
            "These lines of your tailored CV say things the CV doesn't:\n"
            f"{listed}\n"
            "Rewrite them from what the CV actually says, and make the cover letter "
            "match. A tool listed under a job is something they used, not something "
            "they designed, optimised, led or reviewed."
        )
    if letter_sentences:
        listed = "\n".join(f"- {sentence}" for sentence in letter_sentences)
        corrections.append(
            "A reader checked the cover letter against the CV; these sentences claim "
            f"what the CV doesn't support:\n{listed}\n"
            "Rewrite or drop them. Say only what the CV shows; for what it doesn't, "
            "say you're ready to learn it, or leave it out."
        )
    return corrections


def claimed_years(draft: JobApplicationDraft) -> int:
    """The most years of experience the letter or CV claims (0 when none)."""
    text = f"{draft.cover_letter}\n{draft.cv}"
    return max((int(match) for match in _YEARS.findall(text)), default=0)


def unsupported_lines(cv_markdown: str, cv_text: str) -> list[str]:
    """Lines of the tailored CV that name something the original CV doesn't (a misspelt
    or invented name), or most of whose words it never uses (a word counts when the
    CV has it or one with the same first five letters: "optimise" and "optimised").
    "###" headings (title, employer, dates) are checked for names; section headings
    are left out."""
    if not cv_text.strip():
        return []
    known = {word[:5] for word in _WORD.findall(cv_text.lower())}
    normal = _normal(cv_text)
    found = []
    for raw in cv_markdown.splitlines():
        heading = raw.lstrip().startswith("#")
        line = raw.strip().lstrip("#-*• ").strip()
        if not line or (heading and _level(raw) < 3):
            continue
        if unknown_names(line, normal):
            found.append(line)
            continue
        if heading:
            continue
        words = [w for w in _WORD.findall(line.lower()) if len(w) >= 4 and w not in _COMMON]
        new = [word for word in words if word[:5] not in known]
        if len(new) >= MIN_NEW_WORDS and len(new) >= MIN_NEW_SHARE * len(words):
            found.append(line)
    return found[:MAX_UNVERIFIED]


def unknown_names(text: str, normal_evidence: str) -> list[str]:
    """Names in the text that the evidence (see _normal) doesn't contain."""
    return [
        name
        for name in _NAME.findall(text.replace("**", ""))
        if _normal(name) not in _GENERIC_NAMES and _normal(name) not in normal_evidence
    ]


def unknown_name_sentences(letter: str, evidence: str) -> list[str]:
    """The letter's sentences that name something the evidence doesn't."""
    normal = _normal(evidence)
    sentences = re.split(r"(?<=[.!?])\s+|\n+", letter)
    return [s.strip() for s in sentences if s.strip() and unknown_names(s, normal)]


def _normal(text: str) -> str:
    """Lower case, every kind of hyphen as "-" (models write M‑Pesa with U+2011)."""
    return _HYPHENS.sub("-", text.lower())


def _ask(runtime, candidate, job, instructions) -> JobApplicationDraft:
    return runtime.ask(
        ROLE,
        JobApplicationDraft,
        candidate=_data(candidate),
        job=_data(job),
        instructions=escape(instructions.strip(), quote=False) or "None.",
    )


def _data(value: dict) -> str:
    return escape(json.dumps(value, ensure_ascii=False, indent=1), quote=False)
