"""Organization writes and authorization rules.

Every organization mutation locks the parent row first, then rechecks access.
On PostgreSQL this serializes last-admin changes and invitation acceptance,
resend and revocation, including requests that started with stale permissions.
Views and future background jobs must use these services for mutations.
"""

import hashlib
import os
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, Throttled, ValidationError

from apps.common.api.exceptions import Conflict

from .emails import schedule_invitation, schedule_notification
from .models import (
    KnowledgeFolder,
    OpportunityPreferences,
    Organization,
    OrganizationDocument,
    OrganizationEvent,
    OrganizationInvitation,
    OrganizationLink,
    OrganizationMembership,
    OrgSource,
)
from .permissions import require_membership
from .signals import context_changed, document_uploaded

PROFILE_FIELDS = frozenset(
    {
        "name",
        "legal_name",
        "organization_type",
        "registration_number",
        "year_founded",
        "team_size",
        "annual_budget",
        "website",
        "contact_email",
        "phone",
        "country",
        "city",
        "address",
        "description",
        "mission",
        "vision",
        "offerings",
        "target_beneficiaries",
        "track_record",
        "past_funding",
        "industry",
        "sectors",
        "sdgs",
        "operating_countries",
        "languages",
    }
)


def _event(organization, actor, action, *, target_id=None, metadata=None):
    OrganizationEvent.objects.create(
        organization=organization,
        actor=actor,
        action=action,
        target_id=target_id,
        metadata=metadata or {},
    )


def _locked_organization(organization_id, actor, *, admin=True, allow_archived=False):
    organization = get_object_or_404(Organization.objects.select_for_update(), pk=organization_id)
    require_membership(
        organization=organization, user=actor, admin=admin, allow_archived=allow_archived
    )
    return organization


# Services in other apps (knowledge, discovery, ...) follow the same lock-then-check
# order and write to the same activity history through these.
lock_organization = _locked_organization
record_event = _event


def _context_changed(organization):
    context_changed.send(sender=Organization, organization_id=organization.pk)


@transaction.atomic
def create_organization(*, actor, invitations=(), **data):
    """Create the organization with `actor` as its first admin.

    `invitations` is an optional list of {email, role, message} sent in the same
    transaction: a bad invitation rolls back the whole organization.
    """
    if not get_user_model().objects.filter(pk=actor.pk, is_active=True, is_verified=True).exists():
        raise PermissionDenied("An active account with a verified email is required.")
    organization = Organization.objects.create(created_by=actor, **data)
    OrganizationMembership.objects.create(organization=organization, user=actor, role="admin")
    OpportunityPreferences.objects.create(organization=organization)
    _event(organization, actor, "organization.created")
    _invite_many(organization, actor, invitations)
    schedule_notification(
        to=actor.email,
        subject=f"Your organization is ready | {settings.PRODUCT_NAME}",
        template="organizations/emails/organization_created",
        context={"organization_name": organization.name, "recipient_name": actor.get_short_name()},
    )
    return organization


@transaction.atomic
def update_organization(*, organization_id, actor, **data):
    organization = _locked_organization(organization_id, actor)
    if set(data) - PROFILE_FIELDS:
        raise ValidationError("Unsupported organization fields.")
    changed = []
    for field, value in data.items():
        if getattr(organization, field) != value:
            setattr(organization, field, value)
            changed.append(field)
    if changed:
        organization.save(update_fields=[*changed, "updated_at"])
        _event(organization, actor, "organization.updated", metadata={"fields": sorted(changed)})
        _context_changed(organization)
    return organization


@transaction.atomic
def archive_organization(*, organization_id, actor, restore=False):
    organization = _locked_organization(organization_id, actor, allow_archived=True)
    if organization.is_archived == (not restore):
        return organization
    now = timezone.now()
    organization.archived_at = None if restore else now
    organization.save(update_fields=["archived_at", "updated_at"])
    revoked = 0
    if not restore:
        revoked = organization.invitations.filter(status="pending").update(
            status="revoked", responded_at=now, updated_at=now
        )
    _event(
        organization,
        actor,
        "organization.restored" if restore else "organization.archived",
        metadata={"revoked_invitations": revoked},
    )
    return organization


