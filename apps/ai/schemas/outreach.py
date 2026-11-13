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
