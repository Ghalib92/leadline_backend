"""Load apps/ai/config/models.yaml and resolve agent roles to concrete model settings."""

from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from apps.ai.exceptions import AIConfigError

MODELS_FILE = Path(__file__).with_name("models.yaml")
SOURCES_FILE = Path(__file__).with_name("sources.yaml")
PROPOSALS_FILE = Path(__file__).with_name("proposals.yaml")
OUTREACH_FILE = Path(__file__).with_name("outreach.yaml")


class ModelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    max_retries: int | None = Field(default=None, ge=0)
    # How structured outputs are requested: "function_calling" (tool call) or
    # "json_schema" (the provider's JSON-schema response format).
    structured_output: Literal["function_calling", "json_schema", "json_mode"] | None = None


class RoleSpec(ModelParams):
    tier: str
    description: str = ""


class ModelsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    tiers: list[str] = Field(min_length=1)
    defaults: ModelParams = ModelParams()
    roles: dict[str, RoleSpec]

    @model_validator(mode="after")
    def roles_use_declared_tiers(self):
        unknown = {
            name: role.tier for name, role in self.roles.items() if role.tier not in self.tiers
        }
        if unknown:
            raise ValueError(f"roles use undeclared tiers: {unknown}")
        return self


class ResolvedModel(ModelParams):
    """Everything needed to build one chat model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    tier: str
    provider: str
    model: str


@cache
def load_models_file(path: Path = MODELS_FILE) -> ModelsFile:
    try:
        raw = yaml.safe_load(path.read_text())
        return ModelsFile.model_validate(raw)
    except FileNotFoundError as exc:
        raise AIConfigError(f"{path} does not exist.") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise AIConfigError(f"{path} is invalid: {exc}") from exc


def resolve_role(role: str) -> ResolvedModel:
    models = load_models_file()
    spec = models.roles.get(role)
    if spec is None:
        known = ", ".join(sorted(models.roles))
        raise AIConfigError(f"Unknown AI role {role!r}. Known roles: {known}.")
    return _resolve(role=role, tier=spec.tier, overrides=spec, defaults=models.defaults)


def resolve_tier(tier: str) -> ResolvedModel:
    """A tier with only the defaults applied; used by diagnostics such as ai_ping."""
    models = load_models_file()
    if tier not in models.tiers:
        raise AIConfigError(f"Unknown AI tier {tier!r}. Known tiers: {', '.join(models.tiers)}.")
    return _resolve(role=f"tier:{tier}", tier=tier, overrides=None, defaults=models.defaults)


def _resolve(*, role, tier, overrides, defaults) -> ResolvedModel:
    model = settings.AI_MODEL_TIERS.get(tier, "")
    if not model:
        raise AIConfigError(
            f"No model configured for the {tier!r} tier. Set {tier.upper()}_MODEL in .env."
        )
    params = defaults.model_dump(exclude_none=True)
    if overrides is not None:
        params.update(overrides.model_dump(exclude_none=True, exclude={"tier", "description"}))
    return ResolvedModel(
        role=role, tier=tier, provider=settings.AI_LLM_PROVIDER, model=model, **params
    )


# ---------------------------------------------------------------------------
# sources.yaml: discovery budgets, match threshold and source categories
# ---------------------------------------------------------------------------


class RunBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_searches: int = Field(ge=0)
    max_pages: int = Field(ge=0)
    max_tokens: int = Field(gt=0)
    max_wall_seconds: int = Field(gt=0)


class DepthPreset(RunBudget):
    label: str
    description: str = ""
    max_rounds: int = Field(ge=1, le=5)


DEPTHS = ("quick", "standard", "deep")


class DiscoverySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_threshold: int = Field(ge=0, le=100)
    default_depth: str = "standard"
    depths: dict[str, DepthPreset]
    target_results: int = Field(default=5, ge=1)
    results_per_search: int = Field(default=8, ge=1, le=20)
    max_leads_per_round: int = Field(default=15, ge=1)
    max_page_chars: int = Field(default=12000, ge=1000)
    max_concurrency: int = Field(default=4, ge=1, le=16)
    # Similar past decisions shown to the judge (judge/v2 and later).
    decision_memory_size: int = Field(default=4, ge=0, le=10)

    @model_validator(mode="after")
    def every_depth_is_defined(self):
        missing = sorted(set(DEPTHS) - set(self.depths))
        if missing:
            raise ValueError(f"discovery.depths is missing {missing}")
        if self.default_depth not in self.depths:
            raise ValueError(f"discovery.default_depth {self.default_depth!r} is not a depth")
        return self

    def budget_for(self, depth: str) -> dict:
        """The hard limits and rounds a run of this depth gets (frozen on the run)."""
        return self.depths[depth].model_dump(exclude={"label", "description"})

    @property
    def budget(self) -> RunBudget:
        """The default depth's limits."""
        preset = self.depths[self.default_depth]
        return RunBudget(**preset.model_dump(include=set(RunBudget.model_fields)))

    @property
    def max_rounds(self) -> int:
        return self.depths[self.default_depth].max_rounds