def _protect_last_admin(organization, member, *, role, status):
    if (
        member.role == "admin"
        and member.status == "active"
        and (role != "admin" or status != "active")
    ):
        remaining = organization.memberships.filter(
            role="admin", status="active", user__is_active=True
        ).exclude(pk=member.pk)
        if not remaining.exists():
            raise ValidationError(
                {
                    "detail": (
                        "An organization must keep at least one active admin. "
                        "Promote another member first."
                    ),
                    "code": "last_admin",
                }
            )


def _notify_member(organization, member, *, change):
    schedule_notification(
        to=member.user.email,
        subject=f"Your organization access was updated | {settings.PRODUCT_NAME}",
        template="organizations/emails/membership_changed",
        context={
            "organization_name": organization.name,
            "change": change,
            "role": member.get_role_display(),
            "status": member.get_status_display(),
        },
    )


def _cancel_pending_for_member(organization, member):
    now = timezone.now()
    organization.invitations.filter(email__iexact=member.user.email, status="pending").update(
        status="revoked", responded_at=now, updated_at=now
    )


@transaction.atomic
def update_membership(*, organization_id, membership_id, actor, role=None, status=None):
    organization = _locked_organization(organization_id, actor)
    member = get_object_or_404(organization.memberships.select_related("user"), pk=membership_id)
    if member.status == "removed":
        raise ValidationError(
            {"detail": "This member was removed. Send a new invitation to restore access."}
        )
    new_role = role if role is not None else member.role
    new_status = status if status is not None else member.status
    if new_role not in OrganizationMembership.Role.values or new_status not in {
        "active",
        "inactive",
    }:
        raise ValidationError("Invalid role or status.")
    if (new_role, new_status) == (member.role, member.status):
        return member
    _protect_last_admin(organization, member, role=new_role, status=new_status)
    before = {"role": member.role, "status": member.status}
    member.role, member.status = new_role, new_status
    member.save(update_fields=["role", "status", "updated_at"])
    if new_status != "active":
        _cancel_pending_for_member(organization, member)
    _event(
        organization,
        actor,
        "membership.updated",
        target_id=member.pk,
        metadata={"before": before, "after": {"role": new_role, "status": new_status}},
    )
    _notify_member(
        organization,
        member,
        change=(
            "Your access has been paused. Contact an organization admin to reactivate it."
            if new_status == "inactive"
            else "Your organization role or access has been updated."
        ),
    )
    return member


@transaction.atomic
def remove_membership(*, organization_id, actor, membership_id=None, leave=False):
    organization = _locked_organization(organization_id, actor, admin=not leave)
    query = {"user": actor} if leave else {"pk": membership_id}
    member = get_object_or_404(organization.memberships.select_related("user"), **query)
    if member.status == "removed":
        return
    _protect_last_admin(organization, member, role=member.role, status="removed")
    member.status = "removed"
    member.save(update_fields=["status", "updated_at"])
    _cancel_pending_for_member(organization, member)
    _event(
        organization,
        actor,
        "membership.left" if leave else "membership.removed",
        target_id=member.pk,
    )
    _notify_member(
        organization,
        member,
        change=(
            "You have left this organization."
            if leave
            else "An admin has removed your organization access."
        ),
    )


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ensure_invitable(organization, email):
    existing = (
        organization.memberships.filter(user__email__iexact=email).exclude(status="removed").first()
    )
    if existing is not None:
        message = (
            "This person is already an active member."
            if existing.status == "active"
            else "This member is inactive. Reactivate their membership instead."
        )
        raise ValidationError({"email": message})


