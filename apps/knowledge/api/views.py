from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response

from apps.organizations.api.serializers import DocumentSerializer
from apps.organizations.api.views import OrganizationAPIView

from .. import selectors, services
from .serializers import DossierSerializer


@extend_schema(tags=["Company knowledge"], responses=DossierSerializer)
class DossierView(OrganizationAPIView):
    """What the AI knows about the organization. Any member may read it."""

    serializer_class = DossierSerializer

    def get(self, request, organization_id):
        organization = self.get_readable_organization()
        return Response(self.get_serializer(selectors.dossier_for(organization)).data)


@extend_schema(tags=["Company knowledge"], request=None, responses={202: DossierSerializer})
class DossierRefreshView(OrganizationAPIView):
    """Admins regenerate the dossier now; it is also regenerated automatically on changes."""

    serializer_class = DossierSerializer
    throttle_scope = "knowledge_refresh"

    def post(self, request, organization_id):
        dossier = services.request_dossier_refresh(
            organization_id=organization_id, actor=request.user
        )
        return Response(self.get_serializer(dossier).data, status=status.HTTP_202_ACCEPTED)


@extend_schema(
    tags=["Organization documents"], request=None, responses={202: DocumentSerializer}
)
class DocumentReprocessView(OrganizationAPIView):
    """Admins retry text extraction and embedding, e.g. after a failure."""

    serializer_class = DocumentSerializer
    throttle_scope = "knowledge_refresh"

    def post(self, request, organization_id, document_id):
        document = services.request_reprocessing(
            organization_id=organization_id, document_id=document_id, actor=request.user
        )
        return Response(self.get_serializer(document).data, status=status.HTTP_202_ACCEPTED)
