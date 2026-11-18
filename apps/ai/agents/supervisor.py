"""Supervisor: plans which sources to search and with which queries.

Whether a round found enough is decided in code (graphs/discovery/nodes.review):
it is a threshold, not a judgment call.
"""

import json
from datetime import date
from html import escape

from apps.ai.exceptions import StructuredOutputError
from apps.ai.schemas.discovery import SearchPlan

ROLE = "supervisor"


def is_us_eligible(profile: dict) -> bool:
    countries = {
        (profile.get("country") or "").upper(),
        *(str(c).upper() for c in profile.get("operating_countries") or []),
        *(str(c).upper() for c in profile.get("target_countries") or []),
    }
    return any(c in {"US", "USA", "UNITED STATES"} for c in countries if c)


def _format_source(name: str, spec, us_eligible: bool) -> str:
    desc = spec.covers
    if name == "grants_gov" and not us_eligible:
        desc = (
            f"{spec.covers} (NOTE: Organization is non-US; Grants.gov calls rarely fit "
            "unless explicitly open to international applicants)."
        )
    return f"  - {name} ({spec.domain}): {desc}"


def plan_searches(runtime, *, done: list[str], limit: int) -> tuple[list[tuple[str, str]], bool]:
    """Up to `limit` (source, query) pairs; the flag is True when the fallback was used."""
    request = runtime.request
    us_eligible = is_us_eligible(request.profile)
    # Order sources: if non-US, deprioritize grants_gov behind international sources
    sorted_names = sorted(
        [name for name in runtime.source_specs if name in runtime.sources],
        key=lambda s: (1 if (s == "grants_gov" and not us_eligible) else 0, s),
    )
    sources = "\n".join(
        _format_source(name, runtime.source_specs[name], us_eligible) for name in sorted_names
    )
    agent = {
        "name": request.agent_name,
        "instructions": getattr(request, "instructions", "") or "None.",
        "keywords": request.keywords,
        "opportunity_types": request.opportunity_types,
    }
    try:
        plan = runtime.ask(
            ROLE,
            SearchPlan,
            today=date.today().isoformat(),
            max_searches=limit,
            sources=sources,
            opportunity_types=", ".join(request.opportunity_types) or "any",
            organization=escape(runtime.organization_brief(), quote=False),
            agent=escape(json.dumps(agent, ensure_ascii=False), quote=False),
            done="\n".join(f"- {item}" for item in done) or "(nothing yet)",
        )
    except StructuredOutputError:
        return fallback_plan(runtime, done=done, limit=limit), True
    searches, seen = [], set(done)
    for item in plan.searches:
        key = f"{item.source}: {item.query.strip()}"
        if item.source in runtime.sources and key not in seen:
            seen.add(key)
            searches.append((item.source, item.query.strip()))
    if not searches:
        return fallback_plan(runtime, done=done, limit=limit), True
    return searches[:limit], False


def fallback_plan(runtime, *, done: list[str], limit: int) -> list[tuple[str, str]]:
    """Keywords and focus areas across the available sources, when planning failed."""
    request = runtime.request
    dossier = request.dossier or {}
    topics = list(request.keywords) + list(dossier.get("search_keywords") or [])
    topics += [str(sector).replace("_", " ") for sector in request.profile.get("sectors", [])]
    topics += [f["name"] for f in getattr(request, "folders", []) if f.get("name")]
    topics = list(dict.fromkeys(topic.strip() for topic in topics if topic.strip()))
    topics = topics or [request.agent_name]
    us_eligible = is_us_eligible(request.profile)
    # Exclude grants_gov from fallback queries for non-US organizations
    available_sources = [s for s in runtime.sources if s != "grants_gov" or us_eligible] or list(
        runtime.sources
    )
    pairs, seen = [], set(done)
    for topic in topics:
        for source in available_sources:
            key = f"{source}: {topic}"
            if key not in seen:
                seen.add(key)
                pairs.append((source, topic))
    return pairs[:limit]
