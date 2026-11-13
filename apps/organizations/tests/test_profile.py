import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from apps.authentication.tests.factories import UserFactory

from .. import services
from ..models import OpportunityPreferences, OrganizationDocument

pytestmark = pytest.mark.django_db(transaction=True)


def url(name, organization, **kwargs):
    return reverse(
        f"v1:organizations:{name}", kwargs={"organization_id": organization.pk, **kwargs}
    )


def as_user(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def upload(client, organization, *, name="profile.pdf", content=b"%PDF-1.4 example", **data):
    if "folder_id" not in data:
        data["folder_id"] = organization.folders.get_or_create(name="General")[0].pk
    payload = {"title": "Company profile", "file": SimpleUploadedFile(name, content), **data}
    return client.post(url("documents", organization), payload, format="multipart")


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


def test_admin_can_fill_the_full_profile(auth_client, organization):
    response = auth_client.patch(
        url("detail", organization),
        {
            "legal_name": "Acme Africa Ltd",
            "organization_type": "social_enterprise",
            "registration_number": "PVT-123",
            "year_founded": 2019,
            "team_size": "11-50",
            "annual_budget": "250k_1m",
            "city": "Nairobi",
            "mission": "Data for development.",
            "offerings": "Data engineering and AI consulting.",
            "target_beneficiaries": "Smallholder farmers",
            "sectors": ["Agriculture", "agriculture", "Health", ""],
            "sdgs": [2, 3, 2],
            "operating_countries": ["ke", "UG", "ke"],
            "languages": ["EN", "sw"],
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["organization_type"] == "social_enterprise"
    assert response.data["sectors"] == ["Agriculture", "Health"]
    assert response.data["sdgs"] == [2, 3]
    assert response.data["operating_countries"] == ["KE", "UG"]
    assert response.data["languages"] == ["en", "sw"]
    event = organization.events.get(action="organization.updated")
    assert "sectors" in event.metadata["fields"]


@pytest.mark.parametrize(
    "payload",
    [
        {"organization_type": "mafia"},
        {"sdgs": [18]},
        {"operating_countries": ["KEN"]},
        {"year_founded": 3000},
        {"team_size": "7"},
        {"languages": ["english"]},
        {"sectors": [f"sector {n}" for n in range(51)]},
    ],
)
def test_profile_validation(auth_client, organization, payload):
    assert auth_client.patch(url("detail", organization), payload, format="json").status_code == 400


# ---------------------------------------------------------------------------
# Opportunity preferences
# ---------------------------------------------------------------------------


def test_preferences_exist_for_new_organizations(auth_client, organization):
    assert OpportunityPreferences.objects.filter(organization=organization).exists()
    response = auth_client.get(url("preferences", organization))
    assert response.status_code == 200
    assert response.data["opportunity_types"] == []
    assert response.data["funding_currency"] == "USD"


def test_admin_updates_preferences(auth_client, organization):
    response = auth_client.patch(
        url("preferences", organization),
        {
            "opportunity_types": ["grant", "tender", "grant"],
            "geographic_scopes": ["national", "international"],
            "target_countries": ["ke", "TZ"],
            "target_regions": ["east_africa"],
            "keywords": ["climate", "data"],
            "eligibility": ["women_led"],
            "min_funding_amount": "10000",
            "max_funding_amount": "250000",
            "funding_currency": "kes",
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["opportunity_types"] == ["grant", "tender"]
    assert response.data["target_countries"] == ["KE", "TZ"]
    assert response.data["funding_currency"] == "KES"
    assert organization.events.filter(action="preferences.updated").exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"opportunity_types": ["lottery"]},
        {"geographic_scopes": ["galactic"]},
        {"min_funding_amount": "500", "max_funding_amount": "100"},
        {"min_funding_amount": "-1"},
        {"funding_currency": "US"},
    ],
)
def test_preferences_validation(auth_client, organization, payload):
    response = auth_client.patch(url("preferences", organization), payload, format="json")
    assert response.status_code == 400


def test_partial_funding_update_is_checked_against_stored_bound(auth_client, organization):
    endpoint = url("preferences", organization)
    assert (
        auth_client.patch(endpoint, {"max_funding_amount": "100"}, format="json").status_code == 200
    )
    assert (
        auth_client.patch(endpoint, {"min_funding_amount": "500"}, format="json").status_code == 400
    )


def test_members_read_but_cannot_change_preferences(organization, membership):
    client = as_user(membership.user)
    assert client.get(url("preferences", organization)).status_code == 200
    response = client.patch(url("preferences", organization), {"keywords": ["x"]}, format="json")
    assert response.status_code == 403


def test_outsiders_cannot_see_preferences(organization):
    assert as_user(UserFactory()).get(url("preferences", organization)).status_code == 404


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


def test_admin_manages_links(auth_client, organization):
    endpoint = url("links", organization)
    created = auth_client.post(
        endpoint, {"kind": "linkedin", "url": "https://linkedin.com/company/x"}, format="json"
    )
    assert created.status_code == 201, created.data
    duplicate = auth_client.post(endpoint, {"url": "https://linkedin.com/company/x"}, format="json")
    assert duplicate.status_code == 400
    assert (
        auth_client.post(endpoint, {"url": "javascript:alert(1)"}, format="json").status_code == 400
    )
    detail = url("link-detail", organization, link_id=created.data["id"])
    updated = auth_client.patch(detail, {"label": "Company page"}, format="json")
    assert updated.status_code == 200 and updated.data["label"] == "Company page"
    assert auth_client.get(endpoint).data[0]["label"] == "Company page"
    assert auth_client.delete(detail).status_code == 204
    assert auth_client.get(endpoint).data == []


def test_members_cannot_add_links(organization, membership):
    client = as_user(membership.user)
    assert client.get(url("links", organization)).status_code == 200
    response = client.post(url("links", organization), {"url": "https://x.org"}, format="json")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def test_admin_uploads_many_documents_and_members_can_download(
    auth_client, organization, membership
):
    first = upload(auth_client, organization, description="Who we are", category="company_profile")
    assert first.status_code == 201, first.data
    assert first.data["original_filename"] == "profile.pdf"
    assert first.data["size"] == len(b"%PDF-1.4 example")
    assert first.data["processing_status"] == "pending"
    assert first.data["download_url"].endswith(f"/documents/{first.data['id']}/download/")
    for index in range(3):
        assert upload(auth_client, organization, name=f"report-{index}.docx").status_code == 201

    client = as_user(membership.user)
    listing = client.get(url("documents", organization))
    assert listing.status_code == 200 and listing.data["count"] == 4

    download = client.get(first.data["download_url"])
    assert download.status_code == 200
    assert b"".join(download.streaming_content) == b"%PDF-1.4 example"
    assert download["Content-Type"] == "application/pdf"
    assert 'attachment; filename="profile.pdf"' in download["Content-Disposition"]


def test_storage_path_ignores_user_supplied_names(auth_client, organization):
    response = upload(auth_client, organization, name="../../etc/passwd.pdf")
    assert response.status_code == 201
    document = OrganizationDocument.objects.get(pk=response.data["id"])
    assert document.file.name.startswith(f"organizations/{organization.pk}/documents/")
    assert ".." not in document.file.name
    assert document.original_filename == "passwd.pdf"


def test_document_filters(auth_client, organization):
    upload(auth_client, organization, title="Audited accounts 2025", category="financial")
    upload(auth_client, organization, title="Pitch", description="Seed round deck")
    endpoint = url("documents", organization)
    assert auth_client.get(endpoint, {"category": "financial"}).data["count"] == 1
    assert auth_client.get(endpoint, {"search": "seed"}).data["count"] == 1
    assert auth_client.get(endpoint, {"category": "nonsense"}).status_code == 400


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "malware.exe"},
        {"name": "empty.pdf", "content": b""},
        {"title": ""},
        {"category": "secret"},
    ],
)
def test_document_upload_validation(auth_client, organization, overrides):
    assert upload(auth_client, organization, **overrides).status_code == 400
    assert not organization.documents.exists()


