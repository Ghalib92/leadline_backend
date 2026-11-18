"""Writes for leads, outreach, client-lead pitches, the call list and deadline reminders.

Nothing is sent to a funder without a person: members draft and edit emails, and only
an admin's explicit approval sends one. Contacts marked do-not-contact are never
emailed. Workers (no caller) claim their job by a status change under a row lock, so
an email is sent at most once. Client-lead pitches are never sent from here at all:
a person sends them from their own phone or email, then logs what happened.
"""

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.common.api.exceptions import Conflict
from apps.common.jobs import enqueue
from apps.opportunities.choices import OpportunityStatus
from apps.organizations.choices import OpportunityType
from apps.organizations.models import OrganizationMembership
from apps.organizations.permissions import require_membership
from apps.organizations.services import lock_organization, record_event

from .choices import (
    CLOSED_OUTCOMES,
    EDITABLE_EMAIL_STATUSES,
    ContactSource,
    EmailStatus,
    PitchStatus,
    SearchStatus,
)
from .models import (
    Contact,
    ContactSearch,
    LeadActivity,
    LeadPitch,
    OutreachEmail,
    ReminderPreference,
)

# Contacts and outreach are for opportunities the organization is going for.
ACTIVE_STATUSES = (OpportunityStatus.PURSUING, OpportunityStatus.SUBMITTED)
CONTACT_FIELDS = frozenset({"name", "role", "email", "phone", "kind", "notes", "do_not_contact"})
EMAIL_FIELDS = frozenset({"subject", "body"})
FOUND_CONTACT_FIELDS = (
    "name",
    "role",
    "email",
    "phone",
    "kind",
    "relevance",
    "confidence",
    "source",
    "source_url",
    "verified",
)


def _pursued_opportunity(organization, opportunity_id):
    opportunity = get_object_or_404(organization.opportunities, pk=opportunity_id)
    if opportunity.status not in ACTIVE_STATUSES:
        raise Conflict("Approve the opportunity first.", code="not_approved")
    return opportunity


def _fit(field, value):
    """Text cut to the column's length (Hunter's job titles can be long)."""
    max_length = Contact._meta.get_field(field).max_length
    return value[:max_length] if isinstance(value, str) and max_length else value


def _stale(started_at) -> bool:
    cutoff = timezone.now() - timedelta(minutes=settings.OUTREACH_JOB_STALE_MINUTES)
    return started_at is not None and started_at < cutoff


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


@transaction.atomic
def add_contact(*, organization_id, opportunity_id, actor, **data) -> Contact:
    organization = lock_organization(organization_id, actor, admin=False)
    opportunity = get_object_or_404(organization.opportunities, pk=opportunity_id)
    if not (data.get("email") or data.get("phone") or data.get("name")):
        raise ValidationError({"email": "Give at least a name, an email or a phone number."})
    try:
        with transaction.atomic():
            contact = Contact.objects.create(
                organization=organization,
                opportunity=opportunity,
                source=ContactSource.MANUAL,
                created_by=actor,
                confidence=100,
                **data,
            )
    except IntegrityError as exc:
        raise Conflict("This email is already a contact here.", code="duplicate") from exc
    record_event(
        organization,
        actor,
        "contact.added",
        target_id=contact.pk,
        metadata={"opportunity": opportunity.pk},
    )
    return contact


@transaction.atomic
def update_contact(*, organization_id, contact_id, actor, **data) -> Contact:
    organization = lock_organization(organization_id, actor, admin=False)
    contact = get_object_or_404(organization.contacts, pk=contact_id)
    changed = [f for f in CONTACT_FIELDS & data.keys() if getattr(contact, f) != data[f]]
    for field in changed:
        setattr(contact, field, data[field])
    if changed:
        try:
            with transaction.atomic():
                contact.save(update_fields=[*changed, "updated_at"])
        except IntegrityError as exc:
            raise Conflict("This email is already a contact here.", code="duplicate") from exc
        record_event(
            organization,
            actor,
            "contact.updated",
            target_id=contact.pk,
            metadata={"fields": sorted(changed)},
        )
    return contact