def _set_invitation_token(invitation):
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    invitation.token_hash = hash_token(token)
    invitation.expires_at = now + timedelta(hours=settings.ORGANIZATION_INVITATION_EXPIRY_HOURS)
    invitation.status = "pending"
    invitation.email_status = "pending"
    invitation.last_email_attempt_at = now
    invitation.last_sent_at = None
    invitation.responded_at = None
    invitation.save()
    schedule_invitation(invitation, token)


def _invite(organization, actor, *, email, role="member", message=""):
    """Create one invitation on an organization the caller has already locked."""
    email = get_user_model().objects.normalize_email(email)
    if role not in OrganizationMembership.Role.values:
        raise ValidationError({"role": "Invalid organization role."})
    _ensure_invitable(organization, email)
    now = timezone.now()
    # Expired rows must no longer occupy the one-pending-invitation constraint.
    organization.invitations.filter(
        email__iexact=email, status="pending", expires_at__lte=now
    ).update(status="expired", updated_at=now)
    if organization.invitations.filter(email__iexact=email, status="pending").exists():
        raise ValidationError(
            {"email": "A pending invitation already exists. Resend or revoke it."}
        )
    invitation = OrganizationInvitation(
        organization=organization, invited_by=actor, email=email, role=role, message=message
    )
    _set_invitation_token(invitation)
    _event(
        organization,
        actor,
        "invitation.created",
        target_id=invitation.pk,
        metadata={"email": email, "role": role},
    )
    return invitation


def _invite_many(organization, actor, invitations):
    """All-or-nothing: errors are reported per item, like a DRF list serializer."""
    created, errors = [], []
    for item in invitations:
        try:
            created.append(_invite(organization, actor, **item))
            errors.append({})
        except ValidationError as exc:
            errors.append(exc.detail)
    if any(errors):
        raise ValidationError({"invitations": errors})
    return created


@transaction.atomic
def invite_member(*, organization_id, actor, email, role="member", message=""):
    organization = _locked_organization(organization_id, actor)
    return _invite(organization, actor, email=email, role=role, message=message)


@transaction.atomic
def invite_members(*, organization_id, actor, invitations):
    organization = _locked_organization(organization_id, actor)
    return _invite_many(organization, actor, invitations)


@transaction.atomic
def resend_invitation(*, organization_id, invitation_id, actor):
    organization = _locked_organization(organization_id, actor)
    invitation = get_object_or_404(organization.invitations, pk=invitation_id)
    if invitation.status not in {"pending", "expired"}:
        raise ValidationError({"detail": "Only pending or expired invitations can be resent."})
    _ensure_invitable(organization, invitation.email)
    if (
        organization.invitations.filter(email__iexact=invitation.email, status="pending")
        .exclude(pk=invitation.pk)
        .exists()
    ):
        raise ValidationError({"detail": "A newer invitation exists. Use that invitation instead."})
    if invitation.last_email_attempt_at:
        wait = (
            invitation.last_email_attempt_at
            + timedelta(seconds=settings.ORGANIZATION_INVITATION_RESEND_COOLDOWN_SECONDS)
            - timezone.now()
        ).total_seconds()
        if wait > 0:
            raise Throttled(wait=wait, detail="Please wait before resending this invitation.")
    invitation.invited_by = actor
    _set_invitation_token(invitation)
    _event(organization, actor, "invitation.resent", target_id=invitation.pk)
    return invitation


@transaction.atomic
def revoke_invitation(*, organization_id, invitation_id, actor):
    organization = _locked_organization(organization_id, actor)
    invitation = get_object_or_404(organization.invitations, pk=invitation_id)
    if invitation.status == "revoked":
        return invitation
    if invitation.status not in {"pending", "expired"}:
        raise ValidationError({"detail": "This invitation has already been answered."})
    invitation.status = "revoked"
    invitation.responded_at = timezone.now()
    invitation.save(update_fields=["status", "responded_at", "updated_at"])
    _event(organization, actor, "invitation.revoked", target_id=invitation.pk)
    return invitation


