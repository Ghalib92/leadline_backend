from rest_framework import serializers

from ..choices import ProposalJob, ProposalStatus
from ..models import Proposal, ProposalEvent, ProposalVersion


class EvidenceSerializer(serializers.Serializer):
    n = serializers.IntegerField()
    document_id = serializers.CharField(allow_blank=True)
    document_title = serializers.CharField()
    location = serializers.CharField(allow_blank=True)
    excerpt = serializers.CharField()


class SectionSerializer(serializers.Serializer):
    key = serializers.CharField()
    title = serializers.CharField()
    purpose = serializers.CharField(allow_blank=True, default="")
    key_points = serializers.ListField(child=serializers.CharField(), default=list)
    target_words = serializers.IntegerField(default=0)
    content = serializers.CharField(allow_blank=True)
    evidence = EvidenceSerializer(many=True, default=list)
    missing_information = serializers.ListField(child=serializers.CharField(), default=list)
    drafted = serializers.BooleanField(default=True)


class AuthorSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


def _author(user):
    if user is None:
        return None
    return {"id": user.pk, "name": user.get_full_name() or user.email}


class VersionSummarySerializer(serializers.ModelSerializer):
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = ProposalVersion
        fields = ["number", "source", "note", "created_by", "created_at"]
        read_only_fields = fields

    def get_created_by(self, version) -> dict | None:
        return _author(version.created_by)


class VersionSerializer(VersionSummarySerializer):
    sections = SectionSerializer(many=True)

    class Meta(VersionSummarySerializer.Meta):
        fields = [*VersionSummarySerializer.Meta.fields, "sections"]
        read_only_fields = fields


class OpportunitySummarySerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    funder = serializers.CharField()
    deadline = serializers.DateTimeField(allow_null=True)
    status = serializers.CharField()


class JobSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=ProposalJob.choices)
    section = serializers.CharField(allow_blank=True)
    instructions = serializers.CharField(allow_blank=True)
    queued_at = serializers.DateTimeField(allow_null=True)
    started_at = serializers.DateTimeField(allow_null=True)


class ProposalSummarySerializer(serializers.ModelSerializer):
    opportunity = serializers.SerializerMethodField()
    job = serializers.SerializerMethodField()

    class Meta:
        model = Proposal
        fields = [
            "id",
            "opportunity",
            "status",
            "job",
            "version",
            "warning",
            "error",
            "drafted_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_opportunity(self, proposal) -> OpportunitySummarySerializer:
        opportunity = proposal.opportunity
        return OpportunitySummarySerializer(
            {
                "id": opportunity.pk,
                "title": opportunity.title,
                "funder": opportunity.funder,
                "deadline": opportunity.deadline,
                "status": opportunity.status,
            }
        ).data

    def get_job(self, proposal) -> JobSerializer:
        """The running or last job."""
        return JobSerializer(
            {
                "kind": proposal.job,
                "section": proposal.job_section,
                "instructions": proposal.job_instructions,
                "queued_at": proposal.job_queued_at,
                "started_at": proposal.job_started_at,
            }
        ).data


class ProposalKnowledgeSerializer(serializers.Serializer):
    """Which documents evidence comes from; "run" follows the run that found it."""

    knowledge_mode = serializers.ChoiceField(choices=["all", "selected", "none", "run"])
    folders = serializers.ListField(
        child=serializers.CharField(max_length=40), max_length=50, required=False
    )

    def validate(self, attrs):
        if attrs["knowledge_mode"] == "selected" and not attrs.get("folders"):
            raise serializers.ValidationError({"folders": "Choose at least one folder."})
        return attrs


class ProposalSerializer(ProposalSummarySerializer):
    sections = serializers.SerializerMethodField()
    knowledge = serializers.SerializerMethodField()

    class Meta(ProposalSummarySerializer.Meta):
        fields = [
            *ProposalSummarySerializer.Meta.fields,
            "sections",
            "knowledge",
            "requirements",
            "review",
            "prompt_versions",
            "models_used",
            "usage",
            "last_event_seq",
        ]
        read_only_fields = fields

    def get_knowledge(self, proposal) -> dict:
        """{source: proposal|run, mode: all|folders|none, folders: [{id, name}]}."""
        from apps.organizations.models import KnowledgeFolder

        from ..selectors import knowledge_scope

        scope, source = knowledge_scope(proposal)
        names = dict(
            KnowledgeFolder.objects.filter(
                organization_id=proposal.organization_id, pk__in=scope.folder_ids
            ).values_list("pk", "name")
        )
        return {
            "source": source,
            "mode": scope.mode,
            "folders": [
                {"id": fid, "name": names[fid]} for fid in scope.folder_ids if fid in names
            ],
        }

    def get_sections(self, proposal) -> SectionSerializer(many=True):
        """The current version's sections ([] before the first draft)."""
        version = self.context.get("current_version")
        return SectionSerializer(version.sections if version else [], many=True).data


class ProposalEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProposalEvent
        fields = ["seq", "step", "level", "message", "tool", "url", "data", "at"]
        read_only_fields = fields


class ProposalEventsSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ProposalStatus.choices)
    last_seq = serializers.IntegerField()
    events = ProposalEventSerializer(many=True)


class SectionEditSerializer(serializers.Serializer):
    base_version = serializers.IntegerField(min_value=1)
    title = serializers.CharField(max_length=150, required=False)
    content = serializers.CharField(max_length=50_000, allow_blank=True, required=False)

    def validate(self, data):
        if "title" not in data and "content" not in data:
            raise serializers.ValidationError("Send a title or content.")
        return data


class SectionRewriteSerializer(serializers.Serializer):
    instructions = serializers.CharField(max_length=1000, allow_blank=True, required=False)
