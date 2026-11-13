from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.ai.config.loader import load_sources_file
from apps.organizations.api.views import OrganizationAPIView
from apps.organizations.permissions import require_membership

from .. import selectors, services
from ..choices import RunStatus
from ..models import DiscoveryRun, SearchAgent
from .serializers import (
    DiscoveryRunDetailSerializer,
    DiscoveryRunSerializer,
    RunEventsSerializer,
    RunSettingsSerializer,
    RunSettingsWriteSerializer,
    SearchAgentSerializer,
    SearchAgentWriteSerializer,
    SourceCategorySerializer,
)


class AgentAPIView(OrganizationAPIView):
    def get_serializer_context(self):
        context = super().get_serializer_context()
        if "organization_id" in self.kwargs and self.request.user.is_authenticated:
            organization = self.get_readable_organization()
            context["membership"] = require_membership(
                organization=organization, user=self.request.user, allow_archived=True
            )
        return context

    def agent_response(self, agent_id, *, response_status=200):
        agent = selectors.agents_for(self.get_readable_organization()).get(pk=agent_id)
        return Response(self.get_serializer(agent).data, status=response_status)


@extend_schema_view(
    get=extend_schema(tags=["Search agents"]),
    post=extend_schema(
        tags=["Search agents"],
        request=SearchAgentWriteSerializer,
        responses={201: SearchAgentSerializer},
    ),
)
class AgentListCreateView(AgentAPIView, generics.ListAPIView):
    """Any member may create an agent and becomes its owner."""

    serializer_class = SearchAgentSerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SearchAgent.objects.none()
        return selectors.agents_for(self.get_readable_organization())

    def post(self, request, organization_id):
        self.get_readable_organization()  # outsiders get 404 before any validation
        serializer = SearchAgentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agent = services.create_agent(
            organization_id=organization_id, actor=request.user, **serializer.validated_data
        )
        return self.agent_response(agent.pk, response_status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(tags=["Search agents"]),
    patch=extend_schema(tags=["Search agents"], request=SearchAgentWriteSerializer),
    delete=extend_schema(tags=["Search agents"], responses={204: None}),
)
class AgentDetailView(AgentAPIView):
    """Editing and deleting take an admin or the agent's owner."""

    serializer_class = SearchAgentSerializer
    lookup_url_kwarg = "agent_id"

    def get_queryset(self):
        return selectors.agents_for(self.get_readable_organization())

    def get(self, request, organization_id, agent_id):
        return Response(self.get_serializer(self.get_object()).data)

    def patch(self, request, organization_id, agent_id):
        self.get_object()  # 404 for outsiders and unknown agents before validation
        serializer = SearchAgentWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        services.update_agent(
            organization_id=organization_id,
            agent_id=agent_id,
            actor=request.user,
            **serializer.validated_data,
        )
        return self.agent_response(agent_id)

    def delete(self, request, organization_id, agent_id):
        services.delete_agent(
            organization_id=organization_id, agent_id=agent_id, actor=request.user
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Discovery runs"],
    request=RunSettingsWriteSerializer,
    responses={202: DiscoveryRunDetailSerializer},
)
class AgentRunView(OrganizationAPIView):
    """Start a search now. 409 while another run is active, the agent is off, or the
    organization setup is incomplete (`code` says which)."""

    serializer_class = DiscoveryRunDetailSerializer
    throttle_scope = "discovery_run"

    def post(self, request, organization_id, agent_id):
        # Optional: settings for this run only; anything left out comes from the agent
        # (sources, types) or the organization's run settings (depth, folders).
        overrides = RunSettingsWriteSerializer(data=request.data or {}, partial=True)
        overrides.is_valid(raise_exception=True)
        run = services.start_run(
            organization_id=organization_id,
            agent_id=agent_id,
            actor=request.user,
            overrides=overrides.validated_data,
        )
        return Response(run_detail(run), status=status.HTTP_202_ACCEPTED)


def run_detail(run):
    run = DiscoveryRun.objects.prefetch_related("candidates", "opportunities").get(pk=run.pk)
    events = list(run.events.order_by("seq"))
    run.computed_steps = selectors.run_steps(run, events)
    run.computed_outcome = selectors.run_outcome(run, events)
    return DiscoveryRunDetailSerializer(run).data