def _invitation_lookup(*, actor, token=None, invitation_id=None):
    if token is not None:
        return {"token_hash": hash_token(token)}
    # Without the emailed token, only the addressee may reach an invitation by id.
    return {"pk": invitation_id, "email__iexact": actor.email}


def _invalid_invitation():
    return ValidationError(
        {
            "detail": "This invitation is invalid, expired or no longer available.",
            "code": "invalid_invitation",
        }
    )


def _live_invitation(*, actor, **lookup):
    invitation = (
        OrganizationInvitation.objects.select_related("organization", "invited_by")
        .filter(
            **_invitation_lookup(actor=actor, **lookup),
            status="pending",
            expires_at__gt=timezone.now(),
            organization__archived_at__isnull=True,
        )
        .first()
    )
    if invitation is None:
        raise _invalid_invitation()
    if not actor.is_active or not actor.is_verified or invitation.email != actor.email:
        raise PermissionDenied(
            "Sign in with the verified email address that received this invitation."
        )
    return invitation


def _own_answered_invitation(*, actor, **lookup):
    """The recipient's invitation once it is no longer actionable, so the UI can show why."""
    if not actor.is_active or not actor.is_verified:
        return None
    invitation = (
        OrganizationInvitation.objects.select_related("organization", "invited_by")
        .filter(**_invitation_lookup(actor=actor, **lookup))
        .first()
    )
    if invitation is None or invitation.email != actor.email:
        return None
    return None if invitation.effective_status == "pending" else invitation


def preview_invitation(*, actor, token=None, invitation_id=None):
    lookup = {"token": token, "invitation_id": invitation_id}
    return _own_answered_invitation(actor=actor, **lookup) or _live_invitation(
        actor=actor, **lookup
    )


@transaction.atomic
def respond_to_invitation(*, actor, token=None, invitation_id=None, accept=True):
    lookup = {"token": token, "invitation_id": invitation_id}
    # Discover the parent, then follow the same lock order as every admin mutation.
    candidate = OrganizationInvitation.objects.filter(
        **_invitation_lookup(actor=actor, **lookup)
    ).first()
    if candidate is None:
        raise _invalid_invitation()
    get_object_or_404(Organization.objects.select_for_update(), pk=candidate.organization_id)
    # Serialize against email changes and re-read mailbox verification after waiting.
    actor = get_user_model().objects.select_for_update().get(pk=actor.pk)
    if accept:
        # Accepting twice (for example once from the email, again from the list) is a no-op.
        answered = _own_answered_invitation(actor=actor, **lookup)
        if answered is not None and answered.status == "accepted":
            member = answered.organization.memberships.filter(
                user=actor, status="active"
            ).first()
            if answered.accepted_by_id == actor.pk and member is not None:
                return member
    invitation = _live_invitation(actor=actor, **lookup)
    organization = invitation.organization
    member = None
    if accept:
        member = organization.memberships.filter(user=actor).first()
        if member and member.status == "inactive":
            raise PermissionDenied("Your membership is inactive. Ask an admin to reactivate it.")
        if member and member.status == "active":
            # A stale invitation must never alter the role of an existing member.
            raise ValidationError({"detail": "You are already an active member."})
        if member:
            member.role, member.status, member.joined_at = invitation.role, "active", timezone.now()
            member.save(update_fields=["role", "status", "joined_at", "updated_at"])
        else:
            member = OrganizationMembership.objects.create(
                organization=organization, user=actor, role=invitation.role
            )
        invitation.accepted_by = actor
    invitation.status = "accepted" if accept else "declined"
    invitation.responded_at = timezone.now()
    invitation.save(update_fields=["status", "responded_at", "accepted_by", "updated_at"])
    _event(
        organization,
        actor,
        "invitation.accepted" if accept else "invitation.declined",
        target_id=invitation.pk,
        metadata={"role": invitation.role},
    )
    if accept:
        _notify_member(
            organization, member, change="You have joined this organization. Welcome aboard."
        )
    return member if accept else invitation


