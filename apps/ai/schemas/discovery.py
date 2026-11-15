from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Candidate(BaseModel):
    """A verified, scored opportunity a run hands over for deduplication and saving."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    funder: str = Field(default="", max_length=200)
    summary: str = ""
    opportunity_type: str  # apps.organizations.choices.OpportunityType value
    source_name: str = Field(default="", max_length=120)
    source_url: HttpUrl
    eligibility: str = ""
    amount_min: Decimal | None = Field(default=None, ge=0)
    amount_max: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="", max_length=3)
    deadline: datetime | None = None
    location: str = Field(default="", max_length=200)
    match_score: int = Field(ge=0, le=100)
    match_reasons: list[str] = []
    requirements: dict = {}
    verification: dict = {}
    # A client lead's business facts (apps.ai.schemas.clients.Business.as_details()).
    business: dict = {}


# ---------------------------------------------------------------------------
# Agent outputs. Field descriptions are sent to the model with the schema.
# ---------------------------------------------------------------------------


class PlannedSearch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str = Field(description="One of the available source names, exactly as listed.")
    query: str = Field(
        min_length=2,
        max_length=200,
        description=(
            "A short search query (3 to 10 words) for that source. For structured sources "
            "use plain topic keywords; for web search you may add words like 'call for "
            "proposals', 'grant', 'tender' and a country or region."
        ),
    )
    reason: str = Field(default="", description="Why this search fits the organization.")


class SearchPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    searches: list[PlannedSearch] = Field(
        description="Searches to run now, most promising first, within the limit given."
    )


class LeadPick(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int = Field(description="The number of the result in the list.")
    reason: str = Field(default="", description="Why it looks like a real, relevant opportunity.")


class LeadSelection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    picks: list[LeadPick] = Field(
        default_factory=list,
        description=(
            "Results that look like a specific, currently open opportunity the "
            "organization could apply for. Leave out news articles, lists of many "
            "opportunities, expired calls and unrelated pages."
        ),
    )


class VerifiedOpportunity(BaseModel):
    """What the verifier read from one listing."""

    model_config = ConfigDict(extra="ignore")

    is_opportunity: bool = Field(
        description=(
            "True only if the page describes one specific opportunity (a grant, tender, "
            "call, fellowship…) that organizations can apply to or bid for."
        )
    )
    is_open: bool = Field(
        description="True if it is still accepting applications, or opens in the future."
    )
    title: str = Field(default="", max_length=300, description="The official title.")
    funder: str = Field(default="", max_length=200, description="Who funds or issues it.")
    opportunity_type: str = Field(default="other", description="One of the allowed types.")
    summary: str = Field(
        default="", description="Two to four sentences: what it funds or buys, and for whom."
    )
    eligibility: str = Field(default="", description="Who can apply, as stated.")
    amount_min: float | None = Field(default=None, ge=0)
    amount_max: float | None = Field(default=None, ge=0)
    currency: str = Field(default="", max_length=3, description="ISO 4217 code, e.g. USD.")
    deadline: str = Field(
        default="", description="Closing date as YYYY-MM-DD, or empty if not stated."
    )
    location: str = Field(default="", max_length=200, description="Where the work happens.")
    requirements: list[str] = Field(
        default_factory=list,
        description="Key application requirements and documents asked for.",
    )
    rejection_reason: str = Field(
        default="", description="When not an open opportunity, a short reason why."
    )


class JudgeVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=0, le=100, description="Overall fit from 0 to 100, per the rubric.")
    reasons: list[str] = Field(
        default_factory=list,
        description="Two to four short reasons it fits, citing the organization's facts.",
    )
    concerns: list[str] = Field(
        default_factory=list,
        description="Eligibility doubts or gaps the organization should check.",
    )


class SuggestedKeyword(BaseModel):
    model_config = ConfigDict(extra="ignore")

    keyword: str = Field(
        max_length=60,
        description="One to three words that mark opportunities to leave out, e.g. 'road "
        "construction'. Specific enough not to exclude what they do want.",
    )
    rejections: list[int] = Field(
        default_factory=list, description="Numbers of the rejections this keyword explains."
    )
    why: str = Field(default="", max_length=300, description="One sentence for the admin.")


class PreferenceAdvice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    keywords: list[SuggestedKeyword] = Field(
        default_factory=list,
        description="At most three keywords. Only ones that explain two or more rejections.",
    )


# ---------------------------------------------------------------------------
# Opportunity Analyst brief
# ---------------------------------------------------------------------------


class EligibilityCheckItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    criterion: str = Field(description="Eligibility requirement (e.g. 'Registered NGO in Kenya').")
    meets: bool = Field(description="Whether the organization appears to meet this requirement.")
    notes: str = Field(default="", description="Reason or supporting evidence.")


class OpportunityBrief(BaseModel):
    model_config = ConfigDict(extra="ignore")

    what_it_is: str = Field(
        description="A concise executive summary (2-3 sentences) of what this call funds."
    )
    why_it_fits: str = Field(
        description=(
            "Clear explanation of why this matches the organization's focus, offerings, "
            "and track record."
        )
    )
    eligibility_checklist: list[EligibilityCheckItem] = Field(
        default_factory=list,
        description="Checklist of key eligibility criteria and whether the client meets them.",
    )
    documents_needed: list[str] = Field(
        default_factory=list,
        description=(
            "Required proposal/bid submission documents (e.g. 'Project Narrative', "
            "'Detailed Budget', 'Tax Compliance Certificate')."
        ),
    )
    deadline_plan: str = Field(
        description=(
            "Actionable timeline recommendation based on the deadline (e.g. "
            "'Deadline is Nov 15; begin drafting 3 weeks prior')."
        ),
    )
    effort_estimate: str = Field(
        description=(
            "Estimated effort: 'low' (1-2 days), 'medium' (1 week), or 'high' (2+ weeks), "
            "with rationale."
        ),
    )
    next_step: str = Field(
        description=(
            "The immediate recommended next action (e.g. 'Confirm eligibility on registration "
            "type, then draft proposal outline')."
        ),
    )


# ---------------------------------------------------------------------------
# Source Reader schemas
# ---------------------------------------------------------------------------


class ExtractedCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(description="The title or headline of the opportunity / open call.")
    url: str = Field(description="Direct URL or link path to the opportunity call page.")
    funder: str = Field(default="", description="Name of the funding organization or issuer.")
    summary: str = Field(
        default="", description="Brief 1-2 sentence summary of what is funded or solicited."
    )
    opportunity_type: str = Field(
        default="other", description="Type of call (e.g. grant, tender, rfp, fellowship, other)."
    )


class SourceExtraction(BaseModel):
    """Opportunities and open calls discovered from an organization's custom source page."""

    model_config = ConfigDict(extra="ignore")

    calls: list[ExtractedCall] = Field(
        default_factory=list,
        description="List of open calls or opportunity links found on this page.",
    )
