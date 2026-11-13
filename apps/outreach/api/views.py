from datetime import date, datetime, time, timedelta

from django.http import HttpResponse
from django.utils import timezone
from django.utils.text import slugify
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.opportunities.api.serializers import ChoiceSerializer
from apps.organizations.api.views import OrganizationAPIView

from .. import selectors, services
from ..calendar import deadlines_ics
from ..choices import EmailPurpose, EmailStatus
from ..models import Contact, OutreachEmail
from .serializers import (
    ContactSearchSerializer,
    ContactSerializer,
    ContactWriteSerializer,
    DeadlineSerializer,
    EmailApproveSerializer,
    EmailDraftSerializer,
    EmailEditSerializer,
    EmailRedraftSerializer,
    OutreachEmailSerializer,
    ReminderPreferenceSerializer,
)

DEFAULT_DEADLINE_DAYS = 90
MAX_DEADLINE_DAYS = 366
ICS = "text/calendar; charset=utf-8"


class OutreachAPIView(OrganizationAPIView):
    """Any member may read and work on contacts and drafts; only admins send."""

    starts_ai_job = False  # POSTs of these views are throttled (they cost model calls)

    def get_throttles(self):
        ai_job = self.starts_ai_job and self.request.method == "POST"
        self.throttle_scope = "outreach_job" if ai_job else None
        return super().get_throttles()

    def get_opportunity(self):
        return generics.get_object_or_404(
            self.get_readable_organization().opportunities, pk=self.kwargs["opportunity_id"]
        )

    def get_email(self):
        return generics.get_object_or_404(
            selectors.outreach_for(self.get_readable_organization()), pk=self.kwargs["email_id"]
        )

    def validated(self, serializer_class, **kwargs):
        serializer = serializer_class(data=self.request.data, **kwargs)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def email_response(self, email_id, *, response_status=200):
        organization = self.get_readable_organization()
        email = selectors.outreach_for(organization).get(pk=email_id)
        return Response(OutreachEmailSerializer(email).data, status=response_status)


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