class ClientsSettings(BaseModel):
    """Client (business) searches: how many businesses each search may return and how
    they are scored. Budgets come from the run's depth, like any discovery run."""

    model_config = ConfigDict(extra="forbid")

    # Leads scoring below this (0-100) are not added. Lower than opportunities': a lead
    # list is ranked for people to work through, and directory listings say little.
    match_threshold: int = Field(default=40, ge=0, le=100)
    results_per_search: int = Field(default=20, ge=1, le=100)
    # Businesses scored by the client judge in one model call.
    judge_batch_size: int = Field(default=8, ge=1, le=20)
    # At most this many businesses are scored per run (the best-listed first).
    max_candidates: int = Field(default=40, ge=1, le=200)
    # Places searched per run: the brief's cities (else the profile's city), at most this
    # many, so a long list of cities doesn't spend the whole budget on one kind of business.
    max_places: int = Field(default=3, ge=1, le=10)
    # A business with at least this many reviews counts as busy (signal many_reviews).
    busy_reviews: int = Field(default=50, ge=1)


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adapter: str
    domain: str
    covers: str = ""
    enabled: bool = True
    options: dict = {}


class SourceCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    sources: list[str] = []


class SourcesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    discovery: DiscoverySettings
    sources: dict[str, SourceSpec] = {}
    source_categories: dict[str, SourceCategory] = Field(min_length=1)
    clients: ClientsSettings = ClientsSettings()

    @model_validator(mode="after")
    def categories_use_declared_sources(self):
        unknown = {
            name: missing
            for name, category in self.source_categories.items()
            if (missing := sorted(set(category.sources) - set(self.sources)))
        }
        if unknown:
            raise ValueError(f"source_categories use undeclared sources: {unknown}")
        return self

    def sources_for(self, categories) -> list[str]:
        """Enabled sources of the given categories, in order, without repeats."""
        names = []
        for category in categories:
            for name in self.source_categories.get(category, SourceCategory(label="")).sources:
                if self.sources[name].enabled and name not in names:
                    names.append(name)
        return names


@cache
def load_sources_file(path: Path = SOURCES_FILE) -> SourcesFile:
    try:
        raw = yaml.safe_load(path.read_text())
        return SourcesFile.model_validate(raw)
    except FileNotFoundError as exc:
        raise AIConfigError(f"{path} does not exist.") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise AIConfigError(f"{path} is invalid: {exc}") from exc


# ---------------------------------------------------------------------------
# proposals.yaml: Proposal Writer limits
# ---------------------------------------------------------------------------


class ProposalSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    budget: RunBudget
    max_sections: int = Field(ge=1, le=20)
    evidence_per_section: int = Field(ge=0, le=10)
    max_call_chars: int = Field(ge=1000)
    max_concurrency: int = Field(ge=1, le=8)
    default_section_words: int = Field(ge=50, le=3000)


@cache
def load_proposals_file(path: Path = PROPOSALS_FILE) -> ProposalSettings:
    try:
        return ProposalSettings.model_validate(yaml.safe_load(path.read_text()))
    except FileNotFoundError as exc:
        raise AIConfigError(f"{path} does not exist.") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise AIConfigError(f"{path} is invalid: {exc}") from exc


# ---------------------------------------------------------------------------
# outreach.yaml: lead scout and email agent limits
# ---------------------------------------------------------------------------


class LeadScoutSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget: RunBudget
    max_contacts: int = Field(ge=1, le=30)
    contact_page_words: list[str] = Field(min_length=1)
    max_linked_pages: int = Field(ge=0, le=10)
    max_page_chars: int = Field(ge=1000)


class EmailerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget: RunBudget


class OutreachSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    lead_scout: LeadScoutSettings
    emailer: EmailerSettings


@cache
def load_outreach_file(path: Path = OUTREACH_FILE) -> OutreachSettings:
    try:
        return OutreachSettings.model_validate(yaml.safe_load(path.read_text()))
    except FileNotFoundError as exc:
        raise AIConfigError(f"{path} does not exist.") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise AIConfigError(f"{path} is invalid: {exc}") from exc
