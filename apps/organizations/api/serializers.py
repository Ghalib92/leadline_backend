import os

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.authentication.models import User

from ..choices import (
    AccountType,
    BusinessCategory,
    BusinessSize,
    BuyerType,
    Eligibility,
    EmploymentType,
    GeographicScope,
    Goal,
    LeadSignal,
    OpportunityType,
    Region,
    Seniority,
    TenderCategory,
    TenderEligibility,
    Workplace,
)
from ..models import (
    ClientBrief,
    JobBrief,
    KnowledgeFolder,
    OpportunityPreferences,
    Organization,
    OrganizationDocument,
    OrganizationEvent,
    OrganizationInvitation,
    OrganizationLink,
    OrganizationMembership,
    OrgSource,
    TenderBrief,
)

LONG_TEXT = 5000

DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md",
    ".xls", ".xlsx", ".ods", ".csv",
    ".ppt", ".pptx", ".odp",
    ".png", ".jpg", ".jpeg",
}  # fmt: skip


def country_code(value):
    value = value.strip().upper()
    if value and (len(value) != 2 or not value.isascii() or not value.isalpha()):
        raise serializers.ValidationError("Use a two-letter country code, for example KE.")
    return value


class CountryCodeField(serializers.CharField):
    def to_internal_value(self, data):
        return country_code(super().to_internal_value(data))


class UniqueListField(serializers.ListField):
    """A bounded list that drops blanks and duplicates while keeping order."""

    def __init__(self, **kwargs):
        kwargs.setdefault("max_length", 50)
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        values = super().to_internal_value(data)
        seen, unique = set(), []
        for value in values:
            key = value.casefold() if isinstance(value, str) else value
            if value in ("", None) or key in seen:
                continue
            seen.add(key)
            unique.append(value)
        return unique


def tags(max_length=50):
    return UniqueListField(
        child=serializers.CharField(max_length=100, allow_blank=True), max_length=max_length
    )


def choices(choice_class):
    return UniqueListField(child=serializers.ChoiceField(choices=choice_class.choices))


def countries():
    return UniqueListField(child=CountryCodeField(max_length=2, allow_blank=True), max_length=250)


class OrganizationWriteSerializer(serializers.ModelSerializer):
    description = serializers.CharField(max_length=LONG_TEXT, allow_blank=True, required=False)
    mission = serializers.CharField(max_length=LONG_TEXT, allow_blank=True, required=False)
    vision = serializers.CharField(max_length=LONG_TEXT, allow_blank=True, required=False)
    offerings = serializers.CharField(max_length=LONG_TEXT, allow_blank=True, required=False)
    target_beneficiaries = serializers.CharField(
        max_length=LONG_TEXT, allow_blank=True, required=False
    )
    track_record = serializers.CharField(max_length=LONG_TEXT, allow_blank=True, required=False)
    past_funding = serializers.CharField(max_length=LONG_TEXT, allow_blank=True, required=False)
    sectors = tags()
    sdgs = UniqueListField(child=serializers.IntegerField(min_value=1, max_value=17), max_length=17)
    operating_countries = countries()
    languages = UniqueListField(
        child=serializers.RegexField(r"^[a-zA-Z]{2}$", max_length=2), max_length=30
    )
    goals = UniqueListField(child=serializers.ChoiceField(choices=Goal.choices), max_length=3)
    # Set when the workspace is created; a job seeker's is their own.
    account_type = serializers.ChoiceField(choices=AccountType.choices, required=False)

    class Meta:
        model = Organization
        fields = [
            "account_type",
            "name",
            "legal_name",
            "organization_type",
            "registration_number",
            "year_founded",
            "team_size",
            "annual_budget",
            "website",
            "contact_email",
            "phone",
            "country",
            "city",
            "address",
            "description",
            "mission",
            "vision",
            "offerings",
            "target_beneficiaries",
            "track_record",
            "past_funding",
            "industry",
            "sectors",
            "sdgs",
            "operating_countries",
            "languages",
            "goals",
        ]

    def validate_country(self, value):
        return country_code(value)

    def validate_contact_email(self, value):
        return User.objects.normalize_email(value)

    def validate_year_founded(self, value):
        if value is not None and not 1800 <= value <= timezone.now().year:
            raise serializers.ValidationError("Enter a valid founding year.")
        return value

    def validate_languages(self, value):
        return [code.lower() for code in value]


class MyMembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationMembership
        fields = ["id", "role", "status", "joined_at"]
        read_only_fields = fields


