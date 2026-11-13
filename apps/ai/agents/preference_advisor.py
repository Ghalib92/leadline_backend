"""Preference advisor: suggests keywords to exclude, from repeated topic rejections."""

from html import escape

from apps.ai.schemas.discovery import PreferenceAdvice

ROLE = "preference_advisor"


def suggest_keywords(runtime, *, focus: str, rejections: list[dict], already_excluded: list[str]):
    """`rejections`: [{title, funder, reason, comment}], numbered from 1 in the prompt."""
    lines = []
    for number, item in enumerate(rejections, start=1):
        line = f"{number}. {item['title']}"
        if item.get("funder"):
            line += f" ({item['funder']})"
        line += f". Reason: {item['reason']}."
        if item.get("comment"):
            line += f' Note: "{item["comment"][:300]}"'
        lines.append(line)
    return runtime.ask(
        ROLE,
        PreferenceAdvice,
        focus=escape(focus, quote=False),
        rejections=escape("\n".join(lines), quote=False),
        already_excluded=escape(", ".join(already_excluded) or "(none)", quote=False),
    )