def test_document_size_limit(auth_client, organization, settings):
    settings.ORGANIZATION_DOCUMENT_MAX_UPLOAD_MB = 1
    too_big = b"x" * (1024 * 1024 + 1)
    assert upload(auth_client, organization, content=too_big).status_code == 400


def test_members_cannot_upload_edit_or_delete(auth_client, organization, membership):
    document_id = upload(auth_client, organization).data["id"]
    client = as_user(membership.user)
    detail = url("document-detail", organization, document_id=document_id)
    assert upload(client, organization).status_code == 403
    assert client.patch(detail, {"title": "Mine"}, format="json").status_code == 403
    assert client.delete(detail).status_code == 403


def test_download_endpoint_rejects_writes(auth_client, organization):
    document_id = upload(auth_client, organization).data["id"]
    endpoint = url("document-download", organization, document_id=document_id)
    assert auth_client.delete(endpoint).status_code == 405
    assert organization.documents.filter(pk=document_id).exists()


def test_outsiders_cannot_list_or_download(auth_client, organization):
    document_id = upload(auth_client, organization).data["id"]
    client = as_user(UserFactory())
    assert client.get(url("documents", organization)).status_code == 404
    endpoint = url("document-download", organization, document_id=document_id)
    assert client.get(endpoint).status_code == 404


