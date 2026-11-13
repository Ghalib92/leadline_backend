import uuid

from shortuuid.django_fields import ShortUUIDField

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.authentication import services as auth_services
from apps.authentication.tests.factories import UserFactory

from .. import services
from ..models import Organization, OrganizationMembership

pytestmark = pytest.mark.django_db(transaction=True)


def url(name, organization=None, **kwargs):
    if organization:
        kwargs["organization_id"] = organization.pk
    return reverse(f"v1:organizations:{name}", kwargs=kwargs)


def as_user(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.parametrize(
    "name", ["list", "invitation-preview", "invitation-accept", "invitation-decline"]
)
def test_anonymous_requests_are_rejected(api_client, name):
    response = (
        api_client.get(url(name))
        if name == "list"
        else api_client.post(url(name), {"token": "A" * 43}, format="json")
    )
    assert response.status_code == 401


@pytest.mark.parametrize("flags", [{"is_verified": False}, {"is_active": False}])
def test_only_active_verified_accounts_may_create(api_client, flags):
    api_client.force_authenticate(user=UserFactory(**flags))
    response = api_client.post(url("list"), {"name": "Not allowed"}, format="json")
    assert response.status_code == 403
    assert not Organization.objects.exists()


def test_create_multiple_organizations_makes_creator_admin_only_in_each(auth_client, user):
    first = auth_client.post(
        url("list"),
        {
            "name": "Research Team",
            "country": "ke",
            "role": "member",
            "is_staff": True,
            "created_by": str(uuid.uuid4()),
        },
        format="json",
    )
    second = auth_client.post(url("list"), {"name": "Research Team"}, format="json")
    assert first.status_code == second.status_code == 201
    assert first.data["slug"] != second.data["slug"]
    assert first.data["country"] == "KE"
    assert str(first.data["created_by"]) == str(user.pk)
    assert first.data["my_membership"]["role"] == "admin"
    assert first.data["active_member_count"] == 1
    assert user.organization_memberships.filter(role="admin", status="active").count() == 2
    user.refresh_from_db()
    assert not user.is_staff and not user.is_superuser
    assert auth_client.get(url("list")).data["count"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "   "},
        {"name": "x" * 201},
        {"name": "Org", "country": "K3"},
        {"name": "Org", "website": "javascript:alert(1)"},
        {"name": "Org", "description": "x" * 5001},
    ],
)
def test_organization_input_validation(auth_client, payload):
    assert auth_client.post(url("list"), payload, format="json").status_code == 400
    assert not Organization.objects.exists()


