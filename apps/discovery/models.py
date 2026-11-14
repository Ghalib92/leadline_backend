from django.conf import settings
from django.db import models

from apps.common.models import UUIDTimestampedModel
from apps.organizations.choices import Goal, OpportunityType
from apps.organizations.models import Organization

from .choices import (
    ACTIVE_RUN_STATUSES,
    AgentSchedule,
    CandidateDecision,
    EventLevel,
    KnowledgeMode,
    RunDepth,
    RunStatus,
    RunStep,
    RunTrigger,
)


class SearchAgent(UUIDTimestampedModel):
    """A saved search: what to look for, where, and how often."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="search_agents"
    )
    name = models.CharField(max_length=60)
    description = models.CharField(max_length=280, blank=True, default="")
    # Specific instructions / guidance for this agent's searches and scoring:
    instructions = models.TextField(blank=True, default="")
    # What it looks for; decides which types and sources make sense (GOAL_SCOPES).
    goal = models.CharField(max_length=10, choices=Goal.choices, default=Goal.GRANTS)
    # What this agent adds to the organization's search settings (RunSettings and the
    # preferences' keywords); the organization's own always apply as well.
    opportunity_types = models.JSONField(default=list, blank=True)  # OpportunityType values
    keywords = models.JSONField(default=list, blank=True)
    sources = models.JSONField(default=list, blank=True)  # SourceCategory values
    folders = models.ManyToManyField("organizations.KnowledgeFolder", blank=True, related_name="+")
    depth = models.CharField(max_length=10, choices=RunDepth.choices, blank=True, default="")
    # Websites this agent also reads, on top of the organization's sources:
    # [{"url", "label", "notes", "enabled"}].
    links = models.JSONField(default=list, blank=True)
    schedule = models.CharField(
        max_length=10, choices=AgentSchedule.choices, default=AgentSchedule.MANUAL
    )
    enabled = models.BooleanField(default=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="search_agents",
    )

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["organization", "enabled"], name="discovery_agent_org")]

    def __str__(self):
        return self.name


def default_run_sources():
    return ["web", "grants", "procurement"]


def default_run_types():
    return list(OpportunityType.values)


class RunSettings(UUIDTimestampedModel):
    """The organization's defaults for "Run with my settings"."""

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="run_settings"
    )
    depth = models.CharField(max_length=10, choices=RunDepth.choices, default=RunDepth.STANDARD)
    sources = models.JSONField(default=default_run_sources)  # SourceCategory values
    opportunity_types = models.JSONField(default=default_run_types)  # OpportunityType values
    # Empty means every folder.
    folders = models.ManyToManyField("organizations.KnowledgeFolder", blank=True, related_name="+")
    knowledge_mode = models.CharField(
        max_length=10, choices=KnowledgeMode.choices, default=KnowledgeMode.ALL
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    def __str__(self):
        return f"Run settings of {self.organization_id}"


class DiscoveryRun(UUIDTimestampedModel):
    """One execution of a search agent. `created_at` is when it was queued."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="discovery_runs"
    )
    # Runs outlive their agent; the name is kept for the history.
    agent = models.ForeignKey(
        SearchAgent, on_delete=models.SET_NULL, null=True, related_name="runs"
    )
    agent_name = models.CharField(max_length=60)
    trigger = models.CharField(max_length=10, choices=RunTrigger.choices)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    status = models.CharField(max_length=10, choices=RunStatus.choices, default=RunStatus.QUEUED)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    # What the agent asked for, frozen at queue time so edits don't change a run.
    config = models.JSONField(default=dict)
    budget = models.JSONField(default=dict)  # limits, from apps/ai/config/sources.yaml
    usage = models.JSONField(default=dict)  # searches, pages, tokens actually used
    sources_scanned = models.PositiveIntegerField(default=0)
    pages_read = models.PositiveIntegerField(default=0)
    found = models.PositiveIntegerField(default=0)  # candidates added to the feed
    # Recorded on every run so results stay comparable across prompt and model changes.
    runner = models.CharField(max_length=30, blank=True, default="")
    prompt_versions = models.JSONField(default=dict, blank=True)  # role → "judge/v1"
    models_used = models.JSONField(default=dict, blank=True)  # role → model id
    error = models.CharField(max_length=500, blank=True, default="")
    last_event_seq = models.PositiveIntegerField(default=0)
    schedule_slot = models.CharField(  # noqa: DJ001
        max_length=120, null=True, blank=True, default=None
    )
    # Handing the run to the job backend (apps.discovery.jobs). A queued run with no
    # dispatched_at was never handed over; the recovery sweep tries again.
    dispatched_at = models.DateTimeField(null=True, blank=True)
    dispatch_attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # One run at a time per organization: runs are expensive and share budgets.
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(status__in=ACTIVE_RUN_STATUSES),
                name="discovery_one_active_run_per_org",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=RunStatus.values),
                name="discovery_run_valid_status",
            ),
            models.UniqueConstraint(
                fields=["schedule_slot"],
                condition=models.Q(schedule_slot__isnull=False),
                name="discovery_run_unique_schedule_slot",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="discovery_run_history"),
            models.Index(fields=["agent", "-created_at"], name="discovery_run_agent"),
            models.Index(fields=["schedule_slot"], name="discovery_run_schedule_slot"),
        ]

    def __str__(self):
        return f"{self.agent_name} ({self.status})"

    @property
    def is_active(self):
        return self.status in ACTIVE_RUN_STATUSES


class RunEvent(UUIDTimestampedModel):
    """One line of a run's timeline ("Searching grants.gov for…")."""

    run = models.ForeignKey(DiscoveryRun, on_delete=models.CASCADE, related_name="events")
    seq = models.PositiveIntegerField()  # 1, 2, 3… per run; clients poll with ?after=
    step = models.CharField(max_length=10, choices=RunStep.choices)
    level = models.CharField(max_length=10, choices=EventLevel.choices, default=EventLevel.INFO)
    message = models.CharField(max_length=500)
    tool = models.CharField(max_length=60, blank=True, default="")
    source = models.CharField(max_length=120, blank=True, default="")  # e.g. "grants.gov"
    url = models.URLField(max_length=1000, blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField()

    class Meta:
        ordering = ["run", "seq"]
        constraints = [
            models.UniqueConstraint(fields=["run", "seq"], name="discovery_event_unique_seq"),
        ]

    def __str__(self):
        return f"{self.run_id} #{self.seq} {self.message}"


class RunCandidate(UUIDTimestampedModel):
    """Every opportunity a run scored, including the ones it did not add."""

    run = models.ForeignKey(DiscoveryRun, on_delete=models.CASCADE, related_name="candidates")
    title = models.CharField(max_length=300)
    funder = models.CharField(max_length=200, blank=True, default="")
    source_name = models.CharField(max_length=120, blank=True, default="")
    source_url = models.URLField(max_length=1000, blank=True, default="")
    opportunity_type = models.CharField(max_length=20)
    match_score = models.PositiveSmallIntegerField()
    match_reasons = models.JSONField(default=list, blank=True)
    decision = models.CharField(max_length=20, choices=CandidateDecision.choices)
    opportunity = models.ForeignKey(
        "opportunities.Opportunity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        ordering = ["run", "-match_score", "created_at"]

    def __str__(self):
        return f"{self.title} ({self.match_score}, {self.decision})"
