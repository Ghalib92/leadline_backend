import mimetypes

from django.db.models import Count, Q
from django.http import FileResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.throttling import ScopedRateThrottle

from apps.authentication.permissions import IsVerified
from apps.common.api.tickets import TicketAuthentication

from .. import selectors, services
from ..choices import DocumentCategory
from ..models import (
    KnowledgeFolder,
    Organization,
    OrganizationDocument,
    OrganizationInvitation,
    OrganizationLink,
    OrgSource,
)
from ..permissions import require_membership
from .serializers import (
    BulkInvitationCreateSerializer,
    ClientBriefSerializer,
    DocumentSerializer,
    DocumentUpdateSerializer,
    DocumentUploadSerializer,
    EventSerializer,
    FolderSerializer,
    InvitationCreateSerializer,
    InvitationPreviewSerializer,
    InvitationSerializer,
    InvitationTokenSerializer,
    JobBriefSerializer,
    LinkSerializer,
    MembershipSerializer,
    MembershipUpdateSerializer,
    OpportunityPreferencesSerializer,
    OrganizationCreatedSerializer,
    OrganizationCreateSerializer,
    OrganizationSerializer,
    OrganizationWriteSerializer,
    OrgSourceSerializer,
    OrgSourceWriteSerializer,
    ReadCvSerializer,
    SetupStatusSerializer,
    TenderBriefSerializer,
)


class OrganizationAPIView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsVerified]
    throttle_classes = [ScopedRateThrottle]

    def get_organization(self):
        return selectors.organization_for_user(
            user=self.request.user, organization_id=self.kwargs["organization_id"]
        )

    def get_readable_organization(self):
        """Any active member may read; archived organizations stay readable to their admins."""
        organization = self.get_organization()
        require_membership(organization=organization, user=self.request.user, allow_archived=True)
        return organization

    def organization_response(self, organization_id, *, response_status=200):
        organization = selectors.organization_for_user(
            user=self.request.user, organization_id=organization_id
        )
        return Response(OrganizationSerializer(organization).data, status=response_status)


@extend_schema_view(
    get=extend_schema(tags=["Organizations"]),
    post=extend_schema(
        tags=["Organizations"],
        request=OrganizationCreateSerializer,
        responses={201: OrganizationCreatedSerializer},
    ),
)
class OrganizationListCreateView(OrganizationAPIView, generics.ListAPIView):
    serializer_class = OrganizationSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Organization.objects.none()
        return selectors.organizations_for_user(self.request.user)

    def get_throttles(self):
        self.throttle_scope = "organization_create" if self.request.method == "POST" else None
        return super().get_throttles()

    def post(self, request):
        serializer = OrganizationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = services.create_organization(actor=request.user, **serializer.validated_data)
        organization = selectors.organization_for_user(
            user=request.user, organization_id=organization.pk
        )
        return Response(
            OrganizationCreatedSerializer(organization).data, status=status.HTTP_201_CREATED
        )


@extend_schema_view(
    get=extend_schema(tags=["Organizations"], responses=OrganizationSerializer),
    patch=extend_schema(
        tags=["Organizations"],
        request=OrganizationWriteSerializer,
        responses=OrganizationSerializer,
    ),
)
class OrganizationDetailView(OrganizationAPIView):
    serializer_class = OrganizationSerializer

    def get(self, request, organization_id):
        return self.organization_response(organization_id)

    def patch(self, request, organization_id):
        organization = self.get_organization()
        require_membership(organization=organization, user=request.user, admin=True)
        serializer = OrganizationWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        services.update_organization(
            organization_id=organization_id, actor=request.user, **serializer.validated_data
        )
        return self.organization_response(organization_id)


@extend_schema(tags=["Organizations"], request=None, responses=OrganizationSerializer)
class OrganizationArchiveView(OrganizationAPIView):
    serializer_class = OrganizationSerializer
    restore = False

    def post(self, request, organization_id):
        services.archive_organization(
            organization_id=organization_id, actor=request.user, restore=self.restore
        )
        return self.organization_response(organization_id)


