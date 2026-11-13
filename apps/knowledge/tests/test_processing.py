from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.authentication.tests.factories import UserFactory
from apps.knowledge import services
from apps.knowledge.models import DocumentChunk
from apps.knowledge.tasks import process_document
from apps.organizations import services as organization_services
from apps.organizations.models import OrganizationDocument

from .conftest import docx_bytes
from .files import make_pdf

# Uploads schedule processing with on_commit, so tests need real commits.
pytestmark = pytest.mark.django_db(transaction=True)


def org_url(name, organization, **kwargs):
    return reverse(
        f"v1:organizations:{name}", kwargs={"organization_id": organization.pk, **kwargs}
    )


def reprocess_url(organization, document_id):
    return reverse(
        "v1:knowledge:document-reprocess",
        kwargs={"organization_id": organization.pk, "document_id": document_id},
    )


def as_user(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def upload(client, organization, name, content):
    response = client.post(
        org_url("documents", organization),
        {
            "title": name,
            "folder_id": organization.folders.get_or_create(name="General")[0].pk,
            "file": SimpleUploadedFile(name, content),
        },
        format="multipart",
    )
    assert response.status_code == 201, response.data
    return OrganizationDocument.objects.get(pk=response.data["id"])


def test_upload_is_extracted_chunked_and_embedded(auth_client, organization, settings):
    settings.KNOWLEDGE_CHUNK_SIZE, settings.KNOWLEDGE_CHUNK_OVERLAP = 60, 10
    content = docx_bytes(
        "We run coding bootcamps for girls in Nairobi.",
        "Since 2019 we have trained 2,000 youth with support from UNICEF.",
    )
    document = upload(auth_client, organization, "profile.docx", content)

    document.refresh_from_db()
    assert document.processing_status == "processed"
    assert document.processed_at is not None and document.processing_error == ""
    chunks = list(document.chunks.order_by("position"))
    assert len(chunks) >= 2
    assert [chunk.position for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.organization_id == organization.pk for chunk in chunks)
    assert len(chunks[0].embedding) == settings.KNOWLEDGE_EMBEDDING_DIMENSIONS
    assert "bootcamps" in chunks[0].text

    detail = auth_client.get(org_url("document-detail", organization, document_id=document.pk))
    assert detail.data["processing_status"] == "processed"
    assert detail.data["processed_at"] is not None


def test_pdf_chunks_record_their_page(auth_client, organization):
    document = upload(auth_client, organization, "report.pdf", make_pdf(["Intro", "Results"]))
    assert list(document.chunks.values_list("text", "metadata")) == [
        ("Intro", {"page": 1}),
        ("Results", {"page": 2}),
    ]


def test_unsupported_and_corrupt_files(auth_client, organization):
    image = upload(auth_client, organization, "logo.png", b"\x89PNG data")
    broken = upload(auth_client, organization, "broken.pdf", b"%PDF-1.4 example")
    image.refresh_from_db()
    broken.refresh_from_db()
    assert image.processing_status == "unsupported"
    assert ".png files can't be read yet" in image.processing_error
    assert broken.processing_status == "failed"
    assert "could not be read" in broken.processing_error
    assert not DocumentChunk.objects.exists()


def test_unexpected_errors_fail_with_a_generic_message(
    auth_client, organization, monkeypatch
):
    def explode(texts):
        raise RuntimeError("secret internals")

    from apps.knowledge.embeddings import get_embedder

    monkeypatch.setattr(get_embedder(), "embed_documents", explode)
    document = upload(auth_client, organization, "notes.txt", b"Some notes")
    document.refresh_from_db()
    assert document.processing_status == "failed"
    assert document.processing_error == "Processing failed unexpectedly. Try again."


def test_admin_reprocesses_a_failed_document(auth_client, organization):
    document = upload(auth_client, organization, "notes.txt", b"\x00")
    document.refresh_from_db()
    assert document.processing_status == "unsupported"  # no text at all

    # The file is fixed in storage (e.g. by support); reprocessing picks it up.
    storage = document.file.storage
    storage.delete(document.file.name)
    storage.save(document.file.name, SimpleUploadedFile("x", b"Real text now"))
    response = auth_client.post(reprocess_url(organization, document.pk))
    assert response.status_code == 202, response.data
    assert response.data["processing_status"] == "pending"  # the job runs after the response

    document.refresh_from_db()
    assert document.processing_status == "processed"
    assert list(document.chunks.values_list("text", flat=True)) == ["Real text now"]
    assert organization.events.filter(action="document.reprocess_requested").exists()


def test_reprocess_permissions(auth_client, organization, member):
    document = upload(auth_client, organization, "notes.txt", b"Text")
    assert as_user(member).post(reprocess_url(organization, document.pk)).status_code == 403
    assert as_user(UserFactory()).post(reprocess_url(organization, document.pk)).status_code == 404
    other = organization_services.create_organization(
        actor=organization.created_by, name="Other"
    )
    assert auth_client.post(reprocess_url(other, document.pk)).status_code == 404


def test_reprocess_is_refused_while_running_but_allowed_once_stale(
    auth_client, organization, settings
):
    document = upload(auth_client, organization, "notes.txt", b"Text")
    OrganizationDocument.objects.filter(pk=document.pk).update(
        processing_status="processing", processing_started_at=timezone.now()
    )
    response = auth_client.post(reprocess_url(organization, document.pk))
    assert response.status_code == 400
    assert response.data["code"] == "processing"

    stale = timezone.now() - timedelta(minutes=settings.KNOWLEDGE_PROCESSING_TIMEOUT_MINUTES + 1)
    OrganizationDocument.objects.filter(pk=document.pk).update(processing_started_at=stale)
    assert auth_client.post(reprocess_url(organization, document.pk)).status_code == 202


def test_a_stale_worker_cannot_overwrite_a_newer_attempt(auth_client, user, organization):
    document = upload(auth_client, organization, "notes.txt", b"Text")
    first = services.start_processing(document.pk)
    assert services.start_processing(document.pk) is None  # already claimed

    OrganizationDocument.objects.filter(pk=document.pk).update(
        processing_started_at=timezone.now() - timedelta(days=1)
    )
    second = services.start_processing(document.pk)
    assert second is not None and second.started_at != first.started_at
    assert services.fail_processing(first, error="late failure") is False
    assert services.complete_processing(second, [], []) is True
    document.refresh_from_db()
    assert document.processing_status == "processed"
    assert document.processing_error == ""


def test_deleting_a_document_removes_its_chunks(auth_client, organization):
    document = upload(auth_client, organization, "notes.txt", b"Text")
    assert DocumentChunk.objects.filter(document_id=document.pk).exists()
    auth_client.delete(org_url("document-detail", organization, document_id=document.pk))
    assert not DocumentChunk.objects.exists()


def test_missing_documents_are_skipped():
    process_document("doesnotexist")  # deleted before the worker ran: no error


def test_reprocess_command(auth_client, organization):
    from django.core.management import call_command

    document = upload(auth_client, organization, "notes.txt", b"Text")
    DocumentChunk.objects.all().delete()
    call_command("knowledge_reprocess", "--now", "--status", "processed")
    assert document.chunks.count() == 1
