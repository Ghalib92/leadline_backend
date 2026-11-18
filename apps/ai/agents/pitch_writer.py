"""Pitch writer: what to say to an approved client lead (a WhatsApp message, an email
and points for a call), from the lead's checked evidence and the organization's
clients brief. It never sends anything; a person sends it from their own phone or
email."""

import json
import re
from html import escape

from apps.ai.schemas.outreach import LeadPitchDraft

ROLE = "pitch_writer"
UNCHECKED = "not known"  # the start of WEBSITE_STATUS["unknown"] (apps.outreach)
# "You don't have a website", "no online booking", "couldn't find your site"…
_GAP = r"(?:[\w’'-]+\s+){0,5}?"  # a few words ("a public", "a fee payment or results")
_NO_WEBSITE = re.compile(
    r"\b(?:(?:do|does|have|has)(?:n['’]?t| not)\s+"
    + _GAP
    + r"(?:online|web|website|site|booking|enrol|enroll)"
    r"|no\s+" + _GAP + r"(?:website|site|presence|booking)"
    r"|(?:could|can)(?:n['’]?t| not)\s+(?:find|locate|see|spot)\s+"
    + _GAP
    + r"(?:website|site|portal|page|booking|online))",
    re.IGNORECASE,
)
DONT_CLAIM = (
    "Nobody has looked for this business's website. Do not write \"I couldn't find a "
    'website", "you don\'t have a website", "no online booking" or anything like it. '
    'Ask instead, e.g. "Do parents enrol online today?"'
)


def write_pitch(runtime, *, lead: dict, instructions: str, sender: dict | None = None):
    """The pitch; rewritten once when it claims, unchecked, that the business has no
    website (the model was told not to, and sometimes does anyway)."""
    draft = _ask(runtime, lead, instructions, sender)
    unchecked = str(lead.get("website_status", UNCHECKED)).startswith(UNCHECKED)
    if unchecked and claims_no_website(draft):
        draft = _ask(runtime, lead, f"{instructions.strip()}\n{DONT_CLAIM}".strip(), sender)
    return draft


def claims_no_website(draft: LeadPitchDraft) -> bool:
    text = " ".join([draft.whatsapp, draft.email_subject, draft.email_body, *draft.call_points])
    return bool(_NO_WEBSITE.search(text))


def _ask(runtime, lead, instructions, sender) -> LeadPitchDraft:
    return runtime.ask(
        ROLE,
        LeadPitchDraft,
        organization=escape(runtime.organization_brief(), quote=False),
        lead=escape(json.dumps(lead, ensure_ascii=False, indent=1), quote=False),
        sender=escape(json.dumps(sender or {}, ensure_ascii=False), quote=False),
        instructions=escape(instructions.strip(), quote=False) or "None.",
    )
