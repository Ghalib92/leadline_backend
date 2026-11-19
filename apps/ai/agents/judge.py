"""Judge: scores fit against a written rubric. Separate from the agents that found
the opportunity, so nothing grades its own work.

From judge/v2 the prompt also shows the organization's most similar past decisions
(decision memory), so scores follow what its people actually approve and reject.
From judge/v3 it also gets passages from the organization's own documents, limited
to the folders the run may read, as evidence of track record and eligibility.

Tender runs use the tender judge (tender_judge/vN): the same inputs, a rubric for
bidding (scope, eligibility incl. AGPO, time to bid, value and buyer).
"""

import json
from html import escape

from apps.ai.schemas.discovery import JudgeVerdict
from apps.ai.tools.decisions import format_decisions

ROLE = "judge"
TENDER_ROLE = "tender_judge"
JOB_ROLE = "job_judge"


def judge_role(goal: str) -> str:
    return {"tenders": TENDER_ROLE, "jobs": JOB_ROLE}.get(goal, ROLE)


def judge_fit(
    runtime,
    opportunity: dict,
    past_decisions: list[dict] | None = None,
    company_evidence: str = "",
    *,
    role: str = ROLE,
):
    values = {
        "organization": escape(runtime.organization_brief(), quote=False),
        "opportunity": escape(json.dumps(opportunity, ensure_ascii=False, indent=1), quote=False),
    }
    if "past_decisions" in runtime.prompt_for(role).variables:
        values["past_decisions"] = format_decisions(past_decisions or [])
    if "company_evidence" in runtime.prompt_for(role).variables:
        values["company_evidence"] = company_evidence or "No passages were found."
    return runtime.ask(role, JudgeVerdict, **values)