@extend_schema(tags=["Outreach"])
class ContactListView(OutreachAPIView, generics.ListAPIView):
    """The funder contacts for an opportunity, best first. POST adds one by hand."""

    serializer_class = ContactSerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Contact.objects.none()
        return selectors.contacts_for(self.get_opportunity())

    @extend_schema(request=ContactWriteSerializer, responses={201: ContactSerializer})
    def post(self, request, organization_id, opportunity_id):
        self.get_opportunity()  # 404 for outsiders first
        contact = services.add_contact(
            organization_id=organization_id,
            opportunity_id=opportunity_id,
            actor=request.user,
            **self.validated(ContactWriteSerializer),
        )
        return Response(ContactSerializer(contact).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Outreach"])
class ContactDetailView(OutreachAPIView):
    serializer_class = ContactSerializer

    def get_contact(self):
        return generics.get_object_or_404(
            self.get_readable_organization().contacts, pk=self.kwargs["contact_id"]
        )

    def get(self, request, organization_id, contact_id):
        return Response(ContactSerializer(self.get_contact()).data)

    @extend_schema(request=ContactWriteSerializer)
    def patch(self, request, organization_id, contact_id):
        self.get_contact()
        contact = services.update_contact(
            organization_id=organization_id,
            contact_id=contact_id,
            actor=request.user,
            **self.validated(ContactWriteSerializer, partial=True),
        )
        return Response(ContactSerializer(contact).data)

    @extend_schema(responses={204: None})
    def delete(self, request, organization_id, contact_id):
        """Removes the person's details. Their unsent drafts are discarded."""
        self.get_contact()
        services.delete_contact(
            organization_id=organization_id, contact_id=contact_id, actor=request.user
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Outreach"])
class ContactSearchView(OutreachAPIView):
    """GET the latest search for contacts (404 before the first). POST starts the lead
    scout for an approved opportunity; poll GET while `status` is queued or running."""

    serializer_class = ContactSearchSerializer
    starts_ai_job = True

    def get(self, request, organization_id, opportunity_id):
        search = selectors.latest_contact_search(self.get_opportunity())
        if search is None:
            return Response({"detail": "No search yet."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ContactSearchSerializer(search).data)

    @extend_schema(request=None, responses={202: ContactSearchSerializer})
    def post(self, request, organization_id, opportunity_id):
        self.get_opportunity()
        search = services.start_contact_search(
            organization_id=organization_id, opportunity_id=opportunity_id, actor=request.user
        )
        search.refresh_from_db()
        return Response(ContactSearchSerializer(search).data, status=status.HTTP_202_ACCEPTED)


# ---------------------------------------------------------------------------
# Outreach emails
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Outreach"],
    parameters=[
        OpenApiParameter(
            "status",
            str,
            description=(
                "Comma-separated statuses, e.g. `draft,failed` for the approval queue. "
                f"One of: {', '.join(EmailStatus.values)}."
            ),
        ),
        OpenApiParameter("opportunity", str, description="Only emails about this opportunity."),
    ],
)
class OutreachListView(OutreachAPIView, generics.ListAPIView):
    """Outreach emails, most recently changed first."""

    serializer_class = OutreachEmailSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return OutreachEmail.objects.none()
        raw = self.request.query_params.get("status", "")
        statuses = [value for value in raw.split(",") if value]
        if any(value not in EmailStatus.values for value in statuses):
            raise ValidationError({"status": "Unknown status."})
        return selectors.outreach_for(
            self.get_readable_organization(),
            statuses=statuses,
            opportunity_id=self.request.query_params.get("opportunity") or None,
        )


@extend_schema(tags=["Outreach"])
class OpportunityOutreachView(OutreachAPIView):
    """Start an email to one of the opportunity's contacts. Without subject and body
    the email agent drafts it (status `drafting`, then `draft`); poll the email."""

    serializer_class = OutreachEmailSerializer
    starts_ai_job = True

    @extend_schema(request=EmailDraftSerializer, responses={201: OutreachEmailSerializer})
    def post(self, request, organization_id, opportunity_id):
        self.get_opportunity()
        data = self.validated(EmailDraftSerializer)
        email = services.draft_email(
            organization_id=organization_id,
            opportunity_id=opportunity_id,
            contact_id=data.pop("contact"),
            actor=request.user,
            **data,
        )
        return self.email_response(email.pk, response_status=status.HTTP_201_CREATED)


@extend_schema(tags=["Outreach"])
class OutreachDetailView(OutreachAPIView):
    serializer_class = OutreachEmailSerializer

    def get(self, request, organization_id, email_id):
        return Response(OutreachEmailSerializer(self.get_email()).data)

    @extend_schema(request=EmailEditSerializer)
    def patch(self, request, organization_id, email_id):
        """Edit a draft (or a failed email) before it is approved."""
        self.get_email()
        email = services.edit_email(
            organization_id=organization_id,
            email_id=email_id,
            actor=request.user,
            **self.validated(EmailEditSerializer),
        )
        return self.email_response(email.pk)


@extend_schema(tags=["Outreach"], request=EmailRedraftSerializer)
class OutreachRedraftView(OutreachAPIView):
    """Let the email agent write it again, with new instructions."""

    serializer_class = OutreachEmailSerializer
    starts_ai_job = True

    def post(self, request, organization_id, email_id):
        self.get_email()
        email = services.redraft_email(
            organization_id=organization_id,
            email_id=email_id,
            actor=request.user,
            **self.validated(EmailRedraftSerializer),
        )
        return self.email_response(email.pk, response_status=status.HTTP_202_ACCEPTED)


@extend_schema(tags=["Outreach"], request=None)
class OutreachDiscardView(OutreachAPIView):
    serializer_class = OutreachEmailSerializer

    def post(self, request, organization_id, email_id):
        self.get_email()
        email = services.discard_email(
            organization_id=organization_id, email_id=email_id, actor=request.user
        )
        return self.email_response(email.pk)


@extend_schema(tags=["Outreach"], request=EmailApproveSerializer)
class OutreachApproveView(OutreachAPIView):
    """Admins: send the email exactly as it is now (status `sending`, then `sent` or
    `failed`). Replies go to the approving admin, who also gets a copy."""

    serializer_class = OutreachEmailSerializer

    def post(self, request, organization_id, email_id):
        self.get_email()
        email = services.approve_email(
            organization_id=organization_id,
            email_id=email_id,
            actor=request.user,
            **self.validated(EmailApproveSerializer),
        )
        return self.email_response(email.pk)


@extend_schema(tags=["Outreach"], responses=ChoiceSerializer(many=True))
class OutreachPurposeListView(OutreachAPIView):
    """What an email can be for (the `purpose` of a new email)."""

    def get(self, request, organization_id):
        self.get_readable_organization()
        return Response([{"value": value, "label": label} for value, label in EmailPurpose.choices])


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------


def _day(params, name, default) -> date:
    raw = params.get(name)
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError({name: "Use a date like 2026-10-31."}) from exc


def _window(params):
    today = timezone.localdate()
    start = _day(params, "from", today)
    end = _day(params, "to", start + timedelta(days=DEFAULT_DEADLINE_DAYS))
    if end < start:
        raise ValidationError({"to": "Must be on or after `from`."})
    if (end - start).days > MAX_DEADLINE_DAYS:
        raise ValidationError({"to": f"At most {MAX_DEADLINE_DAYS} days after `from`."})
    return _midnight(start), _midnight(end + timedelta(days=1))


def _midnight(day: date):
    return timezone.make_aware(datetime.combine(day, time.min))


DEADLINE_PARAMETERS = [
    OpenApiParameter("from", OpenApiTypes.DATE, description="Default: today."),
    OpenApiParameter("to", OpenApiTypes.DATE, description="Inclusive. Default: 90 days on."),
    OpenApiParameter(
        "saved", bool, description="Include saved opportunities not yet approved. Default: true."
    ),
]


class _DeadlineView(OutreachAPIView):
    def deadlines(self):
        params = self.request.query_params
        start, end = _window(params)
        include_saved = params.get("saved", "true").lower() not in ("false", "0", "no")
        return selectors.deadlines_for(
            self.get_readable_organization(),
            start=start,
            end=end,
            include_saved=include_saved,
        )


@extend_schema(tags=["Outreach"], parameters=DEADLINE_PARAMETERS)
class DeadlineListView(_DeadlineView):
    """Deadlines of opportunities being pursued or submitted (and saved ones), soonest
    first, for the calendar."""

    serializer_class = DeadlineSerializer

    @extend_schema(responses=DeadlineSerializer(many=True))
    def get(self, request, organization_id):
        today = timezone.localdate()
        data = [
            {
                "id": opportunity.pk,
                "title": opportunity.title,
                "funder": opportunity.funder,
                "opportunity_type": opportunity.opportunity_type,
                "status": opportunity.status,
                "saved": opportunity.saved,
                "deadline": opportunity.deadline,
                "days_left": (selectors.deadline_day(opportunity) - today).days,
                "source_url": opportunity.source_url,
            }
            for opportunity in self.deadlines().select_related("organization")
        ]
        return Response(DeadlineSerializer(data, many=True).data)


def _ics_response(content: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(content, content_type=ICS)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@extend_schema(
    tags=["Outreach"], parameters=DEADLINE_PARAMETERS, responses={(200, ICS): OpenApiTypes.BINARY}
)
class DeadlineCalendarView(_DeadlineView):
    """The same deadlines as an .ics file to import into any calendar app."""

    def get(self, request, organization_id):
        organization = self.get_readable_organization()
        content = deadlines_ics(
            self.deadlines().select_related("organization"),
            name=f"{organization.name} deadlines",
        )
        return _ics_response(content, f"{slugify(organization.name) or 'leadline'}-deadlines.ics")


@extend_schema(tags=["Outreach"], responses={(200, ICS): OpenApiTypes.BINARY})
class OpportunityCalendarView(OutreachAPIView):
    """One opportunity's deadline as an .ics file (404 when it has no deadline)."""

    def get(self, request, organization_id, opportunity_id):
        opportunity = self.get_opportunity()
        if opportunity.deadline is None:
            return Response(
                {"detail": "This opportunity has no deadline."}, status=status.HTTP_404_NOT_FOUND
            )
        content = deadlines_ics([opportunity], name=opportunity.title)
        return _ics_response(content, f"deadline-{slugify(opportunity.title)[:60] or 'call'}.ics")


@extend_schema(tags=["Outreach"])
class ReminderPreferenceView(OutreachAPIView):
    """Your own deadline reminder emails for this organization. Admins, and whoever
    approved an opportunity, get reminders for it; on by default."""

    serializer_class = ReminderPreferenceSerializer

    def get(self, request, organization_id):
        organization = self.get_readable_organization()
        preference = selectors.reminder_preference(organization, request.user)
        return Response(ReminderPreferenceSerializer(preference).data)

    def patch(self, request, organization_id):
        self.get_readable_organization()
        preference = services.set_reminder_preference(
            organization_id=organization_id,
            actor=request.user,
            **self.validated(ReminderPreferenceSerializer),
        )
        data = {"enabled": preference.enabled, "days_before": preference.days_before}
        return Response(ReminderPreferenceSerializer(data).data)
