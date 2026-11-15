"""Client (business) search: the businesses sources return and the client judge's verdicts."""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

# A "website" on one of these is a social profile or link page, not a site of its own.
SOCIAL_HOSTS = {
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "tiktok.com": "tiktok",
    "x.com": "x",
    "twitter.com": "x",
    "linktr.ee": "linktree",
    "beacons.ai": "linktree",
    "linkin.bio": "linktree",
    "wa.me": "whatsapp",
    "wa.link": "whatsapp",
    "api.whatsapp.com": "whatsapp",
}


def social_kind(url: str) -> str:
    """The kind of social profile or link page `url` is ("instagram"…), or "" for a site."""
    host = urlsplit(url if "//" in url else f"//{url}").netloc.lower().removeprefix("www.")
    host = host.removeprefix("m.")
    return SOCIAL_HOSTS.get(host, "")


class Business(BaseModel):
    """One business as a directory lists it. Facts only, nothing inferred."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=300)
    source: str  # sources.yaml key, e.g. "osm_places"
    source_label: str  # shown to people: "OpenStreetMap"
    source_url: str  # the listing on the source (map page)
    category: str = ""  # as the source names it: "hotel", "Beauty salon"
    address: str = ""
    city: str = ""
    country: str = ""  # ISO alpha-2 when known
    phone: str = ""
    email: str = ""
    website: str = ""
    socials: dict[str, str] = {}  # kind → profile URL
    rating: float | None = None
    reviews: int | None = None
    # True when the source reliably lists a website and phone when the business has
    # them (Google Maps); False when they are often just not mapped (OpenStreetMap).
    complete_listing: bool = False

    def normalised(self) -> "Business":
        """A website without a scheme gets https:// (directories often drop it); a
        social profile given as the website moves to `socials`."""
        website = self.website.strip()
        if website and "://" not in website:
            website = f"https://{website}"
        kind = social_kind(website) if website else ""
        if not kind:
            return self.model_copy(update={"website": website})
        return self.model_copy(
            update={"website": "", "socials": {**self.socials, kind: self.website}}
        )

    def place(self) -> str:
        return ", ".join(part for part in (self.city, self.country) if part)

    def observed_signals(self, *, busy_reviews: int) -> list[str]:
        """Signs it needs a digital service that the listing itself shows
        (apps.organizations.choices.LeadSignal values). Never a guess: a missing website
        only counts when the source lists websites reliably."""
        signals = []
        if not self.website:
            if self.socials:
                signals.append("social_only")
            elif self.complete_listing:
                signals.append("no_website")
        elif self.website.startswith("http://"):
            signals.append("no_https")
        if self.reviews is not None and self.reviews >= busy_reviews:
            signals.append("many_reviews")
        return signals

    def as_details(self, *, signals: list[str], pitch: str = "") -> dict:
        """What is stored on the lead (Opportunity.business)."""
        details = self.model_dump(exclude={"source_label"}, exclude_defaults=True)
        details["source_label"] = self.source_label
        details["signals"] = signals
        details["complete_listing"] = self.complete_listing
        if pitch:
            details["pitch_angle"] = pitch
        return details


# ---------------------------------------------------------------------------
# Client judge. Field descriptions are sent to the model with the schema.
# ---------------------------------------------------------------------------


class ClientVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int = Field(description="The number of the business in the list.")
    fit: int = Field(
        ge=0,
        le=40,
        description="Customer fit only, 0–40: the kind, size and place of business the "
        "brief targets. Need and reachability are counted separately.",
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="Two or three short reasons, citing the business's listed facts and the "
        "organization's brief.",
    )
    pitch_angle: str = Field(
        default="",
        max_length=300,
        description="One sentence: what to offer this business first and why, from its facts.",
    )


class ClientVerdicts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdicts: list[ClientVerdict] = Field(
        default_factory=list, description="One verdict per business, by its number."
    )
