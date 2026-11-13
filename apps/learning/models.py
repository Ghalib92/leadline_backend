from django.conf import settings
from django.db import models

from apps.common.models import UUIDTimestampedModel
from apps.organizations.models import Organization

from .choices import EvalSuite, SuggestionKind, SuggestionStatus


class PreferenceSuggestion(UUIDTimestampedModel):
    """A change to the organization's preferences, suggested from repeated rejections.

    Nothing changes until an admin accepts it. A dismissed suggestion is never made
    again for the same value.
    """

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="preference_suggestions"
    )
    kind = models.CharField(max_length=20, choices=SuggestionKind.choices)
    value = models.CharField(max_length=200)  # a funder, a keyword, or an amount
    value_key = models.CharField(max_length=200)  # normalised, for uniqueness
    currency = models.CharField(max_length=3, blank=True, default="")  # amount kinds only
    rationale = models.CharField(max_length=500)
    evidence = models.JSONField(default=list)  # ids of the reviews behind it
    status = models.CharField(
        max_length=10, choices=SuggestionStatus.choices, default=SuggestionStatus.PENDING
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "kind", "value_key"],
                name="learning_suggestion_unique_value",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=SuggestionStatus.values),
                name="learning_suggestion_valid_status",
            ),
        ]

    def __str__(self):
        return f"{self.kind}: {self.value} ({self.status})"


class EvalRun(UUIDTimestampedModel):
    """One `run_evals` measurement: which prompts and models, on which cases, scored how.

    Compare runs of the same suite and dataset to see whether a prompt or model change
    helped.
    """

    suite = models.CharField(max_length=12, choices=EvalSuite.choices)
    dataset = models.CharField(max_length=200)  # e.g. "reviews:<org id>" or "golden:demo"
    organization = models.ForeignKey(
        Organization, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    label = models.CharField(max_length=200, blank=True, default="")
    prompt_versions = models.JSONField(default=dict)
    models_used = models.JSONField(default=dict)
    cases = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)  # cases the model failed to answer
    metrics = models.JSONField(default=dict)
    results = models.JSONField(default=list)  # per case: id, expected, got, ok
    usage = models.JSONField(default=dict)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["suite", "dataset", "-created_at"], name="eval_history")]

    def __str__(self):
        return f"{self.suite} on {self.dataset} ({self.created_at:%Y-%m-%d %H:%M})"