def test_admin_can_update_details_but_slug_and_creator_stay_stable(auth_client, user, organization):
    response = auth_client.patch(
        url("detail", organization),
        {
            "name": "New name",
            "website": "https://example.com",
            "industry": "Research",
            "country": "tz",
            "contact_email": "CONTACT@EXAMPLE.COM",
            "slug": "injected",
            "archived_at": "2026-01-01T00:00:00Z",
            "created_by": str(uuid.uuid4()),
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["name"] == "New name"
    assert response.data["slug"] == organization.slug
    assert response.data["country"] == "TZ"
    assert response.data["contact_email"] == "contact@example.com"
    assert response.data["archived_at"] is None
    assert str(response.data["created_by"]) == str(user.pk)


@pytest.mark.parametrize("name", ["detail", "members", "invitations", "activity"])
def test_outsiders_cannot_read_an_organization(organization, name):
    client = as_user(UserFactory())
    assert client.get(url(name, organization)).status_code == 404
    assert client.get(url("list")).data["count"] == 0


def test_platform_staff_do_not_implicitly_become_organization_admins(organization):
    client = as_user(UserFactory(is_staff=True, is_superuser=True))
    assert client.get(url("detail", organization)).status_code == 404
    assert client.post(url("archive", organization)).status_code == 404


def test_member_can_read_active_roster_but_cannot_manage(auth_client, organization, membership):
    inactive = OrganizationMembership.objects.create(
        organization=organization, user=UserFactory(), status="inactive"
    )
    client = as_user(membership.user)
    assert client.get(url("detail", organization)).status_code == 200
    roster = client.get(url("members", organization))
    assert roster.status_code == 200 and roster.data["count"] == 2
    assert all(member["status"] == "active" for member in roster.data["results"])
    assert "otp_code" not in str(roster.data) and "password" not in str(roster.data)
    assert (
        client.get(url("member-detail", organization, membership_id=inactive.pk)).status_code == 404
    )
    assert (
        client.patch(url("detail", organization), {"name": "Oops"}, format="json").status_code
        == 403
    )
    assert (
        client.post(
            url("invitations", organization), {"email": "x@example.com"}, format="json"
        ).status_code
        == 403
    )
    assert client.get(url("invitations", organization)).status_code == 403
    assert client.get(url("activity", organization)).status_code == 403
    assert (
        client.patch(
            url("member-detail", organization, membership_id=membership.pk),
            {"role": "admin"},
            format="json",
        ).status_code
        == 403
    )
    assert (
        client.delete(url("member-detail", organization, membership_id=membership.pk)).status_code
        == 403
    )
    assert auth_client.get(url("members", organization)).data["count"] == 3


def test_membership_change_blocks_existing_jwt_only_for_that_organization(
    auth_client, organization, membership
):
    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {auth_services.issue_session(membership.user)['access']}"
    )
    own = services.create_organization(actor=membership.user, name="Personal team")
    assert client.get(url("detail", organization)).status_code == 200
    endpoint = url("member-detail", organization, membership_id=membership.pk)
    response = auth_client.patch(endpoint, {"status": "inactive"}, format="json")
    assert response.status_code == 200 and response.data["status"] == "inactive"
    assert client.get(url("detail", organization)).status_code == 404
    assert client.get(url("members", organization)).status_code == 404
    assert client.get(url("detail", own)).status_code == 200
    assert client.get(url("list")).data["count"] == 1
    membership.user.refresh_from_db()
    assert membership.user.is_active
    assert auth_client.patch(endpoint, {"status": "active"}, format="json").status_code == 200
    assert client.get(url("detail", organization)).status_code == 200


def test_admin_can_promote_demote_and_remove_members(auth_client, organization, membership):
    endpoint = url("member-detail", organization, membership_id=membership.pk)
    assert auth_client.patch(endpoint, {"role": "admin"}, format="json").status_code == 200
    assert as_user(membership.user).get(url("activity", organization)).status_code == 200
    assert auth_client.patch(endpoint, {"role": "member"}, format="json").status_code == 200
    assert as_user(membership.user).get(url("activity", organization)).status_code == 403
    assert auth_client.delete(endpoint).status_code == 204
    assert auth_client.delete(endpoint).status_code == 204
    membership.refresh_from_db()
    assert membership.status == "removed"
    assert as_user(membership.user).get(url("detail", organization)).status_code == 404
    # Rejoining after removal requires a fresh invitation and consent.
    assert auth_client.patch(endpoint, {"status": "active"}, format="json").status_code == 400


@pytest.mark.parametrize(
    "method,payload",
    [
        ("patch", {"role": "member"}),
        ("patch", {"status": "inactive"}),
        ("delete", None),
        ("leave", None),
    ],
)
def test_last_admin_cannot_be_lost(auth_client, user, organization, method, payload):
    member = organization.memberships.get(user=user)
    endpoint = url("member-detail", organization, membership_id=member.pk)
    if method == "leave":
        response = auth_client.post(url("leave", organization))
    elif method == "delete":
        response = auth_client.delete(endpoint)
    else:
        response = auth_client.patch(endpoint, payload, format="json")
    assert response.status_code == 400
    assert response.data["code"] == "last_admin"
    member.refresh_from_db()
    assert (member.role, member.status) == ("admin", "active")


def test_disabled_account_does_not_count_as_remaining_admin(auth_client, user, organization):
    OrganizationMembership.objects.create(
        organization=organization, role="admin", user=UserFactory(is_active=False)
    )
    response = auth_client.post(url("leave", organization))
    assert response.status_code == 400 and response.data["code"] == "last_admin"


def test_admin_can_leave_after_promoting_another_member(auth_client, organization, membership):
    auth_client.patch(
        url("member-detail", organization, membership_id=membership.pk),
        {"role": "admin"},
        format="json",
    )
    assert auth_client.post(url("leave", organization)).status_code == 204
    assert auth_client.get(url("detail", organization)).status_code == 404
    assert as_user(membership.user).get(url("detail", organization)).status_code == 200


def test_regular_member_can_leave(organization, membership):
    client = as_user(membership.user)
    assert client.post(url("leave", organization)).status_code == 204
    assert client.get(url("detail", organization)).status_code == 404


def test_membership_ids_are_scoped_to_parent_organization(
    auth_client, user, organization, membership
):
    other = services.create_organization(actor=user, name="Other team")
    endpoint = url("member-detail", other, membership_id=membership.pk)
    assert auth_client.get(endpoint).status_code == 404
    assert auth_client.patch(endpoint, {"role": "admin"}, format="json").status_code == 404
    assert auth_client.delete(endpoint).status_code == 404
    membership.refresh_from_db()
    assert membership.role == "member" and membership.status == "active"


@pytest.mark.parametrize("payload", [{"role": "owner"}, {"status": "removed"}, {}, {"user": "bad"}])
def test_member_update_validates_allowed_fields(auth_client, organization, membership, payload):
    response = auth_client.patch(
        url("member-detail", organization, membership_id=membership.pk), payload, format="json"
    )
    assert response.status_code == 400


def test_archiving_preserves_records_and_requires_admin_to_restore(
    auth_client, organization, membership
):
    client = as_user(membership.user)
    response = auth_client.post(url("archive", organization))
    assert response.status_code == 200 and response.data["is_archived"]
    assert client.get(url("detail", organization)).status_code == 404
    assert client.get(url("list")).data["count"] == 0
    assert auth_client.get(url("list")).data["count"] == 1
    assert auth_client.get(url("activity", organization)).status_code == 200
    assert (
        auth_client.patch(url("detail", organization), {"name": "No"}, format="json").status_code
        == 400
    )
    assert (
        auth_client.post(
            url("invitations", organization), {"email": "x@example.com"}, format="json"
        ).status_code
        == 400
    )
    assert client.post(url("restore", organization)).status_code == 403
    assert auth_client.post(url("restore", organization)).status_code == 200
    assert client.get(url("detail", organization)).status_code == 200


def test_activity_is_paginated_and_records_actor_and_changes(
    auth_client, user, organization, membership
):
    auth_client.patch(
        url("member-detail", organization, membership_id=membership.pk),
        {"status": "inactive"},
        format="json",
    )
    response = auth_client.get(url("activity", organization), {"page_size": 1})
    assert response.status_code == 200
    assert response.data["count"] == 2 and response.data["next"]
    event = response.data["results"][0]
    assert event["action"] == "membership.updated"
    assert str(event["actor"]) == str(user.pk)
    assert event["metadata"]["after"]["status"] == "inactive"
    assert auth_client.post(url("activity", organization), {}, format="json").status_code == 405


def test_nonexistent_and_malformed_organization_ids_do_not_crash(auth_client):
    assert (
        auth_client.get(
            reverse("v1:organizations:detail", kwargs={"organization_id": "Zz" * 6})
        ).status_code
        == 404
    )
    assert auth_client.get("/api/v1/organizations/not-a-uuid/").status_code == 404