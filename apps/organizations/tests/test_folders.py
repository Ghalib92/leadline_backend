import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse
from rest_framework.test import APIClient

from apps.authentication.tests.factories import UserFactory

from .. import services
from ..models import Organization, OrganizationDocument, OrganizationMembership
from .factories import make_folder

pytestmark = pytest.mark.django_db(transaction=True)

BEFORE = [("organizations", "0003_document_processing_details")]
AFTER = [("organizations", "0004_knowledge_folders")]


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def test_existing_documents_move_into_one_folder_per_category(organization):
    org = organization  # created on the current schema; the row survives migrating back
    old = migrate(BEFORE)
    Doc = old.get_model("organizations", "OrganizationDocument")
    for title, category in [("A", "proposal"), ("B", "proposal"), ("C", "financial")]:
        Doc.objects.create(
            organization_id=org.pk,
            title=title,
            category=category,
            file=f"{title}.pdf",
            original_filename=f"{title}.pdf",
            size=1,
        )

    new = migrate(AFTER)
    Folder = new.get_model("organizations", "KnowledgeFolder")
    Doc = new.get_model("organizations", "OrganizationDocument")
    names = set(Folder.objects.filter(organization_id=org.pk).values_list("name", flat=True))
    assert names == {"Past proposal or application", "Financial statements / budget"}
    assert not Doc.objects.filter(organization_id=org.pk, folder__isnull=True).exists()

    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def test_deleting_an_organization_removes_its_folders_and_documents(user, organization):
    folder = make_folder(organization)
    OrganizationDocument.objects.create(
        organization=organization,
        folder=folder,
        title="A",
        file="a.pdf",
        original_filename="a.pdf",
        size=1,
    )
    Organization.objects.filter(pk=organization.pk).delete()
    assert not OrganizationDocument.objects.exists()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def url(name, organization, **kwargs):
    return reverse(
        f"v1:organizations:{name}", kwargs={"organization_id": organization.pk, **kwargs}
    )


def upload(client, organization, folder_id):
    payload = {
        "title": "Profile",
        "folder_id": folder_id,
        "file": SimpleUploadedFile("p.pdf", b"%PDF-1.4 x"),
    }
    return client.post(url("documents", organization), payload, format="multipart")


def test_admin_creates_renames_and_lists_folders(auth_client, organization):
    created = auth_client.post(
        url("folders", organization), {"name": "Past proposals", "purpose": "Style examples"}
    )
    assert created.status_code == 201, created.data
    folder_id = created.data["id"]
    renamed = auth_client.patch(
        url("folder-detail", organization, folder_id=folder_id), {"name": "Proposals"}
    )
    assert renamed.data["name"] == "Proposals"
    listing = auth_client.get(url("folders", organization)).data
    assert [(f["name"], f["document_count"]) for f in listing] == [("Proposals", 0)]


def test_folder_names_are_unique_per_organization_ignoring_case(auth_client, organization):
    make_folder(organization, "Contracts")
    response = auth_client.post(url("folders", organization), {"name": "contracts"})
    assert response.status_code == 400 and "name" in response.data


def test_only_empty_folders_can_be_deleted(auth_client, organization):
    full, empty = make_folder(organization, "Full"), make_folder(organization, "Empty")
    assert upload(auth_client, organization, full.pk).status_code == 201
    blocked = auth_client.delete(url("folder-detail", organization, folder_id=full.pk))
    assert blocked.status_code == 409
    assert blocked.data["detail"] == "Move or delete its documents first."
    ok = auth_client.delete(url("folder-detail", organization, folder_id=empty.pk))
    assert ok.status_code == 204


def test_upload_needs_a_folder_of_this_organization(auth_client, user, organization):
    assert upload(auth_client, organization, "").status_code == 400
    other = services.create_organization(actor=user, name="Other", country="KE")
    response = upload(auth_client, organization, make_folder(other, "Theirs").pk)
    assert response.status_code == 400 and "folder_id" in response.data


def test_documents_move_between_folders_and_filter_by_folder(auth_client, organization):
    a, b = make_folder(organization, "A"), make_folder(organization, "B")
    doc = upload(auth_client, organization, a.pk).data
    assert doc["folder"] == {"id": a.pk, "name": "A"}
    moved = auth_client.patch(
        url("document-detail", organization, document_id=doc["id"]), {"folder_id": b.pk}
    )
    assert moved.data["folder"]["id"] == b.pk
    in_a = auth_client.get(url("documents", organization), {"folder": a.pk}).data["results"]
    in_b = auth_client.get(url("documents", organization), {"folder": b.pk}).data["results"]
    assert (len(in_a), len(in_b)) == (0, 1)


def test_members_read_folders_but_cannot_change_them(organization):
    member = UserFactory()
    OrganizationMembership.objects.create(organization=organization, user=member)
    client = APIClient()
    client.force_authenticate(member)
    folder = make_folder(organization)
    assert client.get(url("folders", organization)).status_code == 200
    assert client.post(url("folders", organization), {"name": "X"}).status_code == 403
    detail = url("folder-detail", organization, folder_id=folder.pk)
    assert client.patch(detail, {"name": "Y"}).status_code == 403
    assert client.delete(detail).status_code == 403
