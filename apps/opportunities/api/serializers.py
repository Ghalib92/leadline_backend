from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.organizations.choices import OpportunityType

from ..choices import OpportunityStatus, RejectionReason, ReviewDecision
from ..models import Opportunity, OpportunityReview
from ..selectors import SORTS


class ReviewerSerializer(serializers.Serializer):
    id = serializers.CharField()
    email = serializers.EmailField()
    name = serializers.CharField()


class OpportunityReviewSerializer(serializers.ModelSerializer):
    reviewer = serializers.SerializerMethodField()

    class Meta:
        model = OpportunityReview
        fields = [
            "id",
            "decision",
            "reason",
            "comment",
            "reviewer",
            "status_before",
            "match_score",
            "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(ReviewerSerializer(allow_null=True))
    def get_reviewer(self, review):
        user = review.reviewer
        if user is None:
            return None
        return {"id": user.pk, "email": user.email, "name": user.get_full_name() or user.email}


class AmountSerializer(serializers.Serializer):
    min = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)
    max = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)
    currency = serializers.CharField()


class EligibilityCheckItemSerializer(serializers.Serializer):
    criterion = serializers.CharField()
    meets = serializers.BooleanField()
    notes = serializers.CharField(allow_blank=True, default="")


class OpportunityBriefSerializer(serializers.Serializer):
    what_it_is = serializers.CharField()
    why_it_fits = serializers.CharField()
    eligibility_checklist = EligibilityCheckItemSerializer(many=True)
    documents_needed = serializers.ListField(child=serializers.CharField())
    deadline_plan = serializers.CharField()
    effort_estimate = serializers.CharField()
    next_step = serializers.CharField()


class OpportunitySerializer(serializers.ModelSerializer):
    agent_name = serializers.SerializerMethodField()
    amount = serializers.SerializerMethodField()
    latest_review = serializers.SerializerMethodField()
    brief = serializers.SerializerMethodField()
    brief_status = serializers.SerializerMethodField()
    brief_sources = serializers.SerializerMethodField()

    class Meta:
        model = Opportunity
        fields = [
            "id",
            "agent",
            "agent_name",
            "run",
            "opportunity_type",
            "title",
            "funder",
            "summary",
            "eligibility",
            "amount",
            "deadline",
            "location",
            "source_url",
            "source_name",
            "match_score",
            "match_reasons",
            "status",
            "saved",
            "notes",
            "requirements",
            "business",
            "brief",
            "brief_status",
            "brief_sources",
            "latest_review",
            "found_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_agent_name(self, opportunity) -> str | None:
        return opportunity.agent.name if opportunity.agent else None

    @extend_schema_field(serializers.ChoiceField(choices=["pending", "ready", "failed", "none"]))
    def get_brief_status(self, opportunity) -> str:
        """`pending` while the background analyst works, `failed` when it gave up,
        `none` for client leads (no opportunity brief)."""
        if opportunity.opportunity_type == OpportunityType.CLIENT:
            return "none"
        requirements = opportunity.requirements
        if not isinstance(requirements, dict):
            requirements = {}
        if requirements.get("brief"):
            return "ready"
        return "failed" if requirements.get("brief_error") else "pending"

    def get_brief_sources(self, opportunity) -> dict | None:
        """Which documents the brief read: {mode, folders, documents}; null for briefs
        from before this was recorded."""
        requirements = opportunity.requirements
        return requirements.get("brief_sources") if isinstance(requirements, dict) else None

    @extend_schema_field(OpportunityBriefSerializer(allow_null=True))
    def get_brief(self, opportunity) -> dict | None:
        if isinstance(opportunity.requirements, dict):
            return opportunity.requirements.get("brief")
        return None

    @extend_schema_field(OpportunityReviewSerializer(allow_null=True))
    def get_latest_review(self, opportunity):
        """The current decision, or null while it awaits review."""
        reviews = list(opportunity.reviews.all())  # prefetched newest first
        return OpportunityReviewSerializer(reviews[0]).data if reviews else None

    @extend_schema_field(AmountSerializer(allow_null=True))
    def get_amount(self, opportunity):
        if opportunity.amount_min is None and opportunity.amount_max is None:
            return None
        return AmountSerializer(
            {
                "min": opportunity.amount_min,
                "max": opportunity.amount_max,
                "currency": opportunity.currency,
            }
        ).data


class OpportunityUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=OpportunityStatus.choices, required=False)
    saved = serializers.BooleanField(required=False)
    notes = serializers.CharField(max_length=5000, allow_blank=True, required=False)


