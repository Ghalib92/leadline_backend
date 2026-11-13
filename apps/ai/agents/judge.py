"""Judge: scores fit against a written rubric. Separate from the agents that found
the opportunity, so nothing grades its own work.

From judge/v2 the prompt also shows the organization's most similar past decisions
(decision memory), so scores follow what its people actually approve and reject.
From judge/v3 it also gets passages from the organization's own documents, limited
to the folders the run may read, as evidence of track record and eligibility.
"""

import json
from html import escape

from apps.ai.schemas.discovery import JudgeVerdict
from apps.ai.tools.decisions import format_decisions

ROLE = "judge"


def judge_fit(
    runtime,
    opportunity: dict,
    past_decisions: list[dict] | None = None,
    company_evidence: str = "",
):
    values = {
        "organization": escape(runtime.organization_brief(), quote=False),
        "opportunity": escape(json.dumps(opportunity, ensure_ascii=False, indent=1), quote=False),
    }
    if "past_decisions" in runtime.prompt_for(ROLE).variables:
        values["past_decisions"] = format_decisions(past_decisions or [])
    if "company_evidence" in runtime.prompt_for(ROLE).variables:
        values["company_evidence"] = company_evidence or "No passages were found."
    return runtime.ask(ROLE, JudgeVerdict, **values)
