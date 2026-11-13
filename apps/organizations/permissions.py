"""Reuse these checks in every future organization-owned API and service."""

from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .models import OrganizationMembership


def require_membership(*, organization, user, admin=False, allow_archived=False):
    if not user or not user.is_authenticated:
        raise NotFound("Organization not found.")
    membership = OrganizationMembership.objects.filter(
        organization=organization,
        user_id=user.pk,
        status=OrganizationMembership.Status.ACTIVE,
        user__is_active=True,
        user__is_verified=True,
    ).first()
    if membership is None:
        raise NotFound("Organization not found.")
    if admin and membership.role != OrganizationMembership.Role.ADMIN:
        raise PermissionDenied("An organization admin is required.")
    if organization.is_archived and not allow_archived:
        raise ValidationError({"detail": "This organization is archived. Restore it first."})
    return membership