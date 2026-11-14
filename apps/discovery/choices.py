"""Controlled vocabularies for search agents and discovery runs.

Step keys, levels and decisions match the frontend's run schema
(leadline-ui/app/lib/schema/agent-run.schema.ts).
"""

from dataclasses import dataclass

from django.db import models

from apps.organizations.choices import Goal, OpportunityType


class SourceCategory(models.TextChoices):
    """Where an agent searches; apps/ai/config/sources.yaml maps each to adapters."""

    WEB = "web", "Open web"
    GRANTS = "grants", "Grant databases"
    PROCUREMENT = "procurement", "Procurement portals"
    NEWS = "news", "News & announcements"


@dataclass(frozen=True)
class GoalScope:
    """What an agent with this goal may search: the opportunity types and source
    categories that make sense for it. `runnable` is False until the goal's sources
    exist; its agents can be set up but not run."""

    description: str
    opportunity_types: tuple[str, ...]
    sources: tuple[str, ...]
    runnable: bool = True


TENDER_TYPES = (OpportunityType.TENDER, OpportunityType.RFP, OpportunityType.CONSULTANCY)

GOAL_SCOPES: dict[str, GoalScope] = {
    Goal.CLIENTS: GoalScope(
        description="Businesses that could buy what you sell, with a reason they need it.",
        opportunity_types=(),
        sources=(),
        runnable=False,  # Phase 2 adds the business sources (maps, websites, social)
    ),
    Goal.TENDERS: GoalScope(
        description="Tenders, RFPs and consultancies to bid on.",
        opportunity_types=TENDER_TYPES,
        sources=(SourceCategory.WEB, SourceCategory.PROCUREMENT, SourceCategory.NEWS),
    ),
    Goal.GRANTS: GoalScope(
        description="Grants, fellowships, awards, investment, partnerships and programmes.",
        opportunity_types=tuple(t for t in OpportunityType.values if t not in TENDER_TYPES),
        sources=(SourceCategory.WEB, SourceCategory.GRANTS, SourceCategory.NEWS),
    ),
}


class KnowledgeMode(models.TextChoices):
    """Which company documents a run may read (the org's search settings)."""

    ALL = "all", "All documents"
    SELECTED = "selected", "Only selected folders"
    NONE = "none", "No documents"


class RunDepth(models.TextChoices):
    """How thorough a run is; apps/ai/config/sources.yaml sets each depth's limits."""

    QUICK = "quick", "Quick"
    STANDARD = "standard", "Standard"
    DEEP = "deep", "Deep"


class AgentSchedule(models.TextChoices):
    MANUAL = "manual", "Manual"
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"


class RunTrigger(models.TextChoices):
    MANUAL = "manual", "Manual"
    SCHEDULE = "schedule", "Schedule"


class RunStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    # Finished early (budget or wall time reached, some sources failed) with results.
    PARTIAL = "partial", "Completed with warnings"
    FAILED = "failed", "Failed"


ACTIVE_RUN_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING)


class RunStep(models.TextChoices):
    PROFILE = "profile", "Read the organization profile"
    SEARCH = "search", "Search sources"
    READ = "read", "Read opportunity pages"
    MATCH = "match", "Score the matches"
    SAVE = "save", "Save to the feed"


class EventLevel(models.TextChoices):
    INFO = "info", "Info"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class CandidateDecision(models.TextChoices):
    ADDED = "added", "Added"
    DUPLICATE = "duplicate", "Already in feed"
    BELOW_THRESHOLD = "below_threshold", "Below threshold"


class SkipReason(models.TextChoices):
    """Why a listing was dropped after reading (RunEvent.data["skip"]); counted in the
    run's outcome so the UI can explain a run that added nothing."""

    CLOSED = "closed", "Closed"
    NOT_AN_OPPORTUNITY = "not_an_opportunity", "Not an opportunity"
    WRONG_TYPE = "wrong_type", "Not a type you asked for"
    EXCLUDED = "excluded", "Excluded by your preferences"
    UNREADABLE = "unreadable", "Could not be read"
