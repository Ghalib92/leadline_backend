"""The pitch writer's check for claims about a website nobody looked for."""

import pytest

from apps.ai.agents.pitch_writer import claims_no_website
from apps.ai.schemas.outreach import LeadPitchDraft


@pytest.mark.parametrize(
    ("text", "claims"),
    [
        ("I couldn’t find a public website for the school", True),
        ("I couldn't find a website for the school", True),
        ("I noticed you don't yet have an online enrollment site", True),
        ("your school has no website", True),
        ("there's no public website", True),
        ("doesn't have online booking", True),
        ("I couldn't find an online portal for fee payments", True),
        ("I couldn’t locate a fee payment or results portal", True),
        ("Do parents enrol online today?", False),
        ("We build websites with booking", False),
        ("I can't wait to show you a demo of our booking site", False),
        ("We have no doubt your guests would love online booking", False),
    ],
)
def test_claims_about_a_missing_website_are_caught(text, claims):
    draft = LeadPitchDraft(whatsapp=text, email_subject="Hello", email_body="Hi", call_points=[])
    assert claims_no_website(draft) is claims