class OrganizationSerializer(serializers.ModelSerializer):
    my_membership = serializers.SerializerMethodField()
    is_archived = serializers.BooleanField(read_only=True)
    active_member_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Organization
        fields = [
            "id",
            "slug",
            *OrganizationWriteSerializer.Meta.fields,
            "created_by",
            "created_at",
            "updated_at",
            "archived_at",
            "is_archived",
            "active_member_count",
            "my_membership",
        ]
        read_only_fields = fields

    @extend_schema_field(MyMembershipSerializer)
    def get_my_membership(self, organization):
        members = getattr(organization, "my_memberships", [])
        return MyMembershipSerializer(members[0]).data if members else None


class MemberUserSerializer(serializers.ModelSerializer):
    first_name = serializers.CharField(source="profile.first_name", default="", read_only=True)
    last_name = serializers.CharField(source="profile.last_name", default="", read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "is_active"]
        read_only_fields = fields


class MembershipSerializer(serializers.ModelSerializer):
    user = MemberUserSerializer(read_only=True)

    class Meta:
        model = OrganizationMembership
        fields = [
            "id",
            "organization",
            "user",
            "role",
            "status",
            "joined_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class MembershipUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=OrganizationMembership.Role.choices, required=False)
    status = serializers.ChoiceField(choices=["active", "inactive"], required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide a role or membership status.")
        return attrs


class InvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=255)
    role = serializers.ChoiceField(choices=OrganizationMembership.Role.choices, default="member")
    message = serializers.CharField(max_length=1000, allow_blank=True, required=False, default="")

    def validate_email(self, value):
        return User.objects.normalize_email(value)


MAX_BULK_INVITATIONS = 20


class InvitationListField(serializers.ListField):
    def __init__(self, **kwargs):
        kwargs.setdefault("child", InvitationCreateSerializer())
        kwargs.setdefault("max_length", MAX_BULK_INVITATIONS)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        invitations = super().to_internal_value(data)
        emails = [invitation["email"].lower() for invitation in invitations]
        if len(emails) != len(set(emails)):
            raise serializers.ValidationError("Each email may only be invited once.")
        return invitations


class BulkInvitationCreateSerializer(serializers.Serializer):
    invitations = InvitationListField(allow_empty=False)


class OrganizationCreateSerializer(OrganizationWriteSerializer):
    """Creation also accepts the team to invite, so setup can happen in one step."""

    invitations = InvitationListField(required=False, allow_empty=True)

    class Meta(OrganizationWriteSerializer.Meta):
        fields = [*OrganizationWriteSerializer.Meta.fields, "invitations"]


class InvitationSerializer(serializers.ModelSerializer):
    status = serializers.ChoiceField(
        source="effective_status", choices=OrganizationInvitation.Status.choices, read_only=True
    )

    class Meta:
        model = OrganizationInvitation
        fields = [
            "id",
            "organization",
            "email",
            "role",
            "message",
            "invited_by",
            "status",
            "expires_at",
            "created_at",
            "updated_at",
            "responded_at",
            "accepted_by",
            "email_status",
            "last_email_attempt_at",
            "last_sent_at",
        ]
        read_only_fields = fields


class OrganizationCreatedSerializer(OrganizationSerializer):
    invitations = InvitationSerializer(many=True, read_only=True)

    class Meta(OrganizationSerializer.Meta):
        fields = [*OrganizationSerializer.Meta.fields, "invitations"]
        read_only_fields = fields


class InvitationPreviewSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    invited_by_name = serializers.SerializerMethodField()
    status = serializers.ChoiceField(
        source="effective_status", choices=OrganizationInvitation.Status.choices, read_only=True
    )

    class Meta:
        model = OrganizationInvitation
        fields = [
            "id",
            "organization",
            "organization_name",
            "email",
            "role",
            "message",
            "invited_by_name",
            "status",
            "expires_at",
            "responded_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_invited_by_name(self, obj) -> str:
        return obj.invited_by.get_full_name() if obj.invited_by else "An admin"


class InvitationTokenSerializer(serializers.Serializer):
    token = serializers.RegexField(
        r"^[A-Za-z0-9_-]{43}$", max_length=43, min_length=43, write_only=True
    )


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationEvent
        fields = ["id", "organization", "actor", "action", "target_id", "metadata", "created_at"]
        read_only_fields = fields


class OpportunityPreferencesSerializer(serializers.ModelSerializer):
    opportunity_types = choices(OpportunityType)
    geographic_scopes = choices(GeographicScope)
    target_countries = countries()
    target_regions = choices(Region)
    focus_areas = tags()
    keywords = tags(100)
    excluded_keywords = tags(100)
    preferred_funders = tags()
    excluded_funders = tags()
    eligibility = choices(Eligibility)
    min_funding_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, allow_null=True, required=False
    )
    max_funding_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, allow_null=True, required=False
    )
    funding_currency = serializers.RegexField(r"^[A-Za-z]{3}$", required=False)
    eligibility_notes = serializers.CharField(
        max_length=LONG_TEXT, allow_blank=True, required=False
    )
    additional_context = serializers.CharField(
        max_length=LONG_TEXT, allow_blank=True, required=False
    )

    class Meta:
        model = OpportunityPreferences
        fields = [
            "opportunity_types",
            "geographic_scopes",
            "target_countries",
            "target_regions",
            "focus_areas",
            "keywords",
            "excluded_keywords",
            "preferred_funders",
            "excluded_funders",
            "eligibility",
            "min_funding_amount",
            "max_funding_amount",
            "funding_currency",
            "open_to_partnerships",
            "can_provide_cofunding",
            "eligibility_notes",
            "additional_context",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_funding_currency(self, value):
        return value.upper()

    def validate(self, attrs):
        # Compare against stored values so a PATCH of one bound is still checked.
        current = self.instance
        minimum = attrs.get("min_funding_amount", getattr(current, "min_funding_amount", None))
        maximum = attrs.get("max_funding_amount", getattr(current, "max_funding_amount", None))
        if minimum is not None and maximum is not None and minimum > maximum:
            raise serializers.ValidationError(
                {"max_funding_amount": "Must be greater than or equal to the minimum."}
            )
        return attrs


class ServiceSerializer(serializers.Serializer):
    """Something the organization sells; the price is free text ("from KES 40,000")."""

    name = serializers.CharField(max_length=120)
    description = serializers.CharField(
        max_length=1000, allow_blank=True, required=False, default=""
    )
    price = serializers.CharField(max_length=120, allow_blank=True, required=False, default="")


class ClientBriefSerializer(serializers.ModelSerializer):
    services = ServiceSerializer(many=True, required=False, max_length=20)
    customer_types = choices(BusinessCategory)
    customer_keywords = tags()
    customer_sizes = choices(BusinessSize)
    target_countries = countries()
    target_cities = tags()
    signals = choices(LeadSignal)
    excluded_keywords = tags(100)
    notes = serializers.CharField(max_length=LONG_TEXT, allow_blank=True, required=False)

    class Meta:
        model = ClientBrief
        fields = [
            "services",
            "customer_types",
            "customer_keywords",
            "customer_sizes",
            "target_countries",
            "target_cities",
            "signals",
            "excluded_keywords",
            "notes",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_services(self, value):
        """One entry per name (case-insensitive); the first wins."""
        seen, services = set(), []
        for service in value:
            key = service["name"].strip().casefold()
            if key and key not in seen:
                seen.add(key)
                services.append({**service, "name": service["name"].strip()})
        return services


class JobBriefSerializer(serializers.ModelSerializer):
    headline = serializers.CharField(max_length=200, allow_blank=True, required=False)
    target_roles = tags()
    seniority = choices(Seniority)
    employment_types = choices(EmploymentType)
    workplaces = choices(Workplace)
    target_countries = countries()
    target_cities = tags()
    skills = tags(100)
    years_experience = serializers.IntegerField(
        min_value=0, max_value=60, allow_null=True, required=False
    )
    salary_min = serializers.IntegerField(min_value=0, allow_null=True, required=False)
    salary_currency = serializers.RegexField(r"^[A-Za-z]{3}$", required=False)
    companies = UniqueListField(
        child=serializers.CharField(max_length=300), max_length=50, required=False
    )
    excluded_keywords = tags(100)
    notes = serializers.CharField(max_length=LONG_TEXT, allow_blank=True, required=False)

    class Meta:
        model = JobBrief
        fields = [
            "headline",
            "target_roles",
            "seniority",
            "employment_types",
            "workplaces",
            "target_countries",
            "target_cities",
            "skills",
            "years_experience",
            "salary_min",
            "salary_currency",
            "companies",
            "excluded_keywords",
            "notes",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_salary_currency(self, value):
        return value.upper()

    def validate_companies(self, value):
        """Career page links (or company names): each kept once, trimmed."""
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


class TenderBriefSerializer(serializers.ModelSerializer):
    categories = choices(TenderCategory)
    keywords = tags(100)
    excluded_keywords = tags(100)
    buyer_types = choices(BuyerType)
    target_countries = countries()
    min_value = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, allow_null=True, required=False
    )
    max_value = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=0, allow_null=True, required=False
    )
    currency = serializers.RegexField(r"^[A-Za-z]{3}$", required=False)
    eligibility = choices(TenderEligibility)
    certifications = tags()
    capacity_notes = serializers.CharField(max_length=LONG_TEXT, allow_blank=True, required=False)

    class Meta:
        model = TenderBrief
        fields = [
            "categories",
            "keywords",
            "excluded_keywords",
            "buyer_types",
            "target_countries",
            "min_value",
            "max_value",
            "currency",
            "eligibility",
            "certifications",
            "capacity_notes",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_currency(self, value):
        return value.upper()

    def validate(self, attrs):
        # Compare against stored values so a PATCH of one bound is still checked.
        minimum = attrs.get("min_value", getattr(self.instance, "min_value", None))
        maximum = attrs.get("max_value", getattr(self.instance, "max_value", None))
        if minimum is not None and maximum is not None and minimum > maximum:
            raise serializers.ValidationError(
                {"max_value": "Must be greater than or equal to the minimum."}
            )
        return attrs


class LinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrganizationLink
        fields = ["id", "kind", "url", "label", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]
        # Duplicate URLs are reported by the service, which runs under the organization lock.
        validators = []


class FolderSerializer(serializers.ModelSerializer):
    document_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = KnowledgeFolder
        fields = ["id", "name", "purpose", "document_count", "created_at", "updated_at"]
        read_only_fields = ["id", "document_count", "created_at", "updated_at"]
        # Duplicate names are reported by the service, which runs under the organization lock.
        validators = []


class FolderRefSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class OrgSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrgSource
        fields = ["id", "url", "label", "notes", "enabled", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]
        validators = []


class OrgSourceWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrgSource
        fields = ["url", "label", "notes", "enabled"]
        extra_kwargs = {
            "label": {"required": False, "allow_blank": True},
            "notes": {"required": False, "allow_blank": True},
            "enabled": {"required": False},
        }
        validators = []


class DocumentSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    folder = FolderRefSerializer(read_only=True)

    class Meta:
        model = OrganizationDocument
        fields = [
            "id",
            "title",
            "description",
            "category",
            "folder",
            "original_filename",
            "content_type",
            "size",
            "processing_status",
            "processing_error",
            "processed_at",
            "uploaded_by",
            "download_url",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_download_url(self, document) -> str:
        path = reverse(
            "v1:organizations:document-download",
            kwargs={"organization_id": document.organization_id, "document_id": document.pk},
        )
        request = self.context.get("request")
        return request.build_absolute_uri(path) if request else path


class DocumentUploadSerializer(serializers.ModelSerializer):
    title = serializers.CharField(max_length=200)
    description = serializers.CharField(max_length=LONG_TEXT, allow_blank=True, required=False)
    file = serializers.FileField(max_length=255, allow_empty_file=False)
    folder_id = serializers.CharField(allow_blank=True)

    class Meta:
        model = OrganizationDocument
        fields = ["title", "description", "category", "folder_id", "file"]

    def validate_file(self, upload):
        extension = os.path.splitext(upload.name)[1].lower()
        if extension not in DOCUMENT_EXTENSIONS:
            raise serializers.ValidationError(
                f"Unsupported file type. Allowed: {', '.join(sorted(DOCUMENT_EXTENSIONS))}."
            )
        limit = settings.ORGANIZATION_DOCUMENT_MAX_UPLOAD_MB
        if upload.size > limit * 1024 * 1024:
            raise serializers.ValidationError(f"Files must be {limit} MB or smaller.")
        return upload


class DocumentUpdateSerializer(serializers.ModelSerializer):
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(max_length=LONG_TEXT, allow_blank=True, required=False)
    folder_id = serializers.CharField(required=False)

    class Meta:
        model = OrganizationDocument
        fields = ["title", "description", "category", "folder_id"]


class SetupStepSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    required = serializers.BooleanField()
    done = serializers.BooleanField()


class SetupStatusSerializer(serializers.Serializer):
    steps = SetupStepSerializer(many=True)
    completed = serializers.IntegerField()
    total = serializers.IntegerField()
    percent = serializers.IntegerField()
    ready_for_matching = serializers.BooleanField()
