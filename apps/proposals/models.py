from django.conf import settings
from django.db import models

from apps.common.models import UUIDTimestampedModel
from apps.opportunities.models import Opportunity
from apps.organizations.models import Organization

from .choices import ProposalJob, ProposalStatus, ProposalStep, VersionSource


class Proposal(UUIDTimestampedModel):
    """The application being written for one approved opportunity.

    Its text lives in versions; `version` is the current one. Jobs (a full draft or a
    section rewrite) run in the background (Temporal); `job_started_at` identifies the
    running job so a stale worker can never overwrite a newer one.
    """

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="proposals"
    )
    opportunity = models.OneToOneField(
        Opportunity, on_delete=models.CASCADE, related_name="proposal"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    status = models.CharField(
        max_length=10, choices=ProposalStatus.choices, default=ProposalStatus.QUEUED
    )
    job = models.CharField(max_length=10, choices=ProposalJob.choices, default=ProposalJob.DRAFT)
    job_section = models.CharField(max_length=40, blank=True, default="")
    job_instructions = models.TextField(blank=True, default="")
    job_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    job_queued_at = models.DateTimeField(null=True, blank=True)
    job_started_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=0)  # current version number; 0 = none yet
    requirements = models.JSONField(default=dict, blank=True)  # what the call asks for
    review = models.JSONField(null=True, blank=True)  # the AI's readiness check
    # Tenders: each mandatory requirement against the company's documents, from the
    # AI ([{key, n, code, requirement, kind, status, documents, note}]), plus `done`,
    # which a person ticks when the item is in hand for this bid.
    checklist = models.JSONField(default=list, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)  # last checklist
    warning = models.CharField(max_length=500, blank=True, default="")  # last job was partial
    error = models.CharField(max_length=500, blank=True, default="")  # last job failed
    runner = models.CharField(max_length=30, blank=True, default="")
    prompt_versions = models.JSONField(default=dict, blank=True)
    models_used = models.JSONField(default=dict, blank=True)
    usage = models.JSONField(default=dict, blank=True)
    drafted_at = models.DateTimeField(null=True, blank=True)  # last full draft
    # Which company documents evidence comes from (KnowledgeScope.to_config()); null
    # follows the run that found the opportunity.
    knowledge = models.JSONField(null=True, blank=True)
    last_event_seq = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=ProposalStatus.values),
                name="proposal_valid_status",
            ),
        ]
        indexes = [models.Index(fields=["organization", "-updated_at"], name="proposal_org")]

    def __str__(self):
        return f"Proposal for {self.opportunity_id} (v{self.version}, {self.status})"

    @property
    def is_busy(self):
        return self.status in (ProposalStatus.QUEUED, ProposalStatus.DRAFTING)


class ProposalVersion(UUIDTimestampedModel):
    """An immutable snapshot of the whole text. Every change makes a new version."""

    proposal = models.ForeignKey(Proposal, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    # [{key, title, purpose, key_points, evidence_query, target_words, content (Markdown),
    #   evidence: [{n, document_id, document_title, location, excerpt}],
    #   missing_information, drafted}]
    sections = models.JSONField(default=list)
    source = models.CharField(max_length=12, choices=VersionSource.choices)
    note = models.CharField(max_length=200, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )  # null for AI-written versions

    class Meta:
        ordering = ["proposal", "-number"]
        constraints = [
            models.UniqueConstraint(fields=["proposal", "number"], name="proposal_version_unique"),
        ]

    def __str__(self):
        return f"{self.proposal_id} v{self.number}"


class ProposalEvent(UUIDTimestampedModel):
    """Progress of proposal jobs, for the live view ("Writing “Budget”…")."""

    proposal = models.ForeignKey(Proposal, on_delete=models.CASCADE, related_name="events")
    seq = models.PositiveIntegerField()
    step = models.CharField(max_length=15, choices=ProposalStep.choices)
    level = models.CharField(max_length=10, default="info")
    message = models.CharField(max_length=500)
    tool = models.CharField(max_length=60, blank=True, default="")
    url = models.URLField(max_length=1000, blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField()

    class Meta:
        ordering = ["proposal", "seq"]
        constraints = [
            models.UniqueConstraint(fields=["proposal", "seq"], name="proposal_event_unique_seq"),
        ]

    def __str__(self):
        return f"{self.proposal_id} #{self.seq} {self.message}"