@transaction.atomic
def delete_contact(*, organization_id, contact_id, actor) -> None:
    """Removes the person's details (e.g. on request). Sent emails keep their address
    as the record of what was sent."""
    organization = lock_organization(organization_id, actor, admin=False)
    contact = get_object_or_404(organization.contacts, pk=contact_id)
    contact.emails.filter(status__in=[EmailStatus.DRAFT, EmailStatus.DRAFTING]).update(
        status=EmailStatus.DISCARDED, updated_at=timezone.now()
    )
    contact.delete()
    record_event(organization, actor, "contact.deleted", target_id=contact_id)


@transaction.atomic
def start_contact_search(*, organization_id, opportunity_id, actor) -> ContactSearch:
    organization = lock_organization(organization_id, actor, admin=False)
    opportunity = _pursued_opportunity(organization, opportunity_id)
    active = opportunity.contact_searches.filter(
        status__in=[SearchStatus.QUEUED, SearchStatus.RUNNING]
    ).first()
    if active is not None:
        if not _stale(active.started_at or active.created_at):
            raise Conflict("Already looking for contacts.", code="busy")
        active.status, active.error = SearchStatus.FAILED, "Stopped responding."
        active.save(update_fields=["status", "error", "updated_at"])
    search = ContactSearch.objects.create(
        organization=organization, opportunity=opportunity, requested_by=actor
    )
    record_event(organization, actor, "contacts.search_requested", target_id=search.pk)

    from .tasks import find_contacts

    enqueue(find_contacts, search.pk)
    return search


@transaction.atomic
def claim_contact_search(search_id) -> ContactSearch | None:
    search = ContactSearch.objects.select_for_update().filter(pk=search_id).first()
    if search is None or search.status != SearchStatus.QUEUED:
        return None
    search.status, search.started_at = SearchStatus.RUNNING, timezone.now()
    search.save(update_fields=["status", "started_at", "updated_at"])
    return search


@transaction.atomic
def finish_contact_search(search_id, *, result=None, error="") -> ContactSearch | None:
    search = ContactSearch.objects.select_for_update().filter(pk=search_id).first()
    if search is None or search.status != SearchStatus.RUNNING:
        return None
    if result is not None:
        existing = {
            email.lower()
            for email in search.opportunity.contacts.exclude(email="").values_list(
                "email", flat=True
            )
        }
        for item in result.contacts:
            if item["email"] and item["email"].lower() in existing:
                continue
            Contact.objects.create(
                organization_id=search.organization_id,
                opportunity_id=search.opportunity_id,
                **{key: _fit(key, item[key]) for key in FOUND_CONTACT_FIELDS},
            )
            existing.add(item["email"].lower())
            search.found += 1
        search.warning = " ".join(result.warnings)[:500]
        search.prompt_versions = result.prompt_versions
        search.models_used = result.models_used
        search.usage = result.usage
    search.status = SearchStatus.FAILED if error else SearchStatus.DONE
    search.error = error[:500]
    search.finished_at = timezone.now()
    search.save()
    return search


# ---------------------------------------------------------------------------
# Outreach emails
# ---------------------------------------------------------------------------


def _contact_for_email(organization, opportunity, contact_id) -> Contact:
    contact = get_object_or_404(organization.contacts, pk=contact_id, opportunity=opportunity)
    if contact.do_not_contact:
        raise Conflict("This contact asked not to be contacted.", code="do_not_contact")
    if not contact.email:
        raise ValidationError({"contact": "This contact has no email address."})
    return contact


@transaction.atomic
def draft_email(
    *,
    organization_id,
    opportunity_id,
    contact_id,
    actor,
    purpose,
    instructions="",
    subject=None,
    body=None,
) -> OutreachEmail:
    """Start an email. With a subject and body it is a person's draft; without, the
    email agent drafts it in the background."""
    organization = lock_organization(organization_id, actor, admin=False)
    opportunity = _pursued_opportunity(organization, opportunity_id)
    contact = _contact_for_email(organization, opportunity, contact_id)
    written = subject is not None and body is not None
    email = OutreachEmail.objects.create(
        organization=organization,
        opportunity=opportunity,
        contact=contact,
        to_email=contact.email,
        to_name=contact.name,
        purpose=purpose,
        instructions=instructions.strip(),
        created_by=actor,
        subject=subject or "",
        body=body or "",
        status=EmailStatus.DRAFT if written else EmailStatus.DRAFTING,
        drafting_started_at=None if written else timezone.now(),
    )
    record_event(
        organization,
        actor,
        "outreach.drafted",
        target_id=email.pk,
        metadata={"to": contact.pk, "by": "person" if written else "ai"},
    )
    if not written:
        _queue_drafting(email)
    return email


