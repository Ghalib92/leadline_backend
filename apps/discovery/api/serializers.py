from django.utils import timezone
from rest_framework import serializers

from apps.ai.config.loader import load_sources_file
from apps.knowledge.scope import KnowledgeScope
from apps.organizations.choices import OpportunityType
from apps.organizations.models import OrganizationMembership

from ..choices import AgentSchedule, KnowledgeMode, RunDepth, RunStatus, SourceCategory
from ..models import DiscoveryRun, RunCandidate, RunEvent, RunSettings, SearchAgent


def _unique(values):
    return list(dict.fromkeys(values))


class AgentLinkSerializer(serializers.Serializer):
    url = serializers.URLField(max_length=500)
    label = serializers.CharField(max_length=120, allow_blank=True, required=False, default="")
    notes = serializers.CharField(max_length=500, allow_blank=True, required=False, default="")
    enabled = serializers.BooleanField(required=False, default=True)


class SearchAgentSerializer(serializers.ModelSerializer):
    last_run_id = serializers.CharField(read_only=True, allow_null=True)
    last_run_at = serializers.DateTimeField(read_only=True, allow_null=True)
    last_run_found = serializers.IntegerField(read_only=True, allow_null=True)
    last_run_status = serializers.ChoiceField(
        choices=RunStatus.choices, read_only=True, allow_null=True
    )
    last_run_opportunities = serializers.SerializerMethodField()
    folders = serializers.SerializerMethodField()
    effective = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()

    class Meta:
        model = SearchAgent
        fields = [
            "id",
            "name",
            "description",
            "instructions",
            "opportunity_types",
            "keywords",
            "sources",
            "depth",
            "folders",
            "links",
            "effective",
            "schedule",
            "enabled",
            "owner",
            "last_run_id",
            "last_run_at",
            "last_run_found",
            "last_run_status",
            "last_run_opportunities",
            "can_manage",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_folders(self, agent) -> list[dict]:
        """The folders this agent adds (the organization's apply as well)."""
        return [{"id": f.pk, "name": f.name} for f in agent.folders.order_by("name")]

    def get_effective(self, agent) -> dict:
        """What a run of this agent searches, each part tagged organization or agent."""
        from ..selectors import agent_scope

        return agent_scope(agent.organization, agent)

    def get_can_manage(self, agent) -> bool:
        """Whether the caller may edit, delete or run it (admins and the owner)."""
        membership = self.context.get("membership")
        if membership is None:
            return False
        return (
            membership.role == OrganizationMembership.Role.ADMIN
            or agent.owner_id == membership.user_id
        )

    def get_last_run_opportunities(self, agent) -> list[dict]:
        last_run_id = getattr(agent, "last_run_id", None)
        if not last_run_id:
            return []
        from apps.opportunities.models import Opportunity

        return list(
            Opportunity.objects.filter(run_id=last_run_id)
            .order_by("-match_score")
            .values(
                "id",
                "title",
                "funder",
                "source_url",
                "match_score",
                "opportunity_type",
                "status",
            )[:5]
        )


class SearchAgentWriteSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=2, max_length=60)
    description = serializers.CharField(max_length=280, allow_blank=True, required=False)
    instructions = serializers.CharField(
        max_length=2000, allow_blank=True, required=False, default=""
    )
    # What the agent adds to the organization's search settings; all may be empty.
    opportunity_types = serializers.ListField(
        child=serializers.ChoiceField(choices=OpportunityType.choices), required=False
    )
    keywords = serializers.ListField(
        child=serializers.CharField(max_length=60, allow_blank=True), max_length=20, required=False
    )
    sources = serializers.ListField(
        child=serializers.ChoiceField(choices=SourceCategory.choices), required=False
    )
    folders = serializers.ListField(
        child=serializers.CharField(max_length=40), max_length=50, required=False
    )
    depth = serializers.ChoiceField(choices=RunDepth.choices, allow_blank=True, required=False)
    links = AgentLinkSerializer(many=True, required=False, max_length=30)
    schedule = serializers.ChoiceField(choices=AgentSchedule.choices, required=False)
    enabled = serializers.BooleanField(required=False)

    def validate_opportunity_types(self, value):
        return _unique(value)

    def validate_sources(self, value):
        return _unique(value)

    def validate_keywords(self, value):
        return _unique(keyword.strip() for keyword in value if keyword.strip())

    def validate_links(self, value):
        """One entry per URL; the first wins."""
        seen, links = set(), []
        for link in value:
            if link["url"] not in seen:
                seen.add(link["url"])
                links.append(link)
        return links


