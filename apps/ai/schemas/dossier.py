from pydantic import BaseModel, ConfigDict, Field


class Dossier(BaseModel):
    """The company dossier: what every agent knows about the organization.

    Field descriptions are sent to the model as part of the output schema.
    """

    # Gemini sometimes starts list items with a space; keywords feed searches.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    summary: str = Field(
        description=(
            "Three to five sentences: who the organization is, what it does, for whom and "
            "where. Plain language, no marketing claims."
        )
    )
    organization_type: str = Field(
        default="", description="E.g. NGO, social enterprise, SME, university."
    )
    mission: str = Field(default="", description="One sentence.")
    focus_areas: list[str] = Field(
        default_factory=list, description="Sectors and themes it works on."
    )
    offerings: list[str] = Field(
        default_factory=list, description="Programmes, products and services it delivers."
    )
    beneficiaries: list[str] = Field(
        default_factory=list, description="Who it serves, as specifically as the inputs allow."
    )
    geographies: list[str] = Field(
        default_factory=list, description="Countries and regions where it operates."
    )
    track_record: list[str] = Field(
        default_factory=list,
        description="Concrete achievements, with numbers, years and places where given.",
    )
    past_funders: list[str] = Field(
        default_factory=list, description="Funders, partners and clients it has worked with."
    )
    strengths: list[str] = Field(
        default_factory=list,
        description="Capabilities that make it competitive for funding or contracts.",
    )
    eligibility_facts: list[str] = Field(
        default_factory=list,
        description=(
            "Facts funders filter on: legal status, registration, years operating, team "
            "size, budget, audited accounts, women- or youth-led."
        ),
    )
    seeking: list[str] = Field(
        default_factory=list,
        description="Opportunity types, amounts, regions and partnerships it is looking for.",
    )
    search_keywords: list[str] = Field(
        default_factory=list,
        description="Eight to twenty short search terms for finding relevant opportunities.",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description=(
            "Information missing from the inputs that would improve matching or proposals, "
            "phrased as something the organization could add."
        ),
    )