# ---------------------------------------------------------------------------
# Opportunity preferences
# ---------------------------------------------------------------------------


@transaction.atomic
def update_preferences(*, organization_id, actor, **data):
    organization = _locked_organization(organization_id, actor)
    preferences, _ = OpportunityPreferences.objects.get_or_create(organization=organization)
    changed = []
    for field, value in data.items():
        if getattr(preferences, field) != value:
            setattr(preferences, field, value)
            changed.append(field)
    if changed:
        preferences.save(update_fields=[*changed, "updated_at"])
        _event(organization, actor, "preferences.updated", metadata={"fields": sorted(changed)})
        _context_changed(organization)
    return preferences


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


def _ensure_unique_link(organization, url, *, exclude=None):
    duplicates = organization.links.filter(url=url)
    if exclude is not None:
        duplicates = duplicates.exclude(pk=exclude.pk)
    if duplicates.exists():
        raise ValidationError({"url": "This link has already been added."})


@transaction.atomic
def add_link(*, organization_id, actor, url, kind="website", label=""):
    organization = _locked_organization(organization_id, actor)
    _ensure_unique_link(organization, url)
    link = OrganizationLink.objects.create(
        organization=organization, url=url, kind=kind, label=label
    )
    _event(organization, actor, "link.added", target_id=link.pk, metadata={"url": url})
    return link


@transaction.atomic
def update_link(*, organization_id, link_id, actor, **data):
    organization = _locked_organization(organization_id, actor)
    link = get_object_or_404(organization.links, pk=link_id)
    if "url" in data:
        _ensure_unique_link(organization, data["url"], exclude=link)
    for field, value in data.items():
        setattr(link, field, value)
    link.save()
    _event(organization, actor, "link.updated", target_id=link.pk, metadata={"url": link.url})
    return link


@transaction.atomic
def remove_link(*, organization_id, link_id, actor):
    organization = _locked_organization(organization_id, actor)
    link = get_object_or_404(organization.links, pk=link_id)
    _event(organization, actor, "link.removed", target_id=link.pk, metadata={"url": link.url})
    link.delete()


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


def _ensure_unique_folder(organization, name, *, exclude=None):
    duplicates = organization.folders.filter(name__iexact=name)
    if exclude is not None:
        duplicates = duplicates.exclude(pk=exclude.pk)
    if duplicates.exists():
        raise ValidationError({"name": "A folder with this name already exists."})


def _folder(organization, folder_id):
    folder = organization.folders.filter(pk=folder_id).first() if folder_id else None
    if folder is None:
        raise ValidationError({"folder_id": "Choose one of this organization's folders."})
    return folder


@transaction.atomic
def create_folder(*, organization_id, actor, name, purpose=""):
    organization = _locked_organization(organization_id, actor)
    _ensure_unique_folder(organization, name)
    folder = KnowledgeFolder.objects.create(
        organization=organization, name=name, purpose=purpose, created_by=actor
    )
    _event(organization, actor, "folder.created", target_id=folder.pk, metadata={"name": name})
    return folder


@transaction.atomic
def update_folder(*, organization_id, folder_id, actor, **data):
    organization = _locked_organization(organization_id, actor)
    folder = get_object_or_404(organization.folders, pk=folder_id)
    if "name" in data:
        _ensure_unique_folder(organization, data["name"], exclude=folder)
    changed = [field for field, value in data.items() if getattr(folder, field) != value]
    for field in changed:
        setattr(folder, field, data[field])
    if changed:
        folder.save(update_fields=[*changed, "updated_at"])
        _event(
            organization,
            actor,
            "folder.updated",
            target_id=folder.pk,
            metadata={"fields": sorted(changed)},
        )
    return folder


