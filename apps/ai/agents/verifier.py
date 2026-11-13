"""Verifier: reads one listing and extracts its facts (deadline, amount, eligibility)."""

from datetime import date
from html import escape

from apps.ai.schemas.discovery import VerifiedOpportunity
from apps.organizations.choices import OpportunityType

ROLE = "verifier"


def verify_listing(runtime, *, url: str, source: str, page: str) -> VerifiedOpportunity:
    verified = runtime.ask(
        ROLE,
        VerifiedOpportunity,
        today=date.today().isoformat(),
        opportunity_types=", ".join(OpportunityType.values),
        url=escape(url),
        source=escape(source),
        # Escaped so page text cannot close the <page> tag and pose as instructions.
        page=escape(page[: runtime.settings.max_page_chars], quote=False),
    )
    if verified.opportunity_type not in OpportunityType.values:
        verified.opportunity_type = OpportunityType.OTHER
    return verified
