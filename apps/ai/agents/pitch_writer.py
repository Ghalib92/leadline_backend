"""Pitch writer: what to say to an approved client lead (a WhatsApp message, an email
and points for a call), from the lead's checked evidence and the organization's
clients brief. It never sends anything; a person sends it from their own phone or
email."""

import json
from html import escape

from apps.ai.schemas.outreach import LeadPitchDraft

ROLE = "pitch_writer"


def write_pitch(runtime, *, lead: dict, instructions: str) -> LeadPitchDraft:
    return runtime.ask(
        ROLE,
        LeadPitchDraft,
        organization=escape(runtime.organization_brief(), quote=False),
        lead=escape(json.dumps(lead, ensure_ascii=False, indent=1), quote=False),
        instructions=escape(instructions.strip(), quote=False) or "None.",
    )
