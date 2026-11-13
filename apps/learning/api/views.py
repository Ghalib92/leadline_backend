from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.organizations.api.views import OrganizationAPIView

from .. import services
from ..choices import SuggestionStatus
from ..models import PreferenceSuggestion
from .serializers import PreferenceSuggestionSerializer


@extend_schema(
    tags=["Learning"],
    parameters=[
        OpenApiParameter(
            "status", str, enum=SuggestionStatus.values, description="Default: pending."
        )
    ],
)
class SuggestionListView(OrganizationAPIView, generics.ListAPIView):
    """Preference changes suggested from repeated rejections. `evidence` lists the
    review ids behind each one."""

    serializer_class = PreferenceSuggestionSerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PreferenceSuggestion.objects.none()
        status = self.request.query_params.get("status", SuggestionStatus.PENDING)
        if status not in SuggestionStatus.values:
            raise ValidationError({"status": "Unknown status."})
        organization = self.get_readable_organization()
        return organization.preference_suggestions.filter(status=status)


class _DecisionView(OrganizationAPIView):
    serializer_class = PreferenceSuggestionSerializer
    decide = None  # the service; not `action`, which DRF views already use

    def post(self, request, organization_id, suggestion_id):
        self.get_readable_organization()  # 404 for outsiders first
        suggestion = self.decide(
            organization_id=organization_id, suggestion_id=suggestion_id, actor=request.user
        )
        return Response(self.get_serializer(suggestion).data)


@extend_schema(tags=["Learning"], request=None, responses=PreferenceSuggestionSerializer)
class SuggestionAcceptView(_DecisionView):
    """Admins: apply the change to the organization's preferences."""

    decide = staticmethod(services.accept_suggestion)


@extend_schema(tags=["Learning"], request=None, responses=PreferenceSuggestionSerializer)
class SuggestionDismissView(_DecisionView):
    """Admins: never suggest this value again."""

    decide = staticmethod(services.dismiss_suggestion)
