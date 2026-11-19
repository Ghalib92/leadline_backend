"""Writes for company knowledge: document processing state, chunks and the dossier.

User actions lock the organization and recheck the caller's role, like every other
service. Background jobs have no caller: they lock the row they update and carry a
start timestamp that identifies their attempt, so a stale worker finishing late can
never overwrite a newer attempt's result.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common.jobs import enqueue
from apps.organizations.choices import DocumentCategory, ProcessingStatus
from apps.organizations.models import Organization, OrganizationDocument
from apps.organizations.services import lock_organization, record_event

from . import selectors
from .choices import DossierStatus
from .models import CompanyDossier, DocumentChunk


def _is_running(started_at):
    timeout = timedelta(minutes=settings.KNOWLEDGE_PROCESSING_TIMEOUT_MINUTES)
    return started_at is not None and started_at > timezone.now() - timeout


# ---------------------------------------------------------------------------
# Scheduling (always after commit, so workers see the saved rows)
# ---------------------------------------------------------------------------


def schedule_processing(document_id):
    from .tasks import process_document

    enqueue(process_document, document_id)


def schedule_dossier_refresh(organization_id, *, force=False, delay=None):
    from .tasks import refresh_dossier

    seconds = settings.KNOWLEDGE_DOSSIER_DELAY_SECONDS if delay is None else delay
    enqueue(refresh_dossier, organization_id, force, delay=timedelta(seconds=seconds))


# ---------------------------------------------------------------------------
# User actions
# ---------------------------------------------------------------------------


@transaction.atomic
def request_reprocessing(*, organization_id, document_id, actor):
    organization = lock_organization(organization_id, actor)
    document = get_object_or_404(organization.documents.select_for_update(), pk=document_id)
    if document.processing_status == ProcessingStatus.PROCESSING and _is_running(
        document.processing_started_at
    ):
        raise ValidationError(
            {"detail": "This document is already being processed.", "code": "processing"}
        )
    document.processing_status = ProcessingStatus.PENDING
    document.processing_error = ""
    document.processing_started_at = None
    document.save(
        update_fields=[
            "processing_status",
            "processing_error",
            "processing_started_at",
            "updated_at",
        ]
    )
    record_event(organization, actor, "document.reprocess_requested", target_id=document.pk)
    schedule_processing(document.pk)
    return document


@transaction.atomic
def request_dossier_refresh(*, organization_id, actor):
    """Regenerate now, even if nothing changed (e.g. after a failure or for a new model)."""
    organization = lock_organization(organization_id, actor)
    dossier, _ = CompanyDossier.objects.select_for_update().get_or_create(
        organization=organization
    )
    if dossier.status == DossierStatus.GENERATING and _is_running(
        dossier.generation_started_at
    ):
        raise ValidationError(
            {"detail": "The dossier is already being generated.", "code": "generating"}
        )
    if selectors.dossier_inputs(organization.pk) is None:
        raise ValidationError(
            {
                "detail": (
                    "Add a description, mission or offerings to the profile, or upload a "
                    "document, before generating the dossier."
                ),
                "code": "insufficient_context",
            }
        )
    # Shown as generating at once, so the UI starts polling. No start time yet: the
    # job claims it when a worker picks it up.
    dossier.status = DossierStatus.GENERATING
    dossier.generation_started_at = None
    dossier.error = ""
    dossier.save(update_fields=["status", "generation_started_at", "error", "updated_at"])
    record_event(organization, actor, "dossier.refresh_requested")
    schedule_dossier_refresh(organization.pk, force=True, delay=0)
    return dossier


# ---------------------------------------------------------------------------
# Document processing (background)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessingJob:
    document_id: str
    organization_id: str
    started_at: datetime
    file_name: str  # storage name
    original_filename: str


@transaction.atomic
def start_processing(document_id) -> ProcessingJob | None:
    """Claim the document for this worker; None if it is gone or already being processed."""
    document = OrganizationDocument.objects.select_for_update().filter(pk=document_id).first()
    if document is None:
        return None
    if document.processing_status == ProcessingStatus.PROCESSING and _is_running(
        document.processing_started_at
    ):
        return None
    now = timezone.now()
    document.processing_status = ProcessingStatus.PROCESSING
    document.processing_started_at = now
    document.processing_error = ""
    document.save(
        update_fields=[
            "processing_status",
            "processing_started_at",
            "processing_error",
            "updated_at",
        ]
    )
    return ProcessingJob(
        document_id=document.pk,
        organization_id=document.organization_id,
        started_at=now,
        file_name=document.file.name,
        original_filename=document.original_filename,
    )


def _claimed_document(job):
    return (
        OrganizationDocument.objects.select_for_update()
        .filter(
            pk=job.document_id,
            processing_status=ProcessingStatus.PROCESSING,
            processing_started_at=job.started_at,
        )
        .first()
    )


@transaction.atomic
def complete_processing(job: ProcessingJob, chunks, vectors) -> bool:
    """Replace the document's chunks; False if a newer attempt took over."""
    document = _claimed_document(job)
    if document is None:
        return False
    DocumentChunk.objects.filter(document=document).delete()
    DocumentChunk.objects.bulk_create(
        [
            DocumentChunk(
                organization_id=document.organization_id,
                document=document,
                position=chunk.position,
                text=chunk.text,
                metadata=chunk.metadata,
                embedding=vector,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ],
        batch_size=500,
    )
    document.processing_status = ProcessingStatus.PROCESSED
    document.processed_at = timezone.now()
    document.save(update_fields=["processing_status", "processed_at", "updated_at"])
    schedule_dossier_refresh(document.organization_id)
    if _is_cv(document):
        from .tasks import read_cv

        enqueue(read_cv, document.pk)
    return True


def _is_cv(document) -> bool:
    """A job seeker's CV: a document filed as one, or their first document (a CV
    uploaded as "Other")."""
    if not document.organization.is_candidate:
        return False
    if document.category == DocumentCategory.CV:
        return True
    others = document.organization.documents.exclude(pk=document.pk)
    return not others.filter(processing_status=ProcessingStatus.PROCESSED).exists()


@transaction.atomic
def fail_processing(job: ProcessingJob, *, error, unsupported=False) -> bool:
    document = _claimed_document(job)
    if document is None:
        return False
    had_chunks = DocumentChunk.objects.filter(document=document).delete()[0] > 0
    document.processing_status = (
        ProcessingStatus.UNSUPPORTED if unsupported else ProcessingStatus.FAILED
    )
    document.processing_error = error[:255]
    document.processed_at = None
    document.save(
        update_fields=["processing_status", "processing_error", "processed_at", "updated_at"]
    )
    if had_chunks:  # the document no longer counts towards the dossier
        schedule_dossier_refresh(document.organization_id)
    return True


# ---------------------------------------------------------------------------
# Dossier generation (background)
# ---------------------------------------------------------------------------


@transaction.atomic
def start_dossier_generation(organization_id, *, source_hash, force=False) -> datetime | None:
    """Claim the dossier for this worker; None when there is nothing to do.

    Unchanged inputs are skipped unless `force`. A generation already running is never
    doubled up: the running one checks for newer inputs when it finishes.
    """
    # Locking the organization (not just the dossier) also serialises the first creation.
    organization = Organization.objects.select_for_update().filter(pk=organization_id).first()
    if organization is None:
        return None
    dossier, _ = CompanyDossier.objects.get_or_create(organization=organization)
    if dossier.status == DossierStatus.GENERATING and _is_running(
        dossier.generation_started_at
    ):
        return None
    if not force and dossier.status == DossierStatus.READY and dossier.source_hash == source_hash:
        return None
    now = timezone.now()
    dossier.status = DossierStatus.GENERATING
    dossier.generation_started_at = now
    dossier.error = ""
    dossier.save(update_fields=["status", "generation_started_at", "error", "updated_at"])
    return now


def _claimed_dossier(organization_id, started_at):
    return (
        CompanyDossier.objects.select_for_update()
        .select_related("organization")
        .filter(
            organization_id=organization_id,
            status=DossierStatus.GENERATING,
            generation_started_at=started_at,
        )
        .first()
    )


@transaction.atomic
def save_dossier(
    organization_id, *, started_at, content, source_hash, prompt_id, model
) -> CompanyDossier | None:
    dossier = _claimed_dossier(organization_id, started_at)
    if dossier is None:
        return None
    dossier.status = DossierStatus.READY
    dossier.content = content
    dossier.version += 1
    dossier.source_hash = source_hash
    dossier.prompt_id = prompt_id
    dossier.model = model
    dossier.generated_at = timezone.now()
    dossier.save()
    record_event(
        dossier.organization,
        None,
        "dossier.generated",
        target_id=dossier.pk,
        metadata={"version": dossier.version, "prompt_id": prompt_id, "model": model},
    )
    return dossier


@transaction.atomic
def fail_dossier(organization_id, *, started_at, error) -> bool:
    dossier = _claimed_dossier(organization_id, started_at)
    if dossier is None:
        return False
    dossier.status = DossierStatus.FAILED
    dossier.error = error[:255]
    dossier.save(update_fields=["status", "error", "updated_at"])
    return True
