"""Structured outputs of the Proposal Writer. Field descriptions go to the model."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RequiredSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(max_length=150, description="Section name as the call words it.")
    guidance: str = Field(default="", description="What the call says this section must cover.")
    word_limit: int | None = Field(default=None, ge=0, description="Word limit, if stated.")


class ProposalRequirements(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str = Field(
        default="", description="Two sentences: what the call funds or buys, and from whom."
    )
    sections: list[RequiredSection] = Field(
        default_factory=list,
        description="Sections or questions the application must answer, in the call's order.",
    )
    evaluation_criteria: list[str] = Field(
        default_factory=list, description="How applications are scored, with weights if given."
    )
    eligibility: list[str] = Field(default_factory=list, description="Who may apply.")
    documents_required: list[str] = Field(
        default_factory=list, description="Attachments asked for (budget, CVs, registration…)."
    )
    budget_rules: list[str] = Field(
        default_factory=list, description="Amount limits, co-funding, eligible costs."
    )
    format_rules: list[str] = Field(
        default_factory=list, description="Language, length, template or submission rules."
    )
    deadline: str = Field(default="", description="Closing date as YYYY-MM-DD, if stated.")


REQUIREMENT_KINDS = ("certificate", "form", "security", "experience", "financial", "other")
DATE_KINDS = ("clarification", "meeting", "site_visit", "closing", "opening", "other")
CHECKLIST_STATUSES = ("ready", "check", "missing", "prepare")


def _one_of(value, allowed, default):
    value = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return value if value in allowed else default


class MandatoryRequirement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str = Field(default="", max_length=20, description="The tender's own code, e.g. MR1.")
    requirement: str = Field(
        max_length=400, description="What must be submitted or met, as the tender words it."
    )
    kind: str = Field(
        default="other",
        description=(
            "certificate (a document the firm already holds: registration, tax compliance, "
            "licences, AGPO), form (filled in for this bid: forms, declarations, power of "
            "attorney), security (bid security or bond), experience (past contracts, "
            "references, staff and CVs), financial (audited accounts, bank statements), other."
        ),
    )

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, value):
        return _one_of(value, REQUIREMENT_KINDS, "other")


class KeyDate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event: str = Field(max_length=150, description="e.g. 'Clarification requests close'.")
    date: str = Field(description="YYYY-MM-DD.")
    time: str = Field(default="", description="HH:MM (24h), if stated.")
    kind: str = Field(
        default="other", description="clarification, meeting, site_visit, closing, opening, other."
    )

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, value):
        return _one_of(value, DATE_KINDS, "other")


class TenderRequirements(ProposalRequirements):
    """A tender's requirements: the call's fields plus what a bid must comply with."""

    reference: str = Field(default="", description="The tender number.")
    mandatory_requirements: list[MandatoryRequirement] = Field(
        default_factory=list,
        description=(
            "Every mandatory and eligibility requirement, in the tender's order. A bid "
            "missing one is rejected before scoring."
        ),
    )
    key_dates: list[KeyDate] = Field(
        default_factory=list,
        description="Clarification deadline, pre-bid meeting, site visit, closing, opening.",
    )
    submission: list[str] = Field(
        default_factory=list,
        description="How and where to submit: portal or address, copies, envelopes, binding.",
    )
    pass_mark: str = Field(default="", description="The technical pass mark, if stated.")
    bid_security: str = Field(default="", description="Amount and form of bid security.")
    validity: str = Field(default="", description="How long bids must stay valid.")


class ChecklistVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    n: int = Field(description="The requirement's number.")
    status: str = Field(
        description=(
            "ready (a company document covers it), check (a document may cover it; a person "
            "must confirm, e.g. its expiry), missing (no company document covers it), "
            "prepare (made for this bid: forms, declarations, bid security)."
        )
    )
    document_ids: list[str] = Field(
        default_factory=list, description="IDs of the company documents that cover it."
    )
    note: str = Field(default="", max_length=300, description="One short sentence, if useful.")

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value):
        return _one_of(value, CHECKLIST_STATUSES, "check")


class BidChecklist(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[ChecklistVerdict] = Field(description="One verdict per requirement.")


class OutlineSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str = Field(
        max_length=40, description="Short lowercase id, letters and underscores (e.g. 'budget')."
    )
    title: str = Field(max_length=150)
    purpose: str = Field(default="", description="What this section must achieve for reviewers.")
    key_points: list[str] = Field(
        default_factory=list, description="Points to make, grounded in the organization's facts."
    )
    evidence_query: str = Field(
        default="",
        description="A short search query for the organization's documents to support it.",
    )
    target_words: int = Field(default=0, ge=0, le=3000)


class ProposalOutline(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sections: list[OutlineSection] = Field(description="The proposal's sections, in order.")


class DraftedSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str = Field(
        description=(
            "The section text in Markdown (paragraphs, bullet lists, bold). No heading: the "
            "title is added separately. Cite evidence as [1], [2] by passage number."
        )
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description="Facts the organization must add before submitting (marked [TODO] in text).",
    )


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="ignore")

    section: str = Field(default="", description="Section key, or empty for the whole draft.")
    issue: str
    suggestion: str = ""


class ProposalReview(BaseModel):
    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=0, le=100, description="Readiness against the requirements, 0-100.")
    strengths: list[str] = Field(default_factory=list)
    issues: list[ReviewIssue] = Field(default_factory=list)
    missing_requirements: list[str] = Field(
        default_factory=list, description="Requirements of the call the draft does not meet yet."
    )


class DraftGrade(BaseModel):
    """An eval grader's verdict on a proposal draft (0-10 per criterion)."""

    model_config = ConfigDict(extra="ignore")

    requirements_coverage: int = Field(ge=0, le=10, description="Answers what the call asks.")
    specificity: int = Field(ge=0, le=10, description="Concrete facts, numbers, places.")
    evidence_use: int = Field(ge=0, le=10, description="Claims backed by cited evidence.")
    honesty: int = Field(ge=0, le=10, description="No invented facts; gaps marked [TODO].")
    clarity: int = Field(ge=0, le=10, description="Clear, well organized, persuasive.")
    issues: list[str] = Field(default_factory=list, description="The most important problems.")

    @property
    def overall(self) -> int:
        """0-100, the mean of the criteria."""
        parts = (
            self.requirements_coverage,
            self.specificity,
            self.evidence_use,
            self.honesty,
            self.clarity,
        )
        return round(sum(parts) * 2)
