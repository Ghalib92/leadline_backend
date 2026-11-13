from rest_framework import serializers

from ..models import PreferenceSuggestion


class PreferenceSuggestionSerializer(serializers.ModelSerializer):
    evidence_count = serializers.SerializerMethodField()

    class Meta:
        model = PreferenceSuggestion
        fields = [
            "id",
            "kind",
            "value",
            "currency",
            "rationale",
            "evidence",
            "evidence_count",
            "status",
            "decided_by",
            "decided_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_evidence_count(self, suggestion) -> int:
        return len(suggestion.evidence)
