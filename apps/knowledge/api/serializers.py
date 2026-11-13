from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.ai.schemas.dossier import Dossier

from ..models import CompanyDossier


# Mirrors apps.ai.schemas.dossier.Dossier for the API docs; a test keeps the fields in sync.
class DossierContentSerializer(serializers.Serializer):
    """What the AI knows about the organization, as structured fields."""

    summary = serializers.CharField()
    organization_type = serializers.CharField()
    mission = serializers.CharField()
    focus_areas = serializers.ListField(child=serializers.CharField())
    offerings = serializers.ListField(child=serializers.CharField())
    beneficiaries = serializers.ListField(child=serializers.CharField())
    geographies = serializers.ListField(child=serializers.CharField())
    track_record = serializers.ListField(child=serializers.CharField())
    past_funders = serializers.ListField(child=serializers.CharField())
    strengths = serializers.ListField(child=serializers.CharField())
    eligibility_facts = serializers.ListField(child=serializers.CharField())
    seeking = serializers.ListField(child=serializers.CharField())
    search_keywords = serializers.ListField(child=serializers.CharField())
    gaps = serializers.ListField(child=serializers.CharField())


class DossierSerializer(serializers.ModelSerializer):
    content = serializers.SerializerMethodField()

    class Meta:
        model = CompanyDossier
        fields = ["status", "content", "version", "generated_at", "error", "prompt_id", "model"]
        read_only_fields = fields

    @extend_schema_field(DossierContentSerializer(allow_null=True))
    def get_content(self, dossier):
        # Older rows are re-read through the current schema, so new fields appear empty.
        if not dossier.content:
            return None
        return Dossier.model_validate(dossier.content).model_dump(mode="json")