@extend_schema_view(
    get=extend_schema(
        tags=["Discovery runs"],
        parameters=[
            OpenApiParameter("agent", str, description="Only this agent's runs."),
            OpenApiParameter("status", str, enum=RunStatus.values, many=True),
        ],
    ),
    post=extend_schema(
        tags=["Discovery runs"],
        request=RunSettingsWriteSerializer,
        responses={202: DiscoveryRunDetailSerializer},
    ),
)
class RunListView(OrganizationAPIView, generics.ListAPIView):
    """GET: run history, newest first (steps and candidates are on the detail).

    POST: "Search for opportunities" from the organization's search settings, with no
    saved search. Same body and 409 codes as running an agent."""

    serializer_class = DiscoveryRunSerializer

    def get_throttles(self):
        self.throttle_scope = "discovery_run" if self.request.method == "POST" else None
        return super().get_throttles()

    def post(self, request, organization_id):
        self.get_readable_organization()  # outsiders get 404 before any validation
        overrides = RunSettingsWriteSerializer(data=request.data or {}, partial=True)
        overrides.is_valid(raise_exception=True)
        run = services.start_search(
            organization_id=organization_id,
            actor=request.user,
            overrides=overrides.validated_data,
        )
        return Response(run_detail(run), status=status.HTTP_202_ACCEPTED)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DiscoveryRun.objects.none()
        statuses = self.request.query_params.getlist("status")
        unknown = set(statuses) - set(RunStatus.values)
        if unknown:
            raise ValidationError({"status": f"Unknown status: {', '.join(sorted(unknown))}."})
        return selectors.runs_for(
            self.get_readable_organization(),
            agent_id=self.request.query_params.get("agent"),
            statuses=statuses,
        )


@extend_schema(tags=["Discovery runs"], responses=DiscoveryRunDetailSerializer)
class RunDetailView(OrganizationAPIView):
    """A run with its five steps (each with its events) and every candidate it scored."""

    serializer_class = DiscoveryRunDetailSerializer

    def get(self, request, organization_id, run_id):
        organization = self.get_readable_organization()
        run = generics.get_object_or_404(selectors.runs_for(organization), pk=run_id)
        return Response(run_detail(run))


@extend_schema(
    tags=["Discovery runs"],
    responses=RunEventsSerializer,
    parameters=[
        OpenApiParameter(
            "after", int, description="Return events with seq greater than this (default 0)."
        )
    ],
)
class RunEventsView(OrganizationAPIView):
    """Live timeline: poll with the last `seq` you have until `run_status` is final."""

    serializer_class = RunEventsSerializer

    def get(self, request, organization_id, run_id):
        organization = self.get_readable_organization()
        run = generics.get_object_or_404(selectors.runs_for(organization), pk=run_id)
        try:
            after = max(0, int(request.query_params.get("after", 0)))
        except ValueError as exc:
            raise ValidationError({"after": "Must be a whole number."}) from exc
        events = list(selectors.events_after(run, after))
        return Response(
            RunEventsSerializer(
                {
                    "run_status": run.status,
                    "last_seq": events[-1].seq if events else after,
                    "events": events,
                }
            ).data
        )


@extend_schema(tags=["Search agents"], responses=SourceCategorySerializer(many=True))
class SourceCategoryListView(OrganizationAPIView):
    """Where agents can search, for the agent form."""

    serializer_class = SourceCategorySerializer

    def get(self, request, organization_id):
        self.get_readable_organization()
        categories = load_sources_file().source_categories
        data = [{"value": key, "label": item.label} for key, item in categories.items()]
        return Response(SourceCategorySerializer(data, many=True).data)


@extend_schema_view(
    get=extend_schema(tags=["Search agents"]),
    put=extend_schema(tags=["Search agents"], request=RunSettingsWriteSerializer),
)
class RunSettingsView(OrganizationAPIView):
    """The organization's defaults for "Run with my settings". Admins change them."""

    serializer_class = RunSettingsSerializer

    def get(self, request, organization_id):
        organization = self.get_readable_organization()
        return Response(self.get_serializer(selectors.run_settings_for(organization)).data)

    def put(self, request, organization_id):
        organization = self.get_organization()
        require_membership(organization=organization, user=request.user, admin=True)
        serializer = RunSettingsWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        run_settings = services.update_run_settings(
            organization_id=organization_id,
            actor=request.user,
            **{"folders": [], **serializer.validated_data},
        )
        return Response(self.get_serializer(run_settings).data)
