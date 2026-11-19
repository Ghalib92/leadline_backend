"""Structured outputs for job seekers: what their CV says, and an application for one job.
Field descriptions are sent to the model."""

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

# apps.organizations.choices.Seniority (schemas don't import Django apps).
SeniorityCode = Literal["intern", "entry", "mid", "senior", "lead", "executive"]


class CvFacts(BaseModel):
    """What a CV shows, to fill in the job seeker's job preferences."""

    model_config = ConfigDict(extra="ignore")

    headline: str = Field(
        default="",
        description=(
            'A short professional headline from the CV, e.g. "Backend developer" or '
            '"Accountant (CPA-K)". Under 10 words. Empty if the CV gives no clear one.'
        ),
    )
    target_roles: list[str] = Field(
        default_factory=list,
        description=(
            "Up to 4 job titles this person is qualified for on the evidence of the CV: "
            "their current and recent titles, and close variants. No titles the CV can't "
            "support."
        ),
    )
    skills: list[str] = Field(
        default_factory=list,
        description=(
            "Up to 15 skills the CV shows (tools, languages, methods, certifications), most "
            "used first, each a few words. Only skills named in the CV."
        ),
    )
    years_experience: int | None = Field(
        default=None,
        ge=0,
        le=60,
        description=(
            "Years of paid work experience, counted from the dates in the CV. Null when the "
            "dates don't allow counting."
        ),
    )
    seniority: list[SeniorityCode] = Field(
        default_factory=list,
        description="The one or two levels that fit the CV best.",
    )

    @field_validator("target_roles", "skills", mode="before")
    @classmethod
    def _strings(cls, value):
        return [str(item).strip() for item in value or [] if str(item).strip()]

    @field_validator("seniority", mode="before")
    @classmethod
    def _known_levels(cls, value):
        return [item for item in value or [] if item in get_args(SeniorityCode)][:2]


class JobApplicationDraft(BaseModel):
    """A cover letter and a tailored CV for one job, from the candidate's own CV."""

    model_config = ConfigDict(extra="ignore")

    cover_letter: str = Field(
        min_length=150,
        description=(
            "A plain-text cover letter, 200 to 320 words, from greeting to sign-off. Opens "
            "with the role and why this candidate fits it, gives two or three concrete "
            "examples from the CV that match the posting's main requirements, and closes "
            "with a short, confident ask. Signed with the candidate's name."
        ),
    )
    cv: str = Field(
        min_length=150,
        description=(
            "The candidate's CV rewritten for this job in simple Markdown: '# Name', a "
            "one-line headline, '## Profile' (3 lines), '## Skills' (most relevant to the "
            "job first), '## Experience' ('### Title, Employer (dates)' with '-' bullets, "
            "the most relevant achievements first), '## Education', and any other "
            "sections the CV has, except lists of what they don't have yet. Under 650 "
            "words. Only facts from the CV."
        ),
    )
    gaps: list[str] = Field(
        default_factory=list,
        description=(
            "Up to 4 notes for the candidate, one per requirement of the posting that the "
            "CV doesn't show: what it is, and an honest way to handle it (related "
            "experience to point to, or how to answer if asked). Addressed to the "
            'candidate as "you".'
        ),
    )

    @field_validator("cover_letter")
    @classmethod
    def _paragraphs(cls, value):
        # Seen from Gemini: a whole letter on one line ("Informatics,I am writing").
        if "\n" not in value.strip():
            raise ValueError(
                "Separate the greeting, the paragraphs and the sign-off with line breaks."
            )
        return value


class LetterCheck(BaseModel):
    """Sentences of a cover letter that claim what the CV doesn't support."""

    model_config = ConfigDict(extra="ignore")

    unsupported: list[str] = Field(
        default_factory=list,
        description=(
            "Each sentence of the letter that states something about the candidate the "
            "CV and summary don't support, copied exactly as it appears in the letter. "
            "Empty when every claim is supported."
        ),
    )