def _queue_drafting(email):
    from .tasks import write_email

    enqueue(write_email, email.pk)


@transaction.atomic
def redraft_email(*, organization_id, email_id, actor, instructions="") -> OutreachEmail:
    organization = lock_organization(organization_id, actor, admin=False)
    email = _editable_email(organization, email_id)
    email.status, email.instructions = EmailStatus.DRAFTING, instructions.strip()
    email.drafting_started_at, email.error = timezone.now(), ""
    email.save(
        update_fields=["status", "instructions", "drafting_started_at", "error", "updated_at"]
    )
    _queue_drafting(email)
    return email


def _editable_email(organization, email_id) -> OutreachEmail:
    email = get_object_or_404(organization.outreach_emails.select_for_update(), pk=email_id)
    if email.status == EmailStatus.DRAFTING and _stale(email.drafting_started_at):
        return email  # its worker died; let the person carry on
    if email.status not in EDITABLE_EMAIL_STATUSES:
        raise Conflict(f"This email is {email.get_status_display().lower()}.", code="not_editable")
    return email


@transaction.atomic
def edit_email(*, organization_id, email_id, actor, **data) -> OutreachEmail:
    organization = lock_organization(organization_id, actor, admin=False)
    email = _editable_email(organization, email_id)
    changed = [f for f in EMAIL_FIELDS & data.keys() if getattr(email, f) != data[f]]
    for field in changed:
        setattr(email, field, data[field])
    if changed:
        email.status = EmailStatus.DRAFT
        email.save(update_fields=[*changed, "status", "updated_at"])
    return email


@transaction.atomic
def discard_email(*, organization_id, email_id, actor) -> OutreachEmail:
    organization = lock_organization(organization_id, actor, admin=False)
    email = _editable_email(organization, email_id)
    email.status = EmailStatus.DISCARDED
    email.save(update_fields=["status", "updated_at"])
    record_event(organization, actor, "outreach.discarded", target_id=email.pk)
    return email


@transaction.atomic
def approve_email(*, organization_id, email_id, actor, confirm_repeat=False) -> OutreachEmail:
    """The human approval step: an admin sends the email exactly as shown to them."""
    organization = lock_organization(organization_id, actor)  # admins only
    email = get_object_or_404(organization.outreach_emails.select_for_update(), pk=email_id)
    if email.status not in EDITABLE_EMAIL_STATUSES:
        raise Conflict(f"This email is {email.get_status_display().lower()}.", code="not_sendable")
    if not email.subject.strip() or not email.body.strip():
        raise ValidationError({"body": "Write a subject and a message first."})
    if "[TODO" in email.subject + email.body:
        raise ValidationError({"body": "Fill in the [TODO] parts before sending."})
    if email.contact is None or email.contact.do_not_contact:
        raise Conflict("This contact can no longer be emailed.", code="do_not_contact")
    now = timezone.now()
    approved = organization.outreach_emails.filter(
        status__in=[EmailStatus.SENDING, EmailStatus.SENT]
    )
    if approved.filter(approved_at__gte=now - timedelta(days=1)).count() >= (
        settings.OUTREACH_DAILY_SEND_LIMIT
    ):
        raise Conflict(
            f"At most {settings.OUTREACH_DAILY_SEND_LIMIT} emails can be sent a day. "
            "Try again tomorrow.",
            code="daily_limit",
        )
    recent = approved.filter(
        opportunity_id=email.opportunity_id,
        to_email__iexact=email.to_email,
        approved_at__gte=now - timedelta(days=settings.OUTREACH_REPEAT_DAYS),
    )
    if not confirm_repeat and recent.exists():
        raise Conflict(
            f"{email.to_email} was emailed about this opportunity in the last "
            f"{settings.OUTREACH_REPEAT_DAYS} days. Confirm to send anyway.",
            code="recently_emailed",
        )
    email.status = EmailStatus.SENDING
    email.approved_by, email.approved_at = actor, now
    email.reply_to = actor.email
    email.error = ""
    email.save(
        update_fields=["status", "approved_by", "approved_at", "reply_to", "error", "updated_at"]
    )
    record_event(
        organization,
        actor,
        "outreach.approved",
        target_id=email.pk,
        metadata={"to": email.to_email},
    )

    from .tasks import send_email

    enqueue(send_email, email.pk)
    return email


