import os

import shortuuid
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.text import slugify

from apps.common.models import UUIDTimestampedModel

from .choices import (
    BudgetRange,
    DocumentCategory,
    LinkKind,
    OrganizationType,
    ProcessingStatus,
    TeamSize,
)


class Organization(UUIDTimestampedModel):
    # Identity
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=240, unique=True, editable=False)
    legal_name = models.CharField(max_length=255, blank=True, default="")
    organization_type = models.CharField(
        max_length=30, choices=OrganizationType.choices, blank=True, default=""
    )
    registration_number = models.CharField(max_length=100, blank=True, default="")
    year_founded = models.PositiveSmallIntegerField(null=True, blank=True)
    team_size = models.CharField(max_length=10, choices=TeamSize.choices, blank=True, default="")
    annual_budget = models.CharField(
        max_length=10, choices=BudgetRange.choices, blank=True, default=""
    )
    # Contact and location. `country` is the ISO 3166-1 alpha-2 headquarters country.
    website = models.URLField(max_length=500, blank=True, default="")
    contact_email = models.EmailField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=30, blank=True, default="")
    country = models.CharField(max_length=2, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    address = models.CharField(max_length=500, blank=True, default="")
    # What the organization does: the context opportunity-matching agents read.
    description = models.TextField(blank=True, default="")
    mission = models.TextField(blank=True, default="")
    vision = models.TextField(blank=True, default="")
    offerings = models.TextField(blank=True, default="")  # programs, products and services
    target_beneficiaries = models.TextField(blank=True, default="")
    track_record = models.TextField(blank=True, default="")  # achievements and impact
    past_funding = models.TextField(blank=True, default="")  # previous funders and partners
    industry = models.CharField(max_length=100, blank=True, default="")
    sectors = models.JSONField(default=list, blank=True)
    sdgs = models.JSONField(default=list, blank=True)  # UN Sustainable Development Goals, 1–17
    operating_countries = models.JSONField(default=list, blank=True)  # ISO alpha-2 codes
    languages = models.JSONField(default=list, blank=True)  # ISO 639-1 codes
    # What it wants to find (Goal values); empty until onboarding asks.
    goals = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_organizations",
    )
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name", "id"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = f"{slugify(self.name)[:200] or 'organization'}-{self.pk}"
        super().save(*args, **kwargs)

    @property
    def is_archived(self):
        return self.archived_at is not None


class OrganizationMembership(UUIDTimestampedModel):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        REMOVED = "removed", "Removed"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="organization_memberships",
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    joined_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["joined_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="org_unique_member"),
            models.CheckConstraint(
                condition=models.Q(role__in=["admin", "member"]), name="org_member_valid_role"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["active", "inactive", "removed"]),
                name="org_member_valid_status",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status", "role"], name="org_member_access")
        ]

    def __str__(self):
        return f"{self.user_id} in {self.organization_id} ({self.role}, {self.status})"


class OrganizationInvitation(UUIDTimestampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    class EmailStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="invitations"
    )
    email = models.EmailField(max_length=255)
    role = models.CharField(
        max_length=10,
        choices=OrganizationMembership.Role.choices,
        default=OrganizationMembership.Role.MEMBER,
    )
    message = models.TextField(blank=True, default="")
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="sent_organization_invitations",
    )
    # The raw 256-bit random token exists only in the email and the delivery callback.
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_organization_invitations",
    )
    email_status = models.CharField(
        max_length=10, choices=EmailStatus.choices, default=EmailStatus.PENDING
    )
    last_email_attempt_at = models.DateTimeField(null=True, blank=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                "organization",
                condition=models.Q(status="pending"),
                name="org_unique_pending_invite",
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=["admin", "member"]), name="org_invite_valid_role"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    status__in=["pending", "accepted", "declined", "revoked", "expired"]
                ),
                name="org_invite_valid_status",
            ),
            models.CheckConstraint(
                condition=models.Q(email_status__in=["pending", "sent", "failed"]),
                name="org_invite_valid_email_status",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"], name="org_invite_status"),
            models.Index(fields=["email", "status"], name="org_invite_recipient"),
        ]

    def __str__(self):
        return f"{self.email} → {self.organization_id} ({self.effective_status})"

    @property
    def effective_status(self):
        if self.status == self.Status.PENDING and self.expires_at <= timezone.now():
            return self.Status.EXPIRED
        return self.status


