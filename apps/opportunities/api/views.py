from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.organizations.api.views import OrganizationAPIView

from .. import selectors, services
from ..choices import RejectionReason
from ..models import Opportunity
from .serializers import (
    ChoiceSerializer,
    InsightsSerializer,
    OpportunityBriefSerializer,
    OpportunityFilterSerializer,
    OpportunityReviewSerializer,
    OpportunitySerializer,
    OpportunityUpdateSerializer,
    OverviewSerializer,
    ReviewCreateSerializer,
    ReviewResultSerializer,
)

INSIGHT_DAYS = (7, 30, 90, 180, 365)


def _filters(query_params):
    data = {key: query_params.getlist(key) for key in ("type", "status") if key in query_params}
    data.update({key: value for key, value in query_params.items() if key not in data})
    serializer = OpportunityFilterSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    filters = dict(serializer.validated_data)
    return {
        "types": filters.pop("type", ()),
        "statuses": filters.pop("status", ()),
        "agent_id": filters.pop("agent", None),
        "run_id": filters.pop("run", None),
        **filters,
    }


@extend_schema(
    tags=["Opportunities"],
    parameters=[OpportunityFilterSerializer],
)
class OpportunityListView(OrganizationAPIView, generics.ListAPIView):
    """The organization's feed, newest first unless `sort` says otherwise."""

    serializer_class = OpportunitySerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Opportunity.objects.none()
        return selectors.opportunities_for(
            self.get_readable_organization(), **_filters(self.request.query_params)
        )


@extend_schema_view(
    get=extend_schema(tags=["Opportunities"]),
    patch=extend_schema(tags=["Opportunities"], request=OpportunityUpdateSerializer),
)
class OpportunityDetailView(OrganizationAPIView):
    serializer_class = OpportunitySerializer
    lookup_url_kwarg = "opportunity_id"

    def get_queryset(self):
        return selectors.opportunities_for(self.get_readable_organization())

    def get(self, request, organization_id, opportunity_id):
        return Response(self.get_serializer(self.get_object()).data)

    def patch(self, request, organization_id, opportunity_id):
        self.get_object()  # 404 for outsiders and unknown opportunities before validation
        serializer = OpportunityUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.update_opportunity(
            organization_id=organization_id,
            opportunity_id=opportunity_id,
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(self.get_serializer(self.get_object()).data)


@extend_schema_view(
    get=extend_schema(tags=["Opportunities"], responses={200: OpportunityBriefSerializer}),
    post=extend_schema(tags=["Opportunities"], responses={200: OpportunityBriefSerializer}),
)
class OpportunityBriefView(OrganizationAPIView):
    """View the executive brief for an opportunity, or generate it on demand."""

    serializer_class = OpportunityBriefSerializer

    def get_opportunity(self):
        return generics.get_object_or_404(
            selectors.opportunities_for(self.get_readable_organization()),
            pk=self.kwargs["opportunity_id"],
        )

    def get(self, request, organization_id, opportunity_id):
        opportunity = self.get_opportunity()
        brief_data = (opportunity.requirements or {}).get("brief")
        if not brief_data:
            brief = services.generate_brief_for_opportunity(opportunity)
            brief_data = brief.model_dump()
        return Response(OpportunityBriefSerializer(brief_data).data)

    def post(self, request, organization_id, opportunity_id):
        """Force regenerates the brief."""
        opportunity = self.get_opportunity()
        brief = services.generate_brief_for_opportunity(opportunity, force=True)
        return Response(OpportunityBriefSerializer(brief.model_dump()).data)



@extend_schema(
    tags=["Opportunities"],
    responses=OverviewSerializer,
    parameters=[OpenApiParameter("organization_id", str, OpenApiParameter.PATH)],
)
class OverviewView(OrganizationAPIView):
    """Dashboard numbers: new this week, pursuing, due soon, latest and setup checklist."""

    serializer_class = OverviewSerializer

    def get(self, request, organization_id):
        data = selectors.overview(self.get_readable_organization())
        return Response(OverviewSerializer(data, context=self.get_serializer_context()).data)


@extend_schema_view(
    get=extend_schema(tags=["Opportunity reviews"]),
    post=extend_schema(
        tags=["Opportunity reviews"],
        request=ReviewCreateSerializer,
        responses={201: ReviewResultSerializer},
    ),
)
class OpportunityReviewView(OrganizationAPIView, generics.ListAPIView):
    """Approve (moves it to pursuing and starts the Proposal Writer) or reject with a
    reason and a comment (moves it to dismissed). GET lists the review history."""

    serializer_class = OpportunityReviewSerializer
    pagination_class = None

    def get_throttles(self):
        self.throttle_scope = "opportunity_review" if self.request.method == "POST" else None
        return super().get_throttles()

    def get_opportunity(self):
        return generics.get_object_or_404(
            selectors.opportunities_for(self.get_readable_organization()),
            pk=self.kwargs["opportunity_id"],
        )

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Opportunity.objects.none()
        return selectors.reviews_for(self.get_opportunity())

    def post(self, request, organization_id, opportunity_id):
        self.get_opportunity()  # 404 for outsiders before validation
        serializer = ReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        review = services.review(
            organization_id=organization_id,
            opportunity_id=opportunity_id,
            actor=request.user,
            **serializer.validated_data,
        )
        data = {"review": review, "opportunity": self.get_opportunity()}
        return Response(ReviewResultSerializer(data).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Opportunity reviews"], responses=ChoiceSerializer(many=True))
class RejectionReasonListView(OrganizationAPIView):
    """The reason categories for rejecting, for the reject dialog."""

    serializer_class = ChoiceSerializer

    def get(self, request, organization_id):
        self.get_readable_organization()
        data = [{"value": value, "label": label} for value, label in RejectionReason.choices]
        return Response(ChoiceSerializer(data, many=True).data)


@extend_schema(
    tags=["Opportunities"],
    responses=InsightsSerializer,
    parameters=[OpenApiParameter("days", int, enum=list(INSIGHT_DAYS), default=90)],
)
class InsightsView(OrganizationAPIView):
    """Dashboard charts: runs per week, opportunities by type, source and score, and
    how they were reviewed (approval rate, rejection reasons)."""

    serializer_class = InsightsSerializer

    def get(self, request, organization_id):
        try:
            days = int(request.query_params.get("days", 90))
        except ValueError:
            days = 0
        if days not in INSIGHT_DAYS:
            raise ValidationError({"days": f"Use one of {', '.join(map(str, INSIGHT_DAYS))}."})
        data = selectors.insights(self.get_readable_organization(), days=days)
        return Response(InsightsSerializer(data).data)