@transaction.atomic
def save_drafted_email(email_id, started_at, *, subject, body, prompt_id, model) -> bool:
    email = (
        OutreachEmail.objects.select_for_update()
        .filter(pk=email_id, status=EmailStatus.DRAFTING, drafting_started_at=started_at)
        .first()
    )
    if email is None:
        return False
    email.subject, email.body = subject[:200], body
    email.prompt_id, email.model = prompt_id, model
    email.status = EmailStatus.DRAFT
    email.save()
    return True


@transaction.atomic
def fail_drafting(email_id, started_at, error) -> bool:
    updated = OutreachEmail.objects.filter(
        pk=email_id, status=EmailStatus.DRAFTING, drafting_started_at=started_at
    ).update(status=EmailStatus.FAILED, error=error[:500], updated_at=timezone.now())
    return bool(updated)


def claim_sending(email_id) -> OutreachEmail | None:
    """The email to send now, or None. SENDING emails with no sent_at are sent once:
    the status moves to SENT or FAILED in the same locked transaction as the send."""
    return (
        OutreachEmail.objects.select_for_update(of=("self",))  # the joins are nullable
        .select_related("contact", "organization", "approved_by")
        .filter(pk=email_id, status=EmailStatus.SENDING, sent_at__isnull=True)
        .first()
    )


# ---------------------------------------------------------------------------
# Deadline reminders
# ---------------------------------------------------------------------------


@transaction.atomic
def set_reminder_preference(*, organization_id, actor, **data) -> ReminderPreference:
    organization = lock_organization(organization_id, actor, admin=False, allow_archived=True)
    preference, _ = ReminderPreference.objects.get_or_create(
        organization=organization,
        user=actor,
        defaults={"days_before": list(settings.DEADLINE_REMINDER_DAYS)},
    )
    for field in ("enabled", "days_before", "weekly_digest"):
        if field in data:
            setattr(preference, field, data[field])
    preference.save()
    return preference


# ---------------------------------------------------------------------------
# Client leads: pitches and what came of reaching out
# ---------------------------------------------------------------------------

# A lead approved at some point: still being worked, or already won or lost.
WORKED_LEAD_STATUSES = (*ACTIVE_STATUSES, OpportunityStatus.WON, OpportunityStatus.LOST)
PITCH_FIELDS = frozenset({"whatsapp", "email_subject", "email_body", "call_points"})


def _client_lead(organization, opportunity_id):
    opportunity = get_object_or_404(organization.opportunities, pk=opportunity_id)
    if opportunity.opportunity_type != OpportunityType.CLIENT:
        raise Conflict("Pitches and the call list are for client leads.", code="not_a_lead")
    if opportunity.status not in WORKED_LEAD_STATUSES:
        raise Conflict("Approve the lead first.", code="not_approved")
    return opportunity


def _queue_pitch(pitch, *, actor, instructions=""):
    from .tasks import write_lead_pitch

    pitch.status, pitch.error = PitchStatus.WRITING, ""
    pitch.instructions = instructions.strip()
    pitch.requested_by = actor
    pitch.writing_started_at = timezone.now()
    pitch.save()
    enqueue(write_lead_pitch, pitch.pk)


@transaction.atomic
def write_pitch(*, organization_id, opportunity_id, actor, instructions="") -> LeadPitch:
    """(Re)write the pitch for an approved client lead; the person's edits are replaced,
    so the frontend asks first."""
    organization = lock_organization(organization_id, actor, admin=False)
    opportunity = _client_lead(organization, opportunity_id)
    pitch = LeadPitch.objects.select_for_update().filter(opportunity=opportunity).first()
    if pitch and pitch.status == PitchStatus.WRITING and not _stale(pitch.writing_started_at):
        raise Conflict("The pitch is being written.", code="pitch_busy")
    pitch = pitch or LeadPitch(organization=organization, opportunity=opportunity)
    _queue_pitch(pitch, actor=actor, instructions=instructions)
    record_event(
        organization,
        actor,
        "pitch.requested",
        target_id=opportunity.pk,
        metadata={"title": opportunity.title[:120]},
    )
    return pitch


