"""Structured outputs of the Proposal Writer. Field descriptions go to the model."""

from pydantic import BaseModel, ConfigDict, Field


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
