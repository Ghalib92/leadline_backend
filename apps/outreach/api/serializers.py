from rest_framework import serializers

from ..checks import MAX_REMINDER_DAYS
from ..choices import ContactKind, EmailPurpose
from ..models import Contact, ContactSearch, OutreachEmail


def _person(user):
    if user is None:
        return None
    return {"id": user.pk, "name": user.get_full_name() or user.email}


class PersonSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class EmailOpportunitySerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    funder = serializers.CharField()
    deadline = serializers.DateTimeField(allow_null=True)
    status = serializers.CharField()


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


class ContactSerializer(serializers.ModelSerializer):
    opportunity_id = serializers.CharField(read_only=True)
    can_email = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = [
            "id",
            "opportunity_id",
            "name",
            "role",
            "email",
            "phone",
            "kind",
            "relevance",
            "confidence",
            "source",
            "source_url",
            "verified",
            "do_not_contact",
            "notes",
            "can_email",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_can_email(self, contact) -> bool:
        return bool(contact.email) and not contact.do_not_contact


class ContactWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    role = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(max_length=254, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=40, required=False, allow_blank=True)
    kind = serializers.ChoiceField(choices=ContactKind.choices, required=False)
    notes = serializers.CharField(required=False, allow_blank=True)
    do_not_contact = serializers.BooleanField(required=False)


class ContactSearchSerializer(serializers.ModelSerializer):
    opportunity_id = serializers.CharField(read_only=True)
    requested_by = serializers.SerializerMethodField()

    class Meta:
        model = ContactSearch
        fields = [
            "id",
            "opportunity_id",
            "status",
            "found",
            "warning",
            "error",
            "requested_by",
            "started_at",
            "finished_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_requested_by(self, search) -> dict | None:
        return _person(search.requested_by)


# ---------------------------------------------------------------------------
# Outreach emails
# ---------------------------------------------------------------------------


class OutreachEmailSerializer(serializers.ModelSerializer):
    opportunity = EmailOpportunitySerializer(read_only=True)
    contact_id = serializers.CharField(read_only=True, allow_null=True)
    created_by = serializers.SerializerMethodField()
    approved_by = serializers.SerializerMethodField()

    class Meta:
        model = OutreachEmail
        fields = [
            "id",
            "opportunity",
            "contact_id",
            "to_email",
            "to_name",
            "purpose",
            "instructions",
            "subject",
            "body",
            "status",
            "error",
            "created_by",
            "approved_by",
            "approved_at",
            "sent_at",
            "prompt_id",
            "model",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_created_by(self, email) -> dict | None:
        return _person(email.created_by)

    def get_approved_by(self, email) -> dict | None:
        return _person(email.approved_by)


class EmailDraftSerializer(serializers.Serializer):
    """Without subject and body the email agent drafts it; with both, it is your draft."""

    contact = serializers.CharField()
    purpose = serializers.ChoiceField(choices=EmailPurpose.choices)
    instructions = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    subject = serializers.CharField(max_length=200, required=False)
    body = serializers.CharField(max_length=20000, required=False)

    def validate(self, attrs):
        if ("subject" in attrs) != ("body" in attrs):
            raise serializers.ValidationError(
                "Give both a subject and a body, or neither to let the AI draft it."
            )
        ai_draft = "body" not in attrs
        if ai_draft and attrs["purpose"] == EmailPurpose.OTHER:
            if not attrs.get("instructions", "").strip():
                raise serializers.ValidationError({"instructions": "Say what the email is about."})
        return attrs


class EmailEditSerializer(serializers.Serializer):
    subject = serializers.CharField(max_length=200, required=False)
    body = serializers.CharField(max_length=20000, required=False)


class EmailRedraftSerializer(serializers.Serializer):
    instructions = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class EmailApproveSerializer(serializers.Serializer):
    confirm_repeat = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Send even though this address was emailed about this opportunity recently.",
    )


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------


class DeadlineSerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    funder = serializers.CharField()
    opportunity_type = serializers.CharField()
    status = serializers.CharField()
    saved = serializers.BooleanField()
    deadline = serializers.DateTimeField()
    days_left = serializers.IntegerField()
    source_url = serializers.URLField()


class ReminderPreferenceSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False)
    days_before = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=MAX_REMINDER_DAYS),
        max_length=5,
        required=False,
    )

    def validate_days_before(self, value):
        return sorted(set(value), reverse=True)