def test_documents_are_scoped_to_their_organization(auth_client, user, organization):
    other = services.create_organization(actor=user, name="Other org")
    document_id = upload(auth_client, other).data["id"]
    endpoint = url("document-detail", organization, document_id=document_id)
    assert auth_client.get(endpoint).status_code == 404
    assert auth_client.delete(endpoint).status_code == 404


def test_admin_edits_metadata_and_delete_removes_the_file(auth_client, organization):
    document_id = upload(auth_client, organization).data["id"]
    detail = url("document-detail", organization, document_id=document_id)
    response = auth_client.patch(
        detail, {"title": "2025 profile", "category": "annual_report"}, format="json"
    )
    assert response.status_code == 200
    assert response.data["title"] == "2025 profile"
    assert response.data["category"] == "annual_report"

    stored_name = OrganizationDocument.objects.get(pk=document_id).file.name
    assert default_storage.exists(stored_name)
    assert auth_client.delete(detail).status_code == 204
    assert not default_storage.exists(stored_name)
    actions = set(organization.events.values_list("action", flat=True))
    assert {"document.uploaded", "document.updated", "document.deleted"} <= actions


def test_archived_organization_documents_are_read_only(auth_client, user, organization):
    document_id = upload(auth_client, organization).data["id"]
    services.archive_organization(organization_id=organization.pk, actor=user)
    assert auth_client.get(url("documents", organization)).status_code == 200
    assert upload(auth_client, organization).status_code == 400
    endpoint = url("document-detail", organization, document_id=document_id)
    assert auth_client.delete(endpoint).status_code == 400


# ---------------------------------------------------------------------------
# Setup checklist
# ---------------------------------------------------------------------------


def test_setup_checklist_tracks_onboarding(auth_client, user, organization, membership):
    endpoint = url("setup", organization)
    initial = auth_client.get(endpoint).data
    assert initial["ready_for_matching"] is False
    assert {step["key"] for step in initial["steps"]} == {
        "basics",
        "focus",
        "preferences",
        "documents",
        "links",
        "team",
    }
    assert next(step for step in initial["steps"] if step["key"] == "team")["done"] is True

    services.update_organization(
        organization_id=organization.pk,
        actor=user,
        organization_type="ngo",
        description="We build data tools.",
        mission="Better data.",
        sectors=["Data"],
    )
    services.update_preferences(
        organization_id=organization.pk,
        actor=user,
        opportunity_types=["grant"],
        geographic_scopes=["international"],
    )
    ready = auth_client.get(endpoint).data
    assert ready["ready_for_matching"] is True
    assert ready["completed"] == 4 and ready["total"] == 6

    services.upload_document(
        organization_id=organization.pk,
        actor=user,
        title="Profile",
        folder_id=organization.folders.get_or_create(name="General")[0].pk,
        file=ContentFile(b"data", name="profile.pdf"),
    )
    assert auth_client.get(endpoint).data["completed"] == 5
    assert as_user(membership.user).get(endpoint).status_code == 200
