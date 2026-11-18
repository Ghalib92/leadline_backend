from django.http import HttpResponse
from django.utils.text import slugify
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.organizations.api.views import OrganizationAPIView

from .. import selectors, services
from ..export import proposal_docx
from ..models import Proposal
from .serializers import (
    ChecklistTickSerializer,
    ProposalEventsSerializer,
    ProposalKnowledgeSerializer,
    ProposalSerializer,
    ProposalSummarySerializer,
    SectionEditSerializer,
    SectionRewriteSerializer,
    VersionSerializer,
    VersionSummarySerializer,
)

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class ProposalAPIView(OrganizationAPIView):
    """Any member may read and work on proposals."""

    def get_proposal(self):
        return generics.get_object_or_404(
            selectors.proposals_for(self.get_readable_organization()),
            pk=self.kwargs["proposal_id"],
        )

    def proposal_response(self, proposal_id, *, response_status=200):
        proposal = generics.get_object_or_404(
            selectors.proposals_for(self.get_readable_organization()), pk=proposal_id
        )
        context = {"current_version": selectors.current_version(proposal)}
        return Response(ProposalSerializer(proposal, context=context).data, status=response_status)

    starts_ai_job = False  # POSTs of these views are throttled (they cost model calls)

    def get_throttles(self):
        ai_job = self.starts_ai_job and self.request.method == "POST"
        self.throttle_scope = "proposal_job" if ai_job else None
        return super().get_throttles()


@extend_schema(tags=["Proposals"])
class ProposalListView(ProposalAPIView, generics.ListAPIView):
    serializer_class = ProposalSummarySerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Proposal.objects.none()
        return selectors.proposals_for(self.get_readable_organization())


@extend_schema(tags=["Proposals"])
class OpportunityProposalView(ProposalAPIView):
    """GET the opportunity's proposal (404 until one exists). POST writes a new full
    draft (the first, or again from scratch; earlier versions are kept)."""

    serializer_class = ProposalSerializer
    starts_ai_job = True

    def get(self, request, organization_id, opportunity_id):
        organization = self.get_readable_organization()
        proposal = generics.get_object_or_404(
            selectors.proposals_for(organization), opportunity_id=opportunity_id
        )
        return self.proposal_response(proposal.pk)

    @extend_schema(request=None, responses={202: ProposalSerializer})
    def post(self, request, organization_id, opportunity_id):
        generics.get_object_or_404(
            self.get_readable_organization().opportunities, pk=opportunity_id
        )
        proposal = services.start_draft(
            organization_id=organization_id, opportunity_id=opportunity_id, actor=request.user
        )
        return self.proposal_response(proposal.pk, response_status=status.HTTP_202_ACCEPTED)


@extend_schema(tags=["Proposals"])
class ProposalDetailView(ProposalAPIView):
    serializer_class = ProposalSerializer

    def get(self, request, organization_id, proposal_id):
        return self.proposal_response(self.get_proposal().pk)

    @extend_schema(request=ProposalKnowledgeSerializer, responses=ProposalSerializer)
    def patch(self, request, organization_id, proposal_id):
        """Choose which company documents the next drafts read."""
        proposal = self.get_proposal()
        serializer = ProposalKnowledgeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.set_knowledge(
            organization_id=organization_id,
            proposal_id=proposal.pk,
            actor=request.user,
            **serializer.validated_data,
        )
        return self.proposal_response(proposal.pk)


@extend_schema(
    tags=["Proposals"],
    responses=ProposalEventsSerializer,
    parameters=[OpenApiParameter("after", int, description="Events with seq above this.")],
)
class ProposalEventsView(ProposalAPIView):
    """Live progress of drafting: poll with the last `seq` while status is busy."""

    serializer_class = ProposalEventsSerializer

    def get(self, request, organization_id, proposal_id):
        proposal = self.get_proposal()
        try:
            after = max(0, int(request.query_params.get("after", 0)))
        except ValueError as exc:
            raise ValidationError({"after": "Must be a whole number."}) from exc
        events = list(selectors.events_after(proposal, after))
        data = {
            "status": proposal.status,
            "last_seq": events[-1].seq if events else after,
            "events": events,
        }
        return Response(ProposalEventsSerializer(data).data)


@extend_schema(tags=["Proposals"])
class VersionListView(ProposalAPIView, generics.ListAPIView):
    serializer_class = VersionSummarySerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Proposal.objects.none()
        return selectors.versions_for(self.get_proposal())


