"""Reads of company knowledge, always scoped to one organization."""

import hashlib
import json
from dataclasses import dataclass

from django.conf import settings
from pgvector.django import CosineDistance

from apps.organizations.choices import DocumentCategory, ProcessingStatus
from apps.organizations.models import Organization
from apps.organizations.selectors import preferences_for

from .choices import DossierStatus
from .embeddings import get_embedder
from .models import CompanyDossier, DocumentChunk
from .scope import FOLDERS, KnowledgeScope

# The profile fields the dossier is written from. Contact details are left out: they
# do not change what the organization is, so editing them never triggers a regeneration.
DOSSIER_PROFILE_FIELDS = (
    "name",
    "legal_name",
    "organization_type",
    "registration_number",
    "year_founded",
    "team_size",
    "annual_budget",
    "website",
    "country",
    "city",
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
)
DOSSIER_PREFERENCE_FIELDS = (
    "opportunity_types",
    "geographic_scopes",
    "target_countries",
    "target_regions",
    "focus_areas",
    "keywords",
    "excluded_keywords",
    "preferred_funders",
    "excluded_funders",
    "eligibility",
    "min_funding_amount",
    "max_funding_amount",
    "funding_currency",
    "open_to_partnerships",
    "can_provide_cofunding",
    "eligibility_notes",
    "additional_context",
)
# Without at least one of these (or a processed document) a dossier would be guesswork.
_DESCRIPTIVE_FIELDS = ("description", "mission", "offerings", "track_record")


def dossier_for(organization) -> CompanyDossier:
    """The stored dossier, or an unsaved pending one before the first generation."""
    dossier = CompanyDossier.objects.filter(organization=organization).first()
    return dossier or CompanyDossier(organization=organization, status=DossierStatus.PENDING)


def dossier_inputs(organization_id) -> dict | None:
    """Everything the dossier is written from, or None when there is too little to go on."""
    organization = Organization.objects.filter(pk=organization_id).first()
    if organization is None:
        return None
    profile = {
        field: _plain(getattr(organization, field))
        for field in DOSSIER_PROFILE_FIELDS
        if getattr(organization, field) not in ("", None, [])
    }
    preferences = preferences_for(organization)
    preference_values = {
        field: _plain(getattr(preferences, field))
        for field in DOSSIER_PREFERENCE_FIELDS
        if getattr(preferences, field) not in ("", None, [])
    }
    documents = _document_excerpts(organization)
    if not documents and not any(profile.get(field) for field in _DESCRIPTIVE_FIELDS):
        return None
    return {"profile": profile, "preferences": preference_values, "documents": documents}


def inputs_hash(inputs: dict, signature: dict) -> str:
    payload = json.dumps({"inputs": inputs, **signature}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _plain(value):
    # Decimals (funding amounts) are not JSON serialisable.
    return str(value) if hasattr(value, "as_tuple") else value


def _document_excerpts(organization):
    """The opening of each processed document, newest first, within the prompt budget."""
    per_document = settings.KNOWLEDGE_DOSSIER_DOCUMENT_CHARS
    remaining = settings.KNOWLEDGE_DOSSIER_MAX_DOCUMENT_CHARS
    documents = organization.documents.filter(
        processing_status=ProcessingStatus.PROCESSED
    ).order_by("-created_at", "-id")
    excerpts = []
    for document in documents:
        if remaining <= 0:
            break
        budget = min(per_document, remaining)
        parts, size = [], 0
        for text in document.chunks.order_by("position").values_list("text", flat=True):
            parts.append(text)
            size += len(text) + 1
            if size >= budget:
                break
        excerpt = "\n".join(parts)[:budget]
        if not excerpt:
            continue
        excerpts.append(
            {"title": document.title, "category": document.category, "excerpt": excerpt}
        )
        remaining -= len(excerpt)
    return excerpts


def cv_document(organization, document_id=None):
    """A job seeker's CV: the given document, else their newest processed one filed as a
    CV, else their newest processed document. None when there is none."""
    documents = organization.documents.filter(processing_status=ProcessingStatus.PROCESSED)
    if document_id:
        return documents.filter(pk=document_id).first()
    newest = ("-created_at", "-id")
    return (
        documents.filter(category=DocumentCategory.CV).order_by(*newest).first()
        or documents.order_by(*newest).first()
    )


def document_text(document, max_chars: int) -> str:
    """A document's text rebuilt from its passages, without the overlap each passage
    repeats from the one before it."""
    parts, size, previous = [], 0, ""
    for chunk in document.chunks.order_by("position").values_list("text", flat=True):
        text = _without_overlap(previous, chunk)
        previous = chunk
        parts.append(text)
        size += len(text) + 1
        if size >= max_chars:
            break
    return " ".join(part for part in parts if part)[:max_chars].strip()


MIN_OVERLAP = 20  # shorter matches are chance (passages from different sections)


def _without_overlap(previous: str, text: str) -> str:
    longest = min(len(previous), len(text), settings.KNOWLEDGE_CHUNK_OVERLAP)
    for length in range(longest, MIN_OVERLAP - 1, -1):
        if text.startswith(previous[-length:]):
            return text[length:].strip()
    return text


@dataclass(frozen=True)
class KnowledgeHit:
    document_id: str
    document_title: str
    position: int
    text: str
    metadata: dict
    score: float  # cosine similarity, higher is closer


def search_knowledge(
    organization_id,
    query: str,
    *,
    scope: KnowledgeScope,
    limit: int = 5,
) -> list[KnowledgeHit]:
    """The organization's passages closest in meaning to `query`, within `scope`
    (always filtered by organization as well)."""
    query = query.strip()
    if not query or scope.reads_nothing:
        return []
    vector = get_embedder().embed_query(query)
    qs = DocumentChunk.objects.filter(
        organization_id=organization_id,
        document__processing_status=ProcessingStatus.PROCESSED,
    )
    if scope.mode == FOLDERS:
        qs = qs.filter(document__folder_id__in=scope.folder_ids)
    chunks = (
        qs.select_related("document")
        .annotate(distance=CosineDistance("embedding", vector))
        .order_by("distance")[:limit]
    )
    return [
        KnowledgeHit(
            document_id=chunk.document_id,
            document_title=chunk.document.title,
            position=chunk.position,
            text=chunk.text,
            metadata=chunk.metadata,
            score=round(1 - chunk.distance, 4),
        )
        for chunk in chunks
    ]
