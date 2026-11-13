import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.authentication.tests.factories import UserFactory

from .. import selectors, services
from ..models import OrgSource

pytestmark = pytest.mark.django_db(transaction=True)


def url(name, organization, **kwargs):
    return reverse(
        f"v1:organizations:{name}", kwargs={"organization_id": organization.pk, **kwargs}
    )


def as_user(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


def test_admin_manages_org_sources(auth_client, organization):
    endpoint = url("sources", organization)

    # 1. Create source
    created = auth_client.post(
        endpoint,
        {
            "url": "https://grants.example.org/calls",
            "label": "Example Grants",
            "notes": "Check monthly for climate calls",
            "enabled": True,
        },
        format="json",
    )
    assert created.status_code == 201, created.data
    assert created.data["url"] == "https://grants.example.org/calls"
    assert created.data["label"] == "Example Grants"
    assert created.data["notes"] == "Check monthly for climate calls"
    assert created.data["enabled"] is True
    source_id = created.data["id"]

    # 2. Duplicate URL in same org rejected
    duplicate = auth_client.post(
        endpoint,
        {"url": "https://grants.example.org/calls", "label": "Another label"},
        format="json",
    )
    assert duplicate.status_code == 400

    # 3. Invalid URL rejected
    invalid = auth_client.post(
        endpoint,
        {"url": "javascript:alert(1)"},
        format="json",
    )
    assert invalid.status_code == 400

    # 4. List sources
    listed = auth_client.get(endpoint)
    assert listed.status_code == 200
    assert len(listed.data) == 1
    assert listed.data[0]["id"] == source_id

    # 5. Detail & Update (toggle enabled, change label)
    detail = url("source-detail", organization, source_id=source_id)
    updated = auth_client.patch(
        detail,
        {"enabled": False, "label": "Updated Grants Label"},
        format="json",
    )
    assert updated.status_code == 200
    assert updated.data["enabled"] is False
    assert updated.data["label"] == "Updated Grants Label"

    # 6. Delete source
    deleted = auth_client.delete(detail)
    assert deleted.status_code == 204
    assert auth_client.get(endpoint).data == []


def test_duplicate_url_allowed_across_different_organizations(auth_client, user, organization):
    # Create second organization where user is also admin
    org2 = services.create_organization(
        actor=user,
        name="Second Organization",
        country="KE",
    )
    shared_url = "https://funder.org/opportunities"

    res1 = auth_client.post(
        url("sources", organization),
        {"url": shared_url, "label": "Funder"},
        format="json",
    )
    assert res1.status_code == 201

    res2 = auth_client.post(
        url("sources", org2),
        {"url": shared_url, "label": "Funder for org2"},
        format="json",
    )
    assert res2.status_code == 201


def test_admin_cannot_update_url_to_existing_in_same_org(auth_client, organization):
    src1 = auth_client.post(
        url("sources", organization),
        {"url": "https://a.com/grants"},
        format="json",
    )
    src2 = auth_client.post(
        url("sources", organization),
        {"url": "https://b.com/grants"},
        format="json",
    )
    assert src1.status_code == 201 and src2.status_code == 201

    detail2 = url("source-detail", organization, source_id=src2.data["id"])
    res = auth_client.patch(detail2, {"url": "https://a.com/grants"}, format="json")
    assert res.status_code == 400


def test_member_can_read_but_cannot_mutate_sources(organization, membership):
    client = as_user(membership.user)
    source = OrgSource.objects.create(
        organization=organization,
        url="https://source.example.com",
        label="Test",
        created_by=membership.user,
    )

    # Read list
    res = client.get(url("sources", organization))
    assert res.status_code == 200
    assert len(res.data) == 1

    # Read detail
    detail = url("source-detail", organization, source_id=source.pk)
    assert client.get(detail).status_code == 200

    # Create forbidden
    assert client.post(
        url("sources", organization),
        {"url": "https://new.example.com"},
        format="json",
    ).status_code == 403

    # Update forbidden
    assert client.patch(detail, {"enabled": False}, format="json").status_code == 403

    # Delete forbidden
    assert client.delete(detail).status_code == 403


def test_outsider_cannot_access_sources(organization):
    outsider = as_user(UserFactory())
    assert outsider.get(url("sources", organization)).status_code == 404


# ---------------------------------------------------------------------------
# Services & selectors tests
# ---------------------------------------------------------------------------


def test_services_create_update_remove_events(user, organization):
    source = services.add_org_source(
        organization_id=organization.pk,
        actor=user,
        url="https://foundation.org/calls",
        label="Foundation",
        notes="Important",
        enabled=True,
    )
    assert source.organization == organization
    assert organization.events.filter(action="source.added").exists()

    updated = services.update_org_source(
        organization_id=organization.pk,
        source_id=source.pk,
        actor=user,
        label="Foundation Updated",
        enabled=False,
    )
    assert updated.label == "Foundation Updated"
    assert updated.enabled is False
    assert organization.events.filter(action="source.updated").exists()

    services.remove_org_source(
        organization_id=organization.pk,
        source_id=source.pk,
        actor=user,
    )
    assert not OrgSource.objects.filter(pk=source.pk).exists()
    assert organization.events.filter(action="source.removed").exists()


def test_selectors_org_sources_for(user, organization):
    s1 = services.add_org_source(
        organization_id=organization.pk,
        actor=user,
        url="https://s1.org",
        enabled=True,
    )
    s2 = services.add_org_source(
        organization_id=organization.pk,
        actor=user,
        url="https://s2.org",
        enabled=False,
    )

    all_sources = list(selectors.org_sources_for(organization))
    assert s1 in all_sources and s2 in all_sources

    enabled_sources = list(selectors.org_sources_for(organization, enabled_only=True))
    assert s1 in enabled_sources
    assert s2 not in enabled_sources