@transaction.atomic
def delete_folder(*, organization_id, folder_id, actor):
    organization = _locked_organization(organization_id, actor)
    folder = get_object_or_404(organization.folders, pk=folder_id)
    if folder.documents.exists():
        raise Conflict("Move or delete its documents first.", code="folder_not_empty")
    _event(
        organization, actor, "folder.deleted", target_id=folder.pk, metadata={"name": folder.name}
    )
    folder.delete()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@transaction.atomic
def upload_document(
    *, organization_id, actor, file, title, folder_id, description="", category="other"
):
    organization = _locked_organization(organization_id, actor)
    document = OrganizationDocument(
        organization=organization,
        folder=_folder(organization, folder_id),
        title=title,
        description=description,
        category=category,
        file=file,
        original_filename=os.path.basename(file.name)[:255],
        content_type=(getattr(file, "content_type", "") or "")[:100],
        size=file.size,
        uploaded_by=actor,
    )
    # Saving writes the file to storage. A later rollback leaves an orphaned file,
    # never a row without a file.
    document.save()
    _event(
        organization,
        actor,
        "document.uploaded",
        target_id=document.pk,
        metadata={"title": title, "category": category},
    )
    document_uploaded.send(
        sender=OrganizationDocument, organization_id=organization.pk, document_id=document.pk
    )
    return document


@transaction.atomic
def update_document(*, organization_id, document_id, actor, **data):
    organization = _locked_organization(organization_id, actor)
    document = get_object_or_404(organization.documents, pk=document_id)
    if "folder_id" in data:
        data["folder"] = _folder(organization, data.pop("folder_id"))
    changed = [field for field, value in data.items() if getattr(document, field) != value]
    for field in changed:
        setattr(document, field, data[field])
    if changed:
        document.save(update_fields=[*changed, "updated_at"])
        _event(
            organization,
            actor,
            "document.updated",
            target_id=document.pk,
            metadata={"fields": sorted(changed)},
        )
        _context_changed(organization)
    return document


@transaction.atomic
def delete_document(*, organization_id, document_id, actor):
    organization = _locked_organization(organization_id, actor)
    document = get_object_or_404(organization.documents, pk=document_id)
    storage, name = document.file.storage, document.file.name
    _event(
        organization,
        actor,
        "document.deleted",
        target_id=document.pk,
        metadata={"title": document.title},
    )
    document.delete()
    _context_changed(organization)
    # Remove the stored file only once the row deletion is durable.
    transaction.on_commit(lambda: storage.delete(name), robust=True)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def _ensure_unique_source(organization, url, *, exclude=None):
    duplicates = organization.org_sources.filter(url=url)
    if exclude is not None:
        duplicates = duplicates.exclude(pk=exclude.pk)
    if duplicates.exists():
        raise ValidationError({"url": "This source has already been added."})


@transaction.atomic
def add_org_source(*, organization_id, actor, url, label="", notes="", enabled=True):
    organization = _locked_organization(organization_id, actor)
    _ensure_unique_source(organization, url)
    source = OrgSource.objects.create(
        organization=organization,
        url=url,
        label=label,
        notes=notes,
        enabled=enabled,
        created_by=actor,
    )
    _event(organization, actor, "source.added", target_id=source.pk, metadata={"url": url})
    return source


@transaction.atomic
def update_org_source(*, organization_id, source_id, actor, **data):
    organization = _locked_organization(organization_id, actor)
    source = get_object_or_404(organization.org_sources, pk=source_id)
    if "url" in data:
        _ensure_unique_source(organization, data["url"], exclude=source)
    for field, value in data.items():
        setattr(source, field, value)
    source.save()
    _event(
        organization,
        actor,
        "source.updated",
        target_id=source.pk,
        metadata={"url": source.url},
    )
    return source


@transaction.atomic
def remove_org_source(*, organization_id, source_id, actor):
    organization = _locked_organization(organization_id, actor)
    source = get_object_or_404(organization.org_sources, pk=source_id)
    _event(
        organization,
        actor,
        "source.removed",
        target_id=source.pk,
        metadata={"url": source.url},
    )
    source.delete()

