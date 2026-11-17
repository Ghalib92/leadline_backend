from django.conf import settings
from django.db import models
from django.utils import timezone
from pgvector.django import VectorField

from apps.common.models import UUIDTimestampedModel
from apps.organizations.choices import OpportunityType
from apps.organizations.models import Organization

from .choices import OpportunityStatus, RejectionReason, ReviewDecision


class Opportunity(UUIDTimestampedModel):
    """A grant, tender, partnership… found for an organization and kept in its feed."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="opportunities"
    )
    # Who found it. Both survive deletion of the agent or run as null.
    agent = models.ForeignKey(
        "discovery.SearchAgent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opportunities",
    )
    run = models.ForeignKey(
        "discovery.DiscoveryRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="opportunities",
    )
    opportunity_type = models.CharField(max_length=20, choices=OpportunityType.choices)
    title = models.CharField(max_length=300)
    funder = models.CharField(max_length=200, blank=True, default="")
    summary = models.TextField(blank=True, default="")
    eligibility = models.TextField(blank=True, default="")
    amount_min = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    amount_max = models.DecimalField(max_digits=16, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default="")
    deadline = models.DateTimeField(null=True, blank=True)
    location = models.CharField(max_length=200, blank=True, default="")
    source_url = models.URLField(max_length=1000)
    source_name = models.CharField(max_length=120, blank=True, default="")
    match_score = models.PositiveSmallIntegerField()  # 0–100, from the judge
    match_reasons = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=12, choices=OpportunityStatus.choices, default=OpportunityStatus.NEW
    )
    saved = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")
    found_at = models.DateTimeField(default=timezone.now)
    # Facts the verifier extracted (requirements, documents needed, contacts…) and how.
    requirements = models.JSONField(default=dict, blank=True)
    verification = models.JSONField(default=dict, blank=True)
    # Client leads only: the business's contact and listing facts (name, category,
    # address, phone, website, rating, signs it needs the organization…).
    business = models.JSONField(default=dict, blank=True)
    # Tenders from structured portals (tenders.go.ke): reference, buyer, category,
    # closing time, fee, bid security, AGPO reservation, documents, buyer's contacts.
    tender = models.JSONField(default=dict, blank=True)
    # Deduplication across runs (apps/opportunities/dedupe.py): the same listing,
    # by normalised URL or by title and funder, is never added twice.
    dedupe_key = models.CharField(max_length=64)
    title_key = models.CharField(max_length=64)

    class Meta:
        ordering = ["-found_at", "-id"]
        verbose_name_plural = "opportunities"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "dedupe_key"], name="opportunity_unique_per_org"
            ),
            models.CheckConstraint(
                condition=models.Q(match_score__lte=100), name="opportunity_score_range"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=OpportunityStatus.values),
                name="opportunity_valid_status",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_min__isnull=True)
                | models.Q(amount_max__isnull=True)
                | models.Q(amount_min__lte=models.F("amount_max")),
                name="opportunity_amount_range",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "-found_at"], name="opportunity_feed"),
            models.Index(fields=["organization", "status"], name="opportunity_status"),
            models.Index(fields=["organization", "deadline"], name="opportunity_deadline"),
            models.Index(fields=["organization", "title_key"], name="opportunity_title_key"),
        ]

    def __str__(self):
        return self.title


class OpportunityReview(UUIDTimestampedModel):
    """A person's approve or reject decision, kept as history.

    The newest review is the current one. Each stores what the reviewer saw (a
    snapshot) and which judge scored it, so the learning loop and evals can compare
    the AI's score with the human decision even after the opportunity changes.
    """

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="opportunity_reviews"
    )
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, related_name="reviews")
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="opportunity_reviews",
    )
    decision = models.CharField(max_length=10, choices=ReviewDecision.choices)
    reason = models.CharField(
        max_length=20, choices=RejectionReason.choices, blank=True, default=""
    )
    comment = models.TextField(blank=True, default="")
    status_before = models.CharField(max_length=12, choices=OpportunityStatus.choices)
    match_score = models.PositiveSmallIntegerField()  # the AI's score when reviewed
    snapshot = models.JSONField(default=dict)  # the opportunity as the reviewer saw it
    # The judge behind match_score (from the run), e.g. "judge/v1" and the model id.
    judge_prompt = models.CharField(max_length=60, blank=True, default="")
    judge_model = models.CharField(max_length=120, blank=True, default="")
    # Embedding of the snapshot (decision memory): the judge is shown this organization's
    # most similar past decisions. Filled in by a background job after the review.
    embedding = VectorField(
        dimensions=settings.KNOWLEDGE_EMBEDDING_DIMENSIONS, null=True, blank=True
    )

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(decision__in=ReviewDecision.values),
                name="opportunity_review_valid_decision",
            ),
            # Rejections need a reason; approvals have none.
            models.CheckConstraint(
                condition=(
                    models.Q(decision="approve", reason="")
                    | models.Q(decision="reject", reason__in=RejectionReason.values)
                ),
                name="opportunity_review_reason_matches_decision",
            ),
        ]
        indexes = [
            models.Index(fields=["opportunity", "-created_at"], name="opportunity_review_latest"),
            models.Index(
                fields=["organization", "decision", "-created_at"], name="opportunity_review_org"
            ),
        ]

    def __str__(self):
        return f"{self.decision} {self.opportunity_id}"