@transaction.atomic
def write_pitch_for_approval(*, organization_id, opportunity_id) -> LeadPitch | None:
    """On approval of a client lead: write its pitch, unless it has one (approving again
    after a rejection keeps the earlier one)."""
    from apps.opportunities.models import Opportunity

    opportunity = (
        Opportunity.objects.select_for_update()
        .filter(pk=opportunity_id, organization_id=organization_id)
        .select_related("organization")
        .first()
    )
    if (
        opportunity is None
        or opportunity.organization.is_archived
        or opportunity.opportunity_type != OpportunityType.CLIENT
        or opportunity.status not in ACTIVE_STATUSES
        or LeadPitch.objects.filter(opportunity=opportunity).exists()
    ):
        return None
    pitch = LeadPitch(organization_id=organization_id, opportunity=opportunity)
    _queue_pitch(pitch, actor=None)
    return pitch


@transaction.atomic
def edit_pitch(*, organization_id, opportunity_id, actor, **data) -> LeadPitch:
    organization = lock_organization(organization_id, actor, admin=False)
    opportunity = _client_lead(organization, opportunity_id)
    pitch = get_object_or_404(LeadPitch.objects.select_for_update(), opportunity=opportunity)
    if pitch.status == PitchStatus.WRITING and not _stale(pitch.writing_started_at):
        raise Conflict("Wait until the pitch is written.", code="pitch_busy")
    changed = [f for f in PITCH_FIELDS & data.keys() if getattr(pitch, f) != data[f]]
    for field in changed:
        setattr(pitch, field, data[field])
    if changed:
        pitch.status, pitch.error = PitchStatus.READY, ""
        pitch.edited_by, pitch.edited_at = actor, timezone.now()
        pitch.save()
    return pitch


@transaction.atomic
def save_written_pitch(pitch_id, started_at, draft, *, prompt_id, model) -> bool:
    """The writer's result, kept only if this is still the job it started (a newer
    request or a person's edit wins)."""
    pitch = (
        LeadPitch.objects.select_for_update()
        .filter(pk=pitch_id, status=PitchStatus.WRITING, writing_started_at=started_at)
        .first()
    )
    if pitch is None:
        return False
    pitch.whatsapp = draft.whatsapp.strip()
    pitch.email_subject = draft.email_subject.strip()[:200]
    pitch.email_body = draft.email_body.strip()
    pitch.call_points = [point.strip() for point in draft.call_points if point.strip()][:4]
    pitch.prompt_id, pitch.model = prompt_id, model
    pitch.status = PitchStatus.READY
    pitch.edited_by, pitch.edited_at = None, None
    pitch.save()
    return True


@transaction.atomic
def fail_pitch(pitch_id, started_at, error) -> bool:
    updated = LeadPitch.objects.filter(
        pk=pitch_id, status=PitchStatus.WRITING, writing_started_at=started_at
    ).update(status=PitchStatus.FAILED, error=error[:500], updated_at=timezone.now())
    return bool(updated)


@transaction.atomic
def log_activity(
    *, organization_id, opportunity_id, actor, channel, outcome, note="", follow_up_on=None
) -> LeadActivity:
    """Record a call, message or visit to a client lead and what came of it."""
    organization = lock_organization(organization_id, actor, admin=False)
    opportunity = _client_lead(organization, opportunity_id)
    if follow_up_on and outcome in CLOSED_OUTCOMES:
        raise ValidationError({"follow_up_on": "A closed lead needs no follow-up."})
    if follow_up_on and follow_up_on < timezone.localdate():
        raise ValidationError({"follow_up_on": "Pick today or a later day."})
    activity = LeadActivity.objects.create(
        organization=organization,
        opportunity=opportunity,
        actor=actor,
        channel=channel,
        outcome=outcome,
        note=note.strip(),
        follow_up_on=follow_up_on,
    )
    record_event(
        organization,
        actor,
        "lead.contacted",
        target_id=opportunity.pk,
        metadata={"title": opportunity.title[:120], "channel": channel, "outcome": outcome},
    )
    return activity


@transaction.atomic
def delete_activity(*, organization_id, activity_id, actor) -> None:
    """For mistakes: a member deletes their own entries, an admin anyone's."""
    organization = lock_organization(organization_id, actor, admin=False)
    activity = get_object_or_404(organization.lead_activities, pk=activity_id)
    if activity.actor_id != actor.pk:
        membership = require_membership(organization=organization, user=actor)
        if membership.role != OrganizationMembership.Role.ADMIN:
            raise PermissionDenied("Only an admin can delete someone else's entry.")
    activity.delete()
