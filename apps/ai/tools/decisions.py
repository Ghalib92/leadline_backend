"""Decision memory for the judge: this organization's most similar past decisions.

Reviews are the organization's own data, but their comments are free text, so they
are escaped and tagged as data in the prompt like everything else.
"""

from html import escape

from apps.opportunities.choices import RejectionReason
from apps.opportunities.memory import embed_facts
from apps.opportunities.selectors import similar_decisions


def past_decisions(
    organization_id, facts: dict, *, limit=4, exclude_opportunity_id=None, before=None
) -> list[dict]:
    reviews = similar_decisions(
        organization_id,
        embed_facts(facts),
        limit=limit,
        exclude_opportunity_id=exclude_opportunity_id,
        before=before,
    )
    return [
        {
            "decision": "approved" if review.decision == "approve" else "rejected",
            "reason": RejectionReason(review.reason).label if review.reason else "",
            "comment": review.comment[:300],
            "title": review.snapshot.get("title", ""),
            "funder": review.snapshot.get("funder", ""),
            "ai_score": review.match_score,
            "similarity": round(1 - review.distance, 3),
        }
        for review in reviews
    ]


def format_decisions(decisions: list[dict]) -> str:
    if not decisions:
        return "(no similar past decisions yet)"
    lines = []
    for item in decisions:
        line = f'- {item["decision"].upper()}: "{item["title"]}"'
        if item["funder"]:
            line += f" ({item['funder']})"
        line += f"; AI score was {item['ai_score']}"
        if item["reason"]:
            line += f"; reason: {item['reason']}"
        if item["comment"]:
            line += f'; their note: "{item["comment"]}"'
        lines.append(escape(line, quote=False))
    return "\n".join(lines)
