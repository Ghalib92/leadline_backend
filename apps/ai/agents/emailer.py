"""Email agent: drafts outreach for a person to review. It never sends anything."""

import json
from html import escape

from apps.ai.schemas.outreach import EmailDraft

ROLE = "emailer"


def draft_email(runtime, *, opportunity: dict, contact: dict, purpose: str, instructions: str):
    return runtime.ask(
        ROLE,
        EmailDraft,
        purpose=escape(purpose, quote=False),
        instructions=escape(instructions.strip(), quote=False) or "None.",
        organization=escape(runtime.organization_brief(), quote=False),
        opportunity=escape(json.dumps(opportunity, ensure_ascii=False, indent=1), quote=False),
        contact=escape(json.dumps(contact, ensure_ascii=False), quote=False),
    )