class RunSettingsWriteSerializer(serializers.Serializer):
    depth = serializers.ChoiceField(choices=RunDepth.choices)
    sources = serializers.ListField(
        child=serializers.ChoiceField(choices=SourceCategory.choices), min_length=1
    )
    opportunity_types = serializers.ListField(
        child=serializers.ChoiceField(choices=OpportunityType.choices), min_length=1
    )
    folders = serializers.ListField(child=serializers.CharField(max_length=12), required=False)
    knowledge_mode = serializers.ChoiceField(choices=KnowledgeMode.choices, required=False)

    def validate_sources(self, value):
        return _unique(value)

    def validate_opportunity_types(self, value):
        return _unique(value)

    def validate_folders(self, value):
        return _unique(value)

    def validate(self, attrs):
        mode = attrs.get("knowledge_mode")
        if mode is None and not self.partial:
            # Clients from before the mode: chosen folders mean "only these".
            mode = KnowledgeMode.SELECTED if attrs.get("folders") else KnowledgeMode.ALL
            attrs["knowledge_mode"] = mode
        if mode == KnowledgeMode.SELECTED and not attrs.get("folders", [None]):
            raise serializers.ValidationError(
                {"folders": "Choose at least one folder, or let runs read all documents."}
            )
        return attrs


class RunSettingsSerializer(serializers.ModelSerializer):
    folders = serializers.SerializerMethodField()
    depths = serializers.SerializerMethodField()

    class Meta:
        model = RunSettings
        fields = [
            "depth",
            "sources",
            "opportunity_types",
            "folders",
            "knowledge_mode",
            "depths",
            "updated_at",
        ]
        read_only_fields = fields

    def get_folders(self, run_settings) -> list[dict]:
        return [{"id": f.pk, "name": f.name} for f in run_settings.folders.order_by("name")]

    def get_depths(self, run_settings) -> list[dict]:
        depths = load_sources_file().discovery.depths
        return [
            {"key": key, "label": depths[key].label, "description": depths[key].description}
            for key in RunDepth.values
        ]


class RunEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunEvent
        fields = ["seq", "step", "level", "message", "tool", "source", "url", "data", "at"]
        read_only_fields = fields


class RunStepSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    status = serializers.ChoiceField(
        choices=["pending", "running", "done", "warning", "failed", "skipped"]
    )
    started_at = serializers.DateTimeField(allow_null=True)
    duration_ms = serializers.IntegerField()
    events = RunEventSerializer(many=True)


class RunCandidateSerializer(serializers.ModelSerializer):
    class Meta:
        model = RunCandidate
        fields = [
            "title",
            "funder",
            "source_name",
            "source_url",
            "opportunity_type",
            "match_score",
            "match_reasons",
            "decision",
            "opportunity",
        ]
        read_only_fields = fields


class DiscoveryRunOpportunitySerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    funder = serializers.CharField(allow_blank=True, default="")
    source_url = serializers.CharField(allow_blank=True, default="")
    match_score = serializers.IntegerField()
    opportunity_type = serializers.CharField()
    status = serializers.CharField()


