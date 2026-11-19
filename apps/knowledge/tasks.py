"""Background jobs (apps.common.jobs): document processing, dossier generation, and
reading a job seeker's CV into their job preferences."""

import io
import logging
from datetime import timedelta

from django.conf import settings

from apps.ai.exceptions import AIConfigError, PromptError, StructuredOutputError
from apps.common.jobs import job
from apps.organizations.choices import ProcessingStatus
from apps.organizations.models import OrganizationDocument

from . import selectors, services
from .chunking import chunk_sections
from .embeddings import get_embedder
from .extraction import ExtractionError, UnsupportedDocument, extract_sections

logger = logging.getLogger(__name__)


@job("knowledge.process_document", timeout=timedelta(minutes=15))
def process_document(document_id):
    """Extract text, chunk and embed a document: pending → processing → processed/failed."""
    job = services.start_processing(document_id)
    if job is None:
        return
    try:
        storage = OrganizationDocument._meta.get_field("file").storage
        with storage.open(job.file_name, "rb") as stored:
            data = io.BytesIO(stored.read())  # the PDF and Office readers need to seek
        sections = extract_sections(
            data, job.original_filename, max_chars=settings.KNOWLEDGE_MAX_DOCUMENT_CHARS
        )
        chunks = chunk_sections(
            sections, size=settings.KNOWLEDGE_CHUNK_SIZE, overlap=settings.KNOWLEDGE_CHUNK_OVERLAP
        )
        vectors = get_embedder().embed_documents([chunk.text for chunk in chunks])
    except UnsupportedDocument as exc:
        services.fail_processing(job, error=str(exc), unsupported=True)
        return
    except ExtractionError as exc:
        logger.warning("Document %s could not be read: %r", document_id, exc.__cause__)
        services.fail_processing(job, error=f"{exc} It may be corrupt; try exporting it again.")
        return
    except AIConfigError:
        logger.exception("Embeddings are misconfigured; document %s not processed", document_id)
        services.fail_processing(job, error="Document processing is not configured yet.")
        return
    except Exception:
        logger.exception("Processing document %s failed", document_id)
        services.fail_processing(job, error="Processing failed unexpectedly. Try again.")
        return
    services.complete_processing(job, chunks, vectors)


@job("knowledge.refresh_dossier")
def refresh_dossier(organization_id, force=False):
    """Regenerate the dossier if its inputs, prompt or model changed (or when forced)."""
    from apps.ai.agents.dossier import generate_dossier, generation_signature

    inputs = selectors.dossier_inputs(organization_id)
    if inputs is None:
        return
    config_error = None
    try:
        signature = generation_signature()
    except (AIConfigError, PromptError) as exc:
        signature, config_error = {}, exc
    source_hash = selectors.inputs_hash(inputs, signature)
    started_at = services.start_dossier_generation(
        organization_id, source_hash=source_hash, force=force
    )
    if started_at is None:
        return

    def fail(error):
        services.fail_dossier(organization_id, started_at=started_at, error=error)

    try:
        if config_error is not None:
            raise config_error
        result = generate_dossier(inputs)
    except (AIConfigError, PromptError):
        logger.exception("The dossier agent is misconfigured")
        return fail("The AI model is not configured yet.")
    except StructuredOutputError:
        logger.warning("Dossier for %s: invalid model output", organization_id, exc_info=True)
        return fail("The AI returned an incomplete dossier. Try again.")
    except Exception:
        logger.exception("Dossier generation for %s failed", organization_id)
        return fail("Dossier generation failed. Try again.")

    saved = services.save_dossier(
        organization_id,
        started_at=started_at,
        content=result.dossier.model_dump(mode="json"),
        source_hash=source_hash,
        prompt_id=result.prompt_id,
        model=result.model,
    )
    # Refreshes requested while this one ran were skipped; catch up if inputs moved on.
    if saved is not None:
        latest = selectors.dossier_inputs(organization_id)
        if latest is not None and selectors.inputs_hash(latest, signature) != source_hash:
            services.schedule_dossier_refresh(organization_id)


@job("knowledge.read_cv")
def read_cv(document_id):
    """Read a job seeker's processed CV for their headline, job titles, skills, years and
    level, and fill in their job preferences (empty fields only; the rest become
    suggestions). Safe to repeat: what is already listed is never suggested again."""
    from apps.ai.agents.cv_reader import read_cv as read
    from apps.ai.config.loader import load_outreach_file, resolve_role
    from apps.ai.graphs.budget import Budget, BudgetExhausted
    from apps.ai.graphs.runtime import TaskRuntime
    from apps.organizations.services import save_cv_facts

    document = (
        OrganizationDocument.objects.select_related("organization")
        .filter(pk=document_id, processing_status=ProcessingStatus.PROCESSED)
        .first()
    )
    if document is None or not document.organization.is_candidate:
        return
    try:
        config = load_outreach_file().cv_reader
        text = selectors.document_text(document, config.max_cv_chars)
        if not text:
            return
        runtime = TaskRuntime(budget=Budget(config.budget.model_dump()))
        facts = read(runtime, cv=text)
    except (AIConfigError, PromptError):
        logger.exception("The CV reader is misconfigured")
        return
    except (StructuredOutputError, BudgetExhausted):
        logger.warning("CV %s could not be read", document_id, exc_info=True)
        return
    except Exception:
        logger.exception("Reading CV %s failed", document_id)
        return
    save_cv_facts(
        document.organization_id,
        document_id=document.pk,
        document_title=document.title,
        facts=facts.model_dump(),
        prompt_id=runtime.prompt_versions.get("cv_reader", ""),
        model=runtime.models_used.get("cv_reader") or resolve_role("cv_reader").model,
    )
