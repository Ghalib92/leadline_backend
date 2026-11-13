"""Opportunity analyst: writes an executive brief for a newly found opportunity."""

import json
from datetime import date
from html import escape

from apps.ai.schemas.discovery import OpportunityBrief

ROLE = "opportunity_analyst"


def write_brief(runtime, *, opportunity: dict, company_evidence: str = "") -> OpportunityBrief:
    values = {
        "today": date.today().isoformat(),
        "organization": escape(runtime.organization_brief(), quote=False),
        "opportunity": escape(json.dumps(opportunity, ensure_ascii=False, indent=1), quote=False),
    }
    # From opportunity_analyst/v2: passages from the company's own documents.
    if "company_evidence" in runtime.prompt_for(ROLE).variables:
        values["company_evidence"] = company_evidence or "No passages were found."
    return runtime.ask(ROLE, OpportunityBrief, **values)
