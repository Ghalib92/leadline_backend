from django.conf import settings
from django.db import models
from django.db.models.functions import Lower

from apps.common.models import UUIDTimestampedModel
from apps.opportunities.models import Opportunity
from apps.organizations.models import Organization

from .choices import ContactKind, ContactSource, EmailPurpose, EmailStatus, SearchStatus


class Contact(UUIDTimestampedModel):
    """A funder's business contact for one opportunity. Personal data: kept only with
    where it came from, and deletable at any time."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="contacts"
    )
    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, related_name="contacts")
    name = models.CharField(max_length=150, blank=True, default="")
    role = models.CharField(max_length=150, blank=True, default="")
    email = models.EmailField(max_length=254, blank=True, default="")
    phone = models.CharField(max_length=40, blank=True, default="")
    kind = models.CharField(max_length=10, choices=ContactKind.choices, default=ContactKind.PERSON)
    relevance = models.CharField(max_length=300, blank=True, default="")
    confidence = models.PositiveSmallIntegerField(default=50)
    source = models.CharField(max_length=10, choices=ContactSource.choices)
    source_url = models.URLField(max_length=1000, blank=True, default="")
    verified = models.BooleanField(default=False)  # the email was checked (Hunter)
    do_not_contact = models.BooleanField(default=False)
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )  # null when found by the lead scout

    class Meta:
        ordering = ["-confidence", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                "opportunity",
                Lower("email"),
                condition=~models.Q(email=""),
                name="outreach_contact_unique_email",
            ),
        ]

    def __str__(self):
        return self.name or self.email or self.phone


class ContactSearch(UUIDTimestampedModel):
    """One run of the lead scout for an opportunity."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    opportunity = models.ForeignKey(
        Opportunity, on_delete=models.CASCADE, related_name="contact_searches"
    )
    status = models.CharField(
        max_length=10, choices=SearchStatus.choices, default=SearchStatus.QUEUED
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    found = models.PositiveIntegerField(default=0)  # new contacts saved
    warning = models.CharField(max_length=500, blank=True, default="")
    error = models.CharField(max_length=500, blank=True, default="")
    prompt_versions = models.JSONField(default=dict, blank=True)
    models_used = models.JSONField(default=dict, blank=True)
    usage = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["opportunity"],
                condition=models.Q(status__in=["queued", "running"]),
                name="outreach_one_active_contact_search",
            ),
        ]


class OutreachEmail(UUIDTimestampedModel):
    """An email to a funder's contact. Drafted by the AI or a person, sent only after
    an admin approves it."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="outreach_emails"
    )
    opportunity = models.ForeignKey(
        Opportunity, on_delete=models.CASCADE, related_name="outreach_emails"
    )
    contact = models.ForeignKey(
        Contact, on_delete=models.SET_NULL, null=True, related_name="emails"
    )
    # Kept even if the contact is deleted, as the record of what was sent.
    to_email = models.EmailField(max_length=254)
    to_name = models.CharField(max_length=150, blank=True, default="")
    purpose = models.CharField(max_length=20, choices=EmailPurpose.choices)
    instructions = models.TextField(blank=True, default="")
    subject = models.CharField(max_length=200, blank=True, default="")
    body = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=10, choices=EmailStatus.choices, default=EmailStatus.DRAFTING
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    reply_to = models.EmailField(max_length=254, blank=True, default="")
    error = models.CharField(max_length=500, blank=True, default="")
    prompt_id = models.CharField(max_length=60, blank=True, default="")
    model = models.CharField(max_length=120, blank=True, default="")
    drafting_started_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=EmailStatus.values),
                name="outreach_email_valid_status",
            ),
            # Sent emails always record who approved them.
            models.CheckConstraint(
                condition=~models.Q(status="sent") | models.Q(approved_by__isnull=False),
                name="outreach_sent_needs_approval",
            ),
        ]
        indexes = [models.Index(fields=["organization", "status"], name="outreach_queue")]

    def __str__(self):
        return f"{self.subject or '(drafting)'} → {self.to_email} ({self.status})"


class ReminderPreference(UUIDTimestampedModel):
    """A member's deadline reminders for one organization (on by default)."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    enabled = models.BooleanField(default=True)
    days_before = models.JSONField(default=list)  # e.g. [14, 3, 1]

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="reminder_pref_unique"),
        ]


class ReminderLog(UUIDTimestampedModel):
    """A sent deadline reminder, so each is sent once (per deadline date: if the
    deadline moves, reminders for the new date go out again)."""

    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    deadline = models.DateField()
    days_before = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["opportunity", "user", "deadline", "days_before"],
                name="reminder_log_unique",
            ),
        ]