class DiscoveryRunSerializer(serializers.ModelSerializer):
    duration_ms = serializers.SerializerMethodField()
    opportunities = DiscoveryRunOpportunitySerializer(many=True, read_only=True)
    depth = serializers.SerializerMethodField()
    folders = serializers.SerializerMethodField()
    settings = serializers.SerializerMethodField()

    class Meta:
        model = DiscoveryRun
        fields = [
            "id",
            "agent",
            "agent_name",
            "trigger",
            "status",
            "created_at",
            "started_at",
            "finished_at",
            "duration_ms",
            "sources_scanned",
            "pages_read",
            "found",
            "error",
            "runner",
            "depth",
            "folders",
            "settings",
            "opportunities",
        ]
        read_only_fields = fields

    def get_depth(self, run) -> str:
        return run.config.get("depth", "")  # "" for runs from before depths

    def get_folders(self, run) -> list[dict]:
        return run.config.get("folders", [])

    def get_settings(self, run) -> dict:
        """What the run searched, each part tagged organization, agent or run (chosen
        for this run only). Built-in source types are kept apart from websites.
        Runs from before origins were recorded report everything as the organization's."""
        config = run.config
        origins = config.get("origins", {})

        def origin(field, value):
            return origins.get(field, {}).get(str(value), "organization")

        def tagged(field):
            return [{"value": v, "origin": origin(field, v)} for v in config.get(field, [])]

        return {
            "depth": {
                "value": config.get("depth", ""),
                "origin": origins.get("depth", "organization"),
            },
            "builtin_sources": tagged("sources"),
            "websites": [
                {
                    "url": link["url"],
                    "label": link.get("label", ""),
                    "origin": origin("links", link["url"]),
                }
                for link in config.get("links", [])
            ],
            "opportunity_types": tagged("opportunity_types"),
            "keywords": tagged("keywords"),
            "folders": [
                {"id": f["id"], "name": f["name"], "origin": origin("folders", f["id"])}
                for f in config.get("folders", [])
            ],
            # all, folders or none: which company documents the run could read.
            "knowledge_mode": KnowledgeScope.of_run(config).mode,
        }

    def get_duration_ms(self, run) -> int | None:
        """Elapsed so far for a running run; None until it starts."""
        if run.started_at is None:
            return None
        end = run.finished_at or timezone.now()
        return int((end - run.started_at).total_seconds() * 1000)


class RunSkippedSerializer(serializers.Serializer):
    closed = serializers.IntegerField()
    not_an_opportunity = serializers.IntegerField()
    wrong_type = serializers.IntegerField()
    excluded = serializers.IntegerField()
    unreadable = serializers.IntegerField()


class RunOutcomeSerializer(serializers.Serializer):
    """How a run ended; apps.discovery.selectors.run_outcome."""

    added = serializers.IntegerField()
    duplicates = serializers.IntegerField()
    below_threshold = serializers.IntegerField()
    skipped = RunSkippedSerializer()
    best_score = serializers.IntegerField(allow_null=True)
    match_threshold = serializers.IntegerField(allow_null=True)


class DiscoveryRunDetailSerializer(DiscoveryRunSerializer):
    steps = RunStepSerializer(many=True, source="computed_steps")
    candidates = RunCandidateSerializer(many=True)
    outcome = RunOutcomeSerializer(source="computed_outcome")

    class Meta(DiscoveryRunSerializer.Meta):
        fields = [
            *DiscoveryRunSerializer.Meta.fields,
            "budget",
            "usage",
            "prompt_versions",
            "models_used",
            "last_event_seq",
            "steps",
            "candidates",
            "outcome",
        ]
        read_only_fields = fields


class RunEventsSerializer(serializers.Serializer):
    """Live polling: events after `?after=`, plus enough to know when to stop."""

    run_status = serializers.ChoiceField(choices=RunStatus.choices)
    last_seq = serializers.IntegerField()
    events = RunEventSerializer(many=True)


class SourceCategorySerializer(serializers.Serializer):
    value = serializers.CharField()
    label = serializers.CharField()