@extend_schema(tags=["Proposals"])
class VersionDetailView(ProposalAPIView):
    serializer_class = VersionSerializer

    def get(self, request, organization_id, proposal_id, number):
        version = generics.get_object_or_404(
            selectors.versions_for(self.get_proposal()), number=number
        )
        return Response(VersionSerializer(version).data)


@extend_schema(tags=["Proposals"], request=None, responses={201: ProposalSerializer})
class VersionRestoreView(ProposalAPIView):
    """Make an earlier version current again (as a new version)."""

    serializer_class = ProposalSerializer

    def post(self, request, organization_id, proposal_id, number):
        proposal = self.get_proposal()
        services.restore_version(
            organization_id=organization_id,
            proposal_id=proposal.pk,
            number=number,
            actor=request.user,
        )
        return self.proposal_response(proposal.pk, response_status=status.HTTP_201_CREATED)


@extend_schema(tags=["Proposals"], request=SectionEditSerializer, responses=ProposalSerializer)
class SectionEditView(ProposalAPIView):
    """A person's edit to one section; makes a new version. Send the `base_version`
    you edited: 409 `version_conflict` if someone changed it since."""

    serializer_class = ProposalSerializer

    def patch(self, request, organization_id, proposal_id, key):
        proposal = self.get_proposal()
        serializer = SectionEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.edit_section(
            organization_id=organization_id,
            proposal_id=proposal.pk,
            key=key,
            actor=request.user,
            **serializer.validated_data,
        )
        return self.proposal_response(proposal.pk)


@extend_schema(
    tags=["Proposals"], request=SectionRewriteSerializer, responses={202: ProposalSerializer}
)
class SectionRewriteView(ProposalAPIView):
    """Have the AI rewrite one section, optionally following instructions."""

    serializer_class = ProposalSerializer
    starts_ai_job = True

    def post(self, request, organization_id, proposal_id, key):
        proposal = self.get_proposal()
        serializer = SectionRewriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.rewrite_section(
            organization_id=organization_id,
            proposal_id=proposal.pk,
            key=key,
            actor=request.user,
            **serializer.validated_data,
        )
        return self.proposal_response(proposal.pk, response_status=status.HTTP_202_ACCEPTED)


@extend_schema(tags=["Proposals"], request=ChecklistTickSerializer, responses=ProposalSerializer)
class ChecklistItemView(ProposalAPIView):
    """Tick a bid requirement as in hand (`done: true`), or untick it."""

    serializer_class = ProposalSerializer

    def patch(self, request, organization_id, proposal_id, key):
        proposal = self.get_proposal()
        serializer = ChecklistTickSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.tick_checklist_item(
            organization_id=organization_id,
            proposal_id=proposal.pk,
            key=key,
            done=serializer.validated_data["done"],
            actor=request.user,
        )
        return self.proposal_response(proposal.pk)


@extend_schema(tags=["Proposals"], request=None, responses={202: ProposalSerializer})
class ChecklistRecheckView(ProposalAPIView):
    """Check the tender's requirements against company documents again (after
    uploading what was missing). 409 `not_tender` or `no_checklist`."""

    serializer_class = ProposalSerializer
    starts_ai_job = True

    def post(self, request, organization_id, proposal_id):
        proposal = self.get_proposal()
        services.recheck_documents(
            organization_id=organization_id, proposal_id=proposal.pk, actor=request.user
        )
        return self.proposal_response(proposal.pk, response_status=status.HTTP_202_ACCEPTED)


@extend_schema(
    tags=["Proposals"],
    parameters=[OpenApiParameter("version", int, description="Defaults to the current one.")],
    responses={(200, DOCX): OpenApiTypes.BINARY},
)
class ProposalExportView(ProposalAPIView):
    """Download a version as a Word document."""

    def get(self, request, organization_id, proposal_id):
        proposal = self.get_proposal()
        number = request.query_params.get("version") or proposal.version
        try:
            version = proposal.versions.get(number=int(number))
        except (ValueError, proposal.versions.model.DoesNotExist) as exc:
            raise ValidationError({"version": "No such version."}) from exc
        filename = (
            f"{slugify(proposal.opportunity.title)[:60] or 'proposal'}-v{version.number}.docx"
        )
        response = HttpResponse(proposal_docx(proposal, version), content_type=DOCX)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
