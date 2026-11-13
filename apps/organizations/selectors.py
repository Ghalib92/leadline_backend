from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404

from .models import Organization, OrganizationInvitation, OrganizationMembership


def organizations_for_user(user):
    """Only active memberships; archived organizations remain visible to their admins."""
    if not user.is_authenticated or not user.is_active or not user.is_verified:
        return Organization.objects.none()
    memberships = OrganizationMembership.objects.filter(user=user, status="active")
    return (
        Organization.objects.filter(pk__in=memberships.values("organization_id"))
        .filter(
            Q(archived_at__isnull=True)
            | Q(pk__in=memberships.filter(role="admin").values("organization_id"))
        )
        .annotate(
            active_member_count=Count(
                "memberships",
                filter=Q(memberships__status="active", memberships__user__is_active=True),
            )
        )
        .prefetch_related(Prefetch("memberships", queryset=memberships, to_attr="my_memberships"))
        .order_by("name", "id")
    )


def organization_for_user(*, user, organization_id):
    return get_object_or_404(organizations_for_user(user), pk=organization_id)


def received_invitations(user):
    """Invitations addressed to the user's verified email, answered ones included."""
    if not user.is_authenticated or not user.is_active or not user.is_verified:
        return OrganizationInvitation.objects.none()
    return (
        OrganizationInvitation.objects.filter(
            email__iexact=user.email, organization__archived_at__isnull=True
        )
        .exclude(status="revoked")
        .select_related("organization", "invited_by")
    )


def members_for_user(*, organization, user):
    from .permissions import require_membership

    membership = require_membership(organization=organization, user=user)
    members = organization.memberships.select_related("user__profile")
    if membership.role != OrganizationMembership.Role.ADMIN:
        members = members.filter(status="active", user__is_active=True)
    return members

def preferences_for(organization):
    from .models import OpportunityPreferences

    return OpportunityPreferences.objects.get_or_create(organization=organization)[0]


def setup_status(organization):
    """Onboarding checklist. Required steps are the minimum opportunity matching needs."""
    preferences = preferences_for(organization)
    has_team = (
        organization.memberships.filter(status="active").count() > 1
        or organization.invitations.filter(status="pending").exists()
    )
    steps = [
        (
            "basics",
            "Add your organization type, country and description",
            True,
            all([organization.organization_type, organization.country, organization.description]),
        ),
        (
            "focus",
            "Describe what you do and the sectors you work in",
            True,
            bool((organization.mission or organization.offerings) and organization.sectors),
        ),
        (
            "preferences",
            "Choose the opportunity types and geographic scope you want",
            True,
            bool(preferences.opportunity_types and preferences.geographic_scopes),
        ),
        ("documents", "Upload at least one document", False, organization.documents.exists()),
        (
            "links",
            "Add your website or other links",
            False,
            bool(organization.website) or organization.links.exists(),
        ),
        ("team", "Invite your team", False, has_team),
    ]
    steps = [
        {"key": key, "label": label, "required": required, "done": done}
        for key, label, required, done in steps
    ]
    completed = sum(step["done"] for step in steps)
    return {
        "steps": steps,
        "completed": completed,
        "total": len(steps),
        "percent": round(100 * completed / len(steps)),
        "ready_for_matching": all(step["done"] for step in steps if step["required"]),
    }


def org_sources_for(organization, *, enabled_only: bool = False):
    sources = organization.org_sources.all()
    if enabled_only:
        sources = sources.filter(enabled=True)
    return sources