class OrganizationRestoreView(OrganizationArchiveView):
    restore = True


@extend_schema(tags=["Organization members"], request=None, responses={204: None})
class OrganizationLeaveView(OrganizationAPIView):
    def post(self, request, organization_id):
        services.remove_membership(organization_id=organization_id, actor=request.user, leave=True)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Organization members"],
    parameters=[
        OpenApiParameter("status", str, enum=["active", "inactive", "removed"]),
    ],
)
class MembershipListView(OrganizationAPIView, generics.ListAPIView):
    serializer_class = MembershipSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Organization.objects.none()
        queryset = selectors.members_for_user(
            organization=self.get_organization(), user=self.request.user
        )
        member_status = self.request.query_params.get("status")
        if member_status:
            if member_status not in {"active", "inactive", "removed"}:
                raise ValidationError({"status": "Invalid membership status."})
            queryset = queryset.filter(status=member_status)
        return queryset


@extend_schema_view(
    get=extend_schema(tags=["Organization members"], responses=MembershipSerializer),
    patch=extend_schema(
        tags=["Organization members"],
        request=MembershipUpdateSerializer,
        responses=MembershipSerializer,
    ),
    delete=extend_schema(tags=["Organization members"], request=None, responses={204: None}),
)
class MembershipDetailView(OrganizationAPIView):
    serializer_class = MembershipSerializer
    lookup_url_kwarg = "membership_id"

    def get_queryset(self):
        return selectors.members_for_user(
            organization=self.get_organization(), user=self.request.user
        )

    def get(self, request, organization_id, membership_id):
        return Response(MembershipSerializer(self.get_object()).data)

    def patch(self, request, organization_id, membership_id):
        serializer = MembershipUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = services.update_membership(
            organization_id=organization_id,
            membership_id=membership_id,
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(MembershipSerializer(member).data)

    def delete(self, request, organization_id, membership_id):
        services.remove_membership(
            organization_id=organization_id, membership_id=membership_id, actor=request.user
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    get=extend_schema(
        tags=["Organization invitations"],
        parameters=[
            OpenApiParameter("status", str, enum=OrganizationInvitation.Status.values),
        ],
    ),
    post=extend_schema(
        tags=["Organization invitations"],
        request=InvitationCreateSerializer,
        responses={201: InvitationSerializer},
    ),
)
class InvitationListCreateView(OrganizationAPIView, generics.ListAPIView):
    serializer_class = InvitationSerializer

    def get_throttles(self):
        self.throttle_scope = "organization_invitation" if self.request.method == "POST" else None
        return super().get_throttles()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return OrganizationInvitation.objects.none()
        organization = self.get_organization()
        require_membership(organization=organization, user=self.request.user, admin=True)
        queryset = organization.invitations.all()
        invite_status = self.request.query_params.get("status")
        if invite_status:
            if invite_status not in OrganizationInvitation.Status.values:
                raise ValidationError({"status": "Invalid invitation status."})
            now = timezone.now()
            if invite_status == "expired":
                queryset = queryset.filter(
                    Q(status="expired") | Q(status="pending", expires_at__lte=now)
                )
            elif invite_status == "pending":
                queryset = queryset.filter(status="pending", expires_at__gt=now)
            else:
                queryset = queryset.filter(status=invite_status)
        return queryset

    def post(self, request, organization_id):
        serializer = InvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = services.invite_member(
            organization_id=organization_id, actor=request.user, **serializer.validated_data
        )
        invitation.refresh_from_db()
        return Response(InvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=["Organization invitations"],
    request=BulkInvitationCreateSerializer,
    responses={201: InvitationSerializer(many=True)},
)
class InvitationBulkCreateView(OrganizationAPIView):
    """Invite several people at once; nothing is sent unless every invitation is valid."""

    serializer_class = BulkInvitationCreateSerializer
    throttle_scope = "organization_invitation"

    def post(self, request, organization_id):
        serializer = BulkInvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitations = services.invite_members(
            organization_id=organization_id, actor=request.user, **serializer.validated_data
        )
        for invitation in invitations:
            invitation.refresh_from_db()
        return Response(
            InvitationSerializer(invitations, many=True).data, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["Organization invitations"], request=None, responses=InvitationSerializer)
class InvitationResendView(OrganizationAPIView):
    serializer_class = InvitationSerializer
    throttle_scope = "organization_invitation"

    def post(self, request, organization_id, invitation_id):
        invitation = services.resend_invitation(
            organization_id=organization_id, invitation_id=invitation_id, actor=request.user
        )
        invitation.refresh_from_db()
        return Response(InvitationSerializer(invitation).data)


@extend_schema(tags=["Organization invitations"], request=None, responses=InvitationSerializer)
class InvitationRevokeView(OrganizationAPIView):
    serializer_class = InvitationSerializer

    def post(self, request, organization_id, invitation_id):
        invitation = services.revoke_invitation(
            organization_id=organization_id, invitation_id=invitation_id, actor=request.user
        )
        return Response(InvitationSerializer(invitation).data)


@extend_schema(
    tags=["Organization invitations"],
    request=InvitationTokenSerializer,
    responses=InvitationPreviewSerializer,
)
class InvitationPreviewView(OrganizationAPIView):
    serializer_class = InvitationTokenSerializer
    throttle_scope = "organization_invitation_token"

    def validated_token(self):
        serializer = self.get_serializer(data=self.request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data["token"]

    def post(self, request):
        invitation = services.preview_invitation(token=self.validated_token(), actor=request.user)
        return Response(InvitationPreviewSerializer(invitation).data)


@extend_schema(
    tags=["Organization invitations"],
    request=InvitationTokenSerializer,
    responses=MembershipSerializer,
)
class InvitationAcceptView(InvitationPreviewView):
    def post(self, request):
        member = services.respond_to_invitation(token=self.validated_token(), actor=request.user)
        return Response(MembershipSerializer(member).data)


@extend_schema(
    tags=["Organization invitations"], request=InvitationTokenSerializer, responses={204: None}
)
class InvitationDeclineView(InvitationPreviewView):
    def post(self, request):
        services.respond_to_invitation(
            token=self.validated_token(), actor=request.user, accept=False
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Organization invitations"],
    parameters=[OpenApiParameter("status", str, enum=OrganizationInvitation.Status.values)],
)
class ReceivedInvitationListView(OrganizationAPIView, generics.ListAPIView):
    """Invitations sent to the signed-in user's email, with their current status.

    Pending ones can be answered by id below; answered ones show as accepted/declined.
    """

    serializer_class = InvitationPreviewSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return OrganizationInvitation.objects.none()
        queryset = selectors.received_invitations(self.request.user)
        invite_status = self.request.query_params.get("status")
        if invite_status:
            if invite_status not in OrganizationInvitation.Status.values:
                raise ValidationError({"status": "Invalid invitation status."})
            now = timezone.now()
            if invite_status == "expired":
                queryset = queryset.filter(
                    Q(status="expired") | Q(status="pending", expires_at__lte=now)
                )
            elif invite_status == "pending":
                queryset = queryset.filter(status="pending", expires_at__gt=now)
            else:
                queryset = queryset.filter(status=invite_status)
        return queryset


@extend_schema(tags=["Organization invitations"], request=None, responses=MembershipSerializer)
class ReceivedInvitationAcceptView(OrganizationAPIView):
    serializer_class = MembershipSerializer
    throttle_scope = "organization_invitation_token"

    def post(self, request, invitation_id):
        member = services.respond_to_invitation(invitation_id=invitation_id, actor=request.user)
        return Response(MembershipSerializer(member).data)


@extend_schema(tags=["Organization invitations"], request=None, responses={204: None})
class ReceivedInvitationDeclineView(OrganizationAPIView):
    throttle_scope = "organization_invitation_token"

    def post(self, request, invitation_id):
        services.respond_to_invitation(
            invitation_id=invitation_id, actor=request.user, accept=False
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Organization activity"])
class EventListView(OrganizationAPIView, generics.ListAPIView):
    serializer_class = EventSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Organization.objects.none()
        organization = self.get_organization()
        require_membership(
            organization=organization, user=self.request.user, admin=True, allow_archived=True
        )
        return organization.events.all()


@extend_schema(tags=["Organizations"], responses=SetupStatusSerializer)
class OrganizationSetupView(OrganizationAPIView):
    serializer_class = SetupStatusSerializer

    def get(self, request, organization_id):
        status_data = selectors.setup_status(self.get_readable_organization())
        return Response(SetupStatusSerializer(status_data).data)


@extend_schema_view(
    get=extend_schema(tags=["Organization preferences"]),
    put=extend_schema(tags=["Organization preferences"]),
    patch=extend_schema(tags=["Organization preferences"]),
)
class OpportunityPreferencesView(OrganizationAPIView):
    serializer_class = OpportunityPreferencesSerializer

    def get(self, request, organization_id):
        preferences = selectors.preferences_for(self.get_readable_organization())
        return Response(self.get_serializer(preferences).data)

    def patch(self, request, organization_id):
        organization = self.get_organization()
        require_membership(organization=organization, user=request.user, admin=True)
        preferences = selectors.preferences_for(organization)
        serializer = self.get_serializer(preferences, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        preferences = services.update_preferences(
            organization_id=organization_id, actor=request.user, **serializer.validated_data
        )
        return Response(self.get_serializer(preferences).data)

    put = patch


class _BriefView(OrganizationAPIView):
    """Read by any member, edited by admins; one per organization, created on first use."""

    def brief(self, organization):  # pragma: no cover - set by subclasses
        raise NotImplementedError

    def save(self, **data):  # pragma: no cover - set by subclasses
        raise NotImplementedError

    def get(self, request, organization_id):
        return Response(self.get_serializer(self.brief(self.get_readable_organization())).data)

    def patch(self, request, organization_id):
        organization = self.get_organization()
        require_membership(organization=organization, user=request.user, admin=True)
        serializer = self.get_serializer(self.brief(organization), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        brief = self.save(
            organization_id=organization_id, actor=request.user, **serializer.validated_data
        )
        return Response(self.get_serializer(brief).data)

    put = patch


@extend_schema_view(
    get=extend_schema(tags=["Organization briefs"]),
    put=extend_schema(tags=["Organization briefs"]),
    patch=extend_schema(tags=["Organization briefs"]),
)
class ClientBriefView(_BriefView):
    """What the organization sells and who it sells to (Clients agents)."""

    serializer_class = ClientBriefSerializer

    def brief(self, organization):
        return selectors.client_brief_for(organization)

    def save(self, **data):
        return services.update_client_brief(**data)


@extend_schema_view(
    get=extend_schema(tags=["Organization briefs"]),
    put=extend_schema(tags=["Organization briefs"]),
    patch=extend_schema(tags=["Organization briefs"]),
)
class JobBriefView(_BriefView):
    """What a job seeker looks for (Jobs agents). `cv_suggestions` is what reading their
    CV found: the fields it filled in and job titles and skills it suggests adding."""

    serializer_class = JobBriefSerializer

    def brief(self, organization):
        return selectors.job_brief_for(organization)

    def save(self, **data):
        return services.update_job_brief(**data)


@extend_schema(
    tags=["Organization briefs"], request=ReadCvSerializer, responses={202: JobBriefSerializer}
)
class JobBriefReadCvView(OrganizationAPIView):
    """Read the CV into the job preferences again (in the background; poll the brief).
    Without `document_id`, the newest document filed as a CV, else the newest one."""

    serializer_class = JobBriefSerializer

    def post(self, request, organization_id):
        self.get_organization()
        serializer = ReadCvSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.request_cv_read(
            organization_id=organization_id,
            actor=request.user,
            document_id=serializer.validated_data.get("document_id") or None,
        )
        brief = selectors.job_brief_for(self.get_readable_organization())
        return Response(JobBriefSerializer(brief).data, status=status.HTTP_202_ACCEPTED)


@extend_schema(tags=["Organization briefs"], request=None, responses={200: JobBriefSerializer})
class JobBriefDismissSuggestionsView(OrganizationAPIView):
    """Drop the remaining job titles and skills suggested from the CV."""

    serializer_class = JobBriefSerializer

    def post(self, request, organization_id):
        self.get_organization()
        brief = services.dismiss_cv_suggestions(organization_id=organization_id, actor=request.user)
        return Response(JobBriefSerializer(brief).data)


@extend_schema_view(
    get=extend_schema(tags=["Organization briefs"]),
    put=extend_schema(tags=["Organization briefs"]),
    patch=extend_schema(tags=["Organization briefs"]),
)
class TenderBriefView(_BriefView):
    """What the organization bids for (Tenders agents)."""

    serializer_class = TenderBriefSerializer

    def brief(self, organization):
        return selectors.tender_brief_for(organization)

    def save(self, **data):
        return services.update_tender_brief(**data)


@extend_schema_view(
    get=extend_schema(tags=["Organization links"]),
    post=extend_schema(tags=["Organization links"], responses={201: LinkSerializer}),
)
class LinkListCreateView(OrganizationAPIView, generics.ListAPIView):
    serializer_class = LinkSerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return OrganizationLink.objects.none()
        return self.get_readable_organization().links.all()

    def post(self, request, organization_id):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = services.add_link(
            organization_id=organization_id, actor=request.user, **serializer.validated_data
        )
        return Response(self.get_serializer(link).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    patch=extend_schema(tags=["Organization links"]),
    delete=extend_schema(tags=["Organization links"], responses={204: None}),
)
class LinkDetailView(OrganizationAPIView):
    serializer_class = LinkSerializer

    def patch(self, request, organization_id, link_id):
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        link = services.update_link(
            organization_id=organization_id,
            link_id=link_id,
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(self.get_serializer(link).data)

    def delete(self, request, organization_id, link_id):
        services.remove_link(organization_id=organization_id, link_id=link_id, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    get=extend_schema(tags=["Organization documents"]),
    post=extend_schema(tags=["Organization documents"], responses={201: FolderSerializer}),
)
class FolderListCreateView(OrganizationAPIView, generics.ListAPIView):
    serializer_class = FolderSerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return KnowledgeFolder.objects.none()
        return self.get_readable_organization().folders.annotate(document_count=Count("documents"))

    def post(self, request, organization_id):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        folder = services.create_folder(
            organization_id=organization_id, actor=request.user, **serializer.validated_data
        )
        return Response(self.get_serializer(folder).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    patch=extend_schema(tags=["Organization documents"]),
    delete=extend_schema(tags=["Organization documents"], responses={204: None}),
)
class FolderDetailView(OrganizationAPIView):
    serializer_class = FolderSerializer

    def patch(self, request, organization_id, folder_id):
        serializer = self.get_serializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        folder = services.update_folder(
            organization_id=organization_id,
            folder_id=folder_id,
            actor=request.user,
            **serializer.validated_data,
        )
        folder.document_count = folder.documents.count()
        return Response(self.get_serializer(folder).data)

    def delete(self, request, organization_id, folder_id):
        services.delete_folder(
            organization_id=organization_id, folder_id=folder_id, actor=request.user
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    get=extend_schema(tags=["Organization sources"]),
    post=extend_schema(
        tags=["Organization sources"],
        request=OrgSourceWriteSerializer,
        responses={201: OrgSourceSerializer},
    ),
)
class OrgSourceListCreateView(OrganizationAPIView, generics.ListAPIView):
    serializer_class = OrgSourceSerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return OrgSource.objects.none()
        return self.get_readable_organization().org_sources.all()

    def post(self, request, organization_id):
        serializer = OrgSourceWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        source = services.add_org_source(
            organization_id=organization_id, actor=request.user, **serializer.validated_data
        )
        return Response(OrgSourceSerializer(source).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(tags=["Organization sources"]),
    patch=extend_schema(tags=["Organization sources"], request=OrgSourceWriteSerializer),
    delete=extend_schema(tags=["Organization sources"], responses={204: None}),
)
class OrgSourceDetailView(OrganizationAPIView):
    serializer_class = OrgSourceSerializer
    lookup_url_kwarg = "source_id"

    def get_queryset(self):
        return self.get_readable_organization().org_sources.all()

    def get(self, request, organization_id, source_id):
        return Response(self.get_serializer(self.get_object()).data)

    def patch(self, request, organization_id, source_id):
        serializer = OrgSourceWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        source = services.update_org_source(
            organization_id=organization_id,
            source_id=source_id,
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(OrgSourceSerializer(source).data)

    def delete(self, request, organization_id, source_id):
        services.remove_org_source(
            organization_id=organization_id, source_id=source_id, actor=request.user
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    get=extend_schema(
        tags=["Organization documents"],
        parameters=[
            OpenApiParameter("category", str, enum=DocumentCategory.values),
            OpenApiParameter("folder", str, description="A folder id."),
            OpenApiParameter("search", str, description="Matches title or description."),
        ],
    ),
    post=extend_schema(
        tags=["Organization documents"],
        request={"multipart/form-data": DocumentUploadSerializer},
        responses={201: DocumentSerializer},
    ),
)
class DocumentListCreateView(OrganizationAPIView, generics.ListAPIView):
    serializer_class = DocumentSerializer
    # The browser uploads straight here with a transfer ticket (see apps.common.api.tickets).
    authentication_classes = [*api_settings.DEFAULT_AUTHENTICATION_CLASSES, TicketAuthentication]
    parser_classes = [MultiPartParser, FormParser]

    def get_throttles(self):
        self.throttle_scope = "organization_document" if self.request.method == "POST" else None
        return super().get_throttles()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return OrganizationDocument.objects.none()
        queryset = self.get_readable_organization().documents.select_related("folder")
        category = self.request.query_params.get("category")
        if category:
            if category not in DocumentCategory.values:
                raise ValidationError({"category": "Invalid document category."})
            queryset = queryset.filter(category=category)
        folder = self.request.query_params.get("folder")
        if folder:
            queryset = queryset.filter(folder_id=folder)
        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(description__icontains=search)
            )
        return queryset

    def post(self, request, organization_id):
        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = services.upload_document(
            organization_id=organization_id, actor=request.user, **serializer.validated_data
        )
        return Response(self.get_serializer(document).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    get=extend_schema(tags=["Organization documents"]),
    patch=extend_schema(tags=["Organization documents"], request=DocumentUpdateSerializer),
    delete=extend_schema(tags=["Organization documents"], responses={204: None}),
)
class DocumentDetailView(OrganizationAPIView):
    serializer_class = DocumentSerializer
    lookup_url_kwarg = "document_id"

    def get_queryset(self):
        return self.get_readable_organization().documents.all()

    def get(self, request, organization_id, document_id):
        return Response(self.get_serializer(self.get_object()).data)

    def patch(self, request, organization_id, document_id):
        serializer = DocumentUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        document = services.update_document(
            organization_id=organization_id,
            document_id=document_id,
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(self.get_serializer(document).data)

    def delete(self, request, organization_id, document_id):
        services.delete_document(
            organization_id=organization_id, document_id=document_id, actor=request.user
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Organization documents"],
    responses={(200, "application/octet-stream"): bytes},
)
class DocumentDownloadView(OrganizationAPIView):
    """Files are only served through this member-checked view, never from a public URL.
    The browser downloads straight from here with a transfer ticket."""

    authentication_classes = [*api_settings.DEFAULT_AUTHENTICATION_CLASSES, TicketAuthentication]

    lookup_url_kwarg = "document_id"

    def get_queryset(self):
        return self.get_readable_organization().documents.all()

    def get(self, request, organization_id, document_id):
        document = self.get_object()
        content_type = mimetypes.guess_type(document.original_filename)[0]
        return FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=document.original_filename,
            content_type=content_type or "application/octet-stream",
        )