class OpportunityFilterSerializer(serializers.Serializer):
    """Query parameters of the feed. `type` and `status` may repeat."""

    type = serializers.ListField(
        child=serializers.ChoiceField(choices=OpportunityType.choices), required=False
    )
    status = serializers.ListField(
        child=serializers.ChoiceField(choices=OpportunityStatus.choices), required=False
    )
    agent = serializers.CharField(max_length=12, required=False)
    run = serializers.CharField(max_length=12, required=False, allow_blank=True)
    saved = serializers.BooleanField(required=False, allow_null=True, default=None)
    search = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    min_score = serializers.IntegerField(min_value=0, max_value=100, required=False)
    deadline_before = serializers.DateTimeField(required=False)
    review = serializers.ChoiceField(choices=["pending", "approved", "rejected"], required=False)
    sort = serializers.ChoiceField(choices=list(SORTS), default="-found_at")


class OverviewChecklistSerializer(serializers.Serializer):
    profile = serializers.BooleanField()
    agent = serializers.BooleanField()
    teammate = serializers.BooleanField()


class OverviewPipelineSerializer(serializers.Serializer):
    new = serializers.IntegerField()
    reviewing = serializers.IntegerField()
    pursuing = serializers.IntegerField()
    submitted = serializers.IntegerField()


class AttentionItemSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(
        choices=["deadline_without_proposal", "to_review", "eligibility_gap", "failed_runs"]
    )
    count = serializers.IntegerField()
    target = serializers.CharField(allow_blank=True)


class OverviewSerializer(serializers.Serializer):
    new_this_week = serializers.IntegerField()
    pursuing = serializers.IntegerField()
    active_agents = serializers.IntegerField()
    due_soon = serializers.IntegerField()
    upcoming = OpportunitySerializer(many=True)
    latest = OpportunitySerializer(many=True)
    pipeline = OverviewPipelineSerializer()
    attention = AttentionItemSerializer(many=True)
    checklist = OverviewChecklistSerializer()


class ReviewCreateSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=ReviewDecision.choices)
    # Required when rejecting (the service says which is missing).
    reason = serializers.ChoiceField(choices=RejectionReason.choices, required=False)
    comment = serializers.CharField(max_length=2000, allow_blank=True, required=False)


class ReviewResultSerializer(serializers.Serializer):
    review = OpportunityReviewSerializer()
    opportunity = OpportunitySerializer()


class ChoiceSerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()


class CountSerializer(serializers.Serializer):
    key = serializers.CharField()
    count = serializers.IntegerField()


class RunWeekSerializer(serializers.Serializer):
    week = serializers.DateTimeField()
    runs = serializers.IntegerField()
    succeeded = serializers.IntegerField()
    partial = serializers.IntegerField()
    failed = serializers.IntegerField()
    found = serializers.IntegerField()


class ScoreBucketSerializer(serializers.Serializer):
    label = serializers.CharField()
    min = serializers.IntegerField()
    max = serializers.IntegerField()
    count = serializers.IntegerField()


class ReviewTotalsSerializer(serializers.Serializer):
    approved = serializers.IntegerField()
    rejected = serializers.IntegerField()
    pending = serializers.IntegerField()
    approval_rate = serializers.FloatField(allow_null=True)


class InsightsSerializer(serializers.Serializer):
    days = serializers.IntegerField()
    runs_by_week = RunWeekSerializer(many=True)
    opportunities_found = serializers.IntegerField()
    by_type = CountSerializer(many=True)
    by_source = CountSerializer(many=True)
    by_score = ScoreBucketSerializer(many=True)
    reviews = ReviewTotalsSerializer()
    rejection_reasons = CountSerializer(many=True)