class OrganizationEvent(UUIDTimestampedModel):
    """Service-written history; never expose invitation tokens or credentials here."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=80)
    # null = the event has no target; an ID reference, not free text.
    target_id = models.CharField(max_length=12, null=True, blank=True, editable=False)  # noqa: DJ001
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["organization", "-created_at"], name="org_event_timeline")]

    def __str__(self):
        return f"{self.organization_id}: {self.action}"


class OpportunityPreferences(UUIDTimestampedModel):
    """What the organization wants to pursue: the brief for opportunity discovery."""

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="opportunity_preferences"
    )
    opportunity_types = models.JSONField(default=list, blank=True)
    geographic_scopes = models.JSONField(default=list, blank=True)
    target_countries = models.JSONField(default=list, blank=True)  # ISO alpha-2 codes
    target_regions = models.JSONField(default=list, blank=True)
    focus_areas = models.JSONField(default=list, blank=True)
    keywords = models.JSONField(default=list, blank=True)
    excluded_keywords = models.JSONField(default=list, blank=True)
    preferred_funders = models.JSONField(default=list, blank=True)
    excluded_funders = models.JSONField(default=list, blank=True)
    eligibility = models.JSONField(default=list, blank=True)
    min_funding_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    max_funding_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    funding_currency = models.CharField(max_length=3, default="USD")
    open_to_partnerships = models.BooleanField(default=True)
    can_provide_cofunding = models.BooleanField(default=False)
    eligibility_notes = models.TextField(blank=True, default="")
    additional_context = models.TextField(blank=True, default="")

    class Meta:
        verbose_name_plural = "opportunity preferences"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(min_funding_amount__isnull=True)
                | models.Q(max_funding_amount__isnull=True)
                | models.Q(min_funding_amount__lte=models.F("max_funding_amount")),
                name="org_prefs_funding_range",
            ),
        ]

    def __str__(self):
        return f"Preferences for {self.organization_id}"


class ClientBrief(UUIDTimestampedModel):
    """What the organization sells and who it sells to: the brief for Clients agents."""

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="client_brief"
    )
    # What it sells: [{"name", "description", "price"}]; price is free text ("from KES 40k").
    services = models.JSONField(default=list, blank=True)
    customer_types = models.JSONField(default=list, blank=True)  # BusinessCategory values
    customer_keywords = models.JSONField(default=list, blank=True)  # e.g. "boutique hotels"
    customer_sizes = models.JSONField(default=list, blank=True)  # BusinessSize values
    target_countries = models.JSONField(default=list, blank=True)  # ISO alpha-2 codes
    target_cities = models.JSONField(default=list, blank=True)
    signals = models.JSONField(default=list, blank=True)  # LeadSignal values
    excluded_keywords = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True, default="")

    def __str__(self):
        return f"Clients brief for {self.organization_id}"


class TenderBrief(UUIDTimestampedModel):
    """What the organization bids for: the brief for Tenders agents."""

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="tender_brief"
    )
    categories = models.JSONField(default=list, blank=True)  # TenderCategory values
    keywords = models.JSONField(default=list, blank=True)
    excluded_keywords = models.JSONField(default=list, blank=True)
    buyer_types = models.JSONField(default=list, blank=True)  # BuyerType values
    target_countries = models.JSONField(default=list, blank=True)  # ISO alpha-2 codes
    min_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    max_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    eligibility = models.JSONField(default=list, blank=True)  # TenderEligibility values
    certifications = models.JSONField(default=list, blank=True)  # e.g. "ISO 9001", "NCA 4"
    capacity_notes = models.TextField(blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(min_value__isnull=True)
                | models.Q(max_value__isnull=True)
                | models.Q(min_value__lte=models.F("max_value")),
                name="org_tender_brief_value_range",
            ),
        ]

    def __str__(self):
        return f"Tenders brief for {self.organization_id}"


class OrganizationLink(UUIDTimestampedModel):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="links")
    kind = models.CharField(max_length=20, choices=LinkKind.choices, default=LinkKind.WEBSITE)
    url = models.URLField(max_length=500)
    label = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "url"], name="org_unique_link_url"),
        ]

    def __str__(self):
        return f"{self.organization_id}: {self.url}"


def document_upload_to(document, filename):
    # Never build storage paths from user-supplied names.
    extension = os.path.splitext(filename)[1].lower()
    return f"organizations/{document.organization_id}/documents/{shortuuid.uuid()}{extension}"


class KnowledgeFolder(UUIDTimestampedModel):
    """A named group of documents with a stated purpose, e.g. "Past proposals"."""

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="folders")
    name = models.CharField(max_length=100)
    purpose = models.CharField(max_length=300, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(Lower("name"), "organization", name="org_unique_folder_name"),
        ]

    def __str__(self):
        return self.name


class OrganizationDocument(UUIDTimestampedModel):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="documents"
    )
    # RESTRICT: a folder with documents can't be deleted on its own, but deleting the
    # organization still removes both.
    folder = models.ForeignKey(KnowledgeFolder, on_delete=models.RESTRICT, related_name="documents")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    category = models.CharField(
        max_length=20, choices=DocumentCategory.choices, default=DocumentCategory.OTHER
    )
    file = models.FileField(upload_to=document_upload_to, max_length=500)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, blank=True, default="")
    size = models.PositiveBigIntegerField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="uploaded_organization_documents",
    )
    processing_status = models.CharField(
        max_length=12, choices=ProcessingStatus.choices, default=ProcessingStatus.PENDING
    )
    # Shown to the user when processing failed or the file is unsupported.
    processing_error = models.CharField(max_length=255, blank=True, default="")
    # Identifies the current processing attempt: a stale worker whose start time no longer
    # matches must not overwrite a newer attempt's result.
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["organization", "category"], name="org_document_category"),
            models.Index(fields=["processing_status"], name="org_document_processing"),
        ]

    def __str__(self):
        return self.title


class OrgSource(UUIDTimestampedModel):
    """An organization-configured source website, portal or listing page to search."""

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="org_sources"
    )
    url = models.URLField(max_length=500)
    label = models.CharField(max_length=120, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    enabled = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_org_sources",
    )

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "url"], name="org_source_unique_url"),
        ]

    def __str__(self):
        return self.label or self.url
