"""CV reader: the headline, job titles, skills, years and level a CV shows, to fill in a
job seeker's job preferences. Skills and titles are kept only if the CV's text names
them, so a guess ("knows Git") never reaches the preferences."""

import re
from html import escape

from apps.ai.schemas.jobs import CvFacts

ROLE = "cv_reader"


def read_cv(runtime, *, cv: str) -> CvFacts:
    facts = runtime.ask(ROLE, CvFacts, cv=escape(cv, quote=False))
    words = _words(cv)
    return facts.model_copy(
        update={
            "headline": facts.headline.strip()[:200],
            "skills": _named_in(facts.skills, words)[:15],
            "target_roles": _unique(facts.target_roles)[:4],
        }
    )


def _named_in(items: list[str], words: set[str]) -> list[str]:
    """Items whose words all appear in the CV ("Django REST Framework" needs all three;
    "REST API" is found in "REST APIs")."""
    kept = []
    for item in _unique(items):
        needed = _words(item)
        if needed and all(word in words or f"{word}s" in words for word in needed):
            kept.append(item)
    return kept


def _unique(items: list[str]) -> list[str]:
    seen, kept = set(), []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            kept.append(item.strip()[:80])
    return kept


def _words(text: str) -> set[str]:
    """Lower-case words, keeping "c++", "c#" and "node.js" whole."""
    return {word.strip(".") for word in re.findall(r"[a-z0-9+#.]+", text.lower())} - {""}
