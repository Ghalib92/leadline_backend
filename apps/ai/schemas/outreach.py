from pydantic import BaseModel, ConfigDict, Field


class FoundContact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(default="", max_length=150, description="Person's name; empty for an inbox.")
    role: str = Field(default="", max_length=150, description="Job title or team, as written.")
    email: str = Field(default="", max_length=254, description="Exactly as written on the page.")
    phone: str = Field(default="", max_length=40, description="Exactly as written on the page.")
    page: int = Field(description="The number of the page it was found on.")
    kind: str = Field(default="person", description="'person' or 'inbox' (e.g. grants@…).")
    relevance: str = Field(
        default="",
        max_length=300,
        description="Why this contact is right for questions about this opportunity.",
    )
    confidence: int = Field(default=50, ge=0, le=100, description="How sure it is relevant.")


class ContactList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    contacts: list[FoundContact] = Field(
        default_factory=list,
        description=(
            "Contacts for this opportunity or its funder's grants/partnerships team. Only "
            "contacts written on the pages; never guess or construct an email address."
        ),
    )


class EmailDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    subject: str = Field(max_length=150)
    body: str = Field(
        description=(
            "Plain-text email body, greeting to sign-off, under 220 words. Mark facts the "
            "team must add as [TODO: …]."
        )
    )


class LeadPitchDraft(BaseModel):
    """What to say to a client lead. Field descriptions are sent to the model."""

    model_config = ConfigDict(extra="ignore")

    whatsapp: str = Field(
        description=(
            "A WhatsApp message to the business, under 80 words: a greeting, who we are in "
            "a few words, one specific thing we noticed about them (from their evidence), "
            "what we could do about it, and one easy ask (a short call or a quick demo). "
            "Plain text, no markdown. End with the sender's name and the organization's name."
        )
    )
    email_subject: str = Field(
        max_length=120, description="A short, specific subject line; no clickbait."
    )
    email_body: str = Field(
        description=(
            "Plain-text email, greeting to sign-off, under 150 words, with the same "
            "observation and ask as the WhatsApp message. Sign off with the sender's name "
            "and the organization's name."
        )
    )
    call_points: list[str] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "Three short points for a phone call, in order: how to open (who we are and "
            "why we're calling), the specific thing we noticed, and the ask."
        ),
    )
