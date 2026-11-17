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


# Signs that a feature is missing from a website. They are noted only when the brief
# asks for them (or names no signs at all): a law firm without online booking is not
# evidence of anything unless booking is what the organization sells.
FEATURE_SIGNALS = {
    "booking": "no_online_booking",
    "chat": "no_chatbot",
    "payments": "no_online_payments",
}


class SiteCheck(BaseModel):
    """What a business's website showed when the lead lookup read it. None means not
    known (the page could not be read for it), never "no"."""

    model_config = ConfigDict(extra="forbid")

    url: str  # the address checked, after redirects
    reachable: bool = True
    # Why it counts as down: "its domain no longer resolves", "it returns HTTP 500"…
    problem: str = ""
    # robots.txt doesn't let us read it, or the site refuses automated visitors.
    blocked: bool = False
    https: bool | None = None
    certificate_error: bool = False
    mobile_ready: bool | None = None  # a viewport set up for phones
    copyright_year: int | None = None
    booking: bool | None = None
    chat: bool | None = None
    payments: bool | None = None
    mobile_speed: int | None = None  # Google PageSpeed mobile performance, 0–100
    # Set by the checker from sources.yaml's thresholds (clients.lookup):
    slow: bool = False  # mobile_speed below slow_below
    stale: bool = False  # copyright_year at least stale_after_years old
    checked_at: str = ""  # ISO date
    # A rendering service that read the page for us (sites built by JavaScript or that
    # refuse simple clients), e.g. "Jina Reader"; "" when we read it ourselves.
    read_by: str = ""

    @property
    def readable(self) -> bool:
        """It loaded and we may read it, so what it shows (or lacks) is evidence."""
        return self.reachable and not self.blocked

    def signals(self, wanted: frozenset[str] | set[str] = frozenset()) -> list[str]:
        """The LeadSignal values this check shows (see FEATURE_SIGNALS for `wanted`)."""
        if not self.reachable:
            return ["broken_website"]
        if self.blocked:
            return []
        checks = {
            "no_https": self.https is False,
            "not_mobile_friendly": self.mobile_ready is False,
            "slow_website": self.slow,
            "outdated_website": self.stale,
        }
        signals = [signal for signal, shown in checks.items() if shown]
        signals += [
            signal
            for feature, signal in FEATURE_SIGNALS.items()
            if getattr(self, feature) is False and (not wanted or signal in wanted)
        ]
        return signals


class Business(BaseModel):
    """One business as a directory lists it, plus what the lead lookup found (each with
    where it came from). Facts only, nothing inferred."""

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
    # Set by the lead lookup, never by a directory:
    looked_up: bool = False  # the web was searched for its website (none was listed)
    site: SiteCheck | None = None  # what its website showed
    # Field ("website", "phone", "email", "instagram"…) → where the lookup found it:
    # "web search" or "website". Fields not here came from the directory.
    found_on: dict[str, str] = {}

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

    def website_status(self) -> str:
        """listed | found (by the web search) | down | none (a complete listing shows
        none) | not_found (none listed and none found on the web) | unknown."""
        if self.website:
            if self.site is not None and not self.site.reachable:
                return "down"
            return "found" if "website" in self.found_on else "listed"
        if self.complete_listing:
            return "none"
        return "not_found" if self.looked_up else "unknown"

    @property
    def checked(self) -> bool:
        """Whether its need was looked into beyond the listing."""
        return self.complete_listing or self.looked_up or self.site is not None

    def observed_signals(
        self, *, busy_reviews: int, wanted: frozenset[str] | set[str] = frozenset()
    ) -> list[str]:
        """Signs it needs a digital service that its listing and website show
        (apps.organizations.choices.LeadSignal values). Never a guess: a missing website
        only counts when the source lists websites reliably or a web search found none,
        and a feature only counts as missing when its page was read."""
        if not self.website:
            if self.socials:
                signals = ["social_only"]
            elif self.complete_listing or self.looked_up:
                signals = ["no_website"]
            else:
                signals = []
        elif self.site is None:
            signals = ["no_https"] if self.website.startswith("http://") else []
        else:
            signals = self.site.signals(wanted)
        if self.reviews is not None and self.reviews >= busy_reviews:
            signals.append("many_reviews")
        return signals

    def as_details(
        self, *, signals: list[str], pitch: str = "", evidence: list[dict] | None = None
    ) -> dict:
        """What is stored on the lead (Opportunity.business)."""
        details = self.model_dump(exclude={"source_label"}, exclude_defaults=True)
        details["source_label"] = self.source_label
        details["signals"] = signals
        details["complete_listing"] = self.complete_listing
        details["website_status"] = self.website_status()
        if evidence:
            details["evidence"] = evidence
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
