"""System checks for document processing and embedding settings."""

from django.conf import settings
from django.core.checks import Error, Warning, register

from .embeddings import registered_embedders


@register()
def knowledge_configuration(app_configs, **kwargs):
    messages = []
    if settings.KNOWLEDGE_EMBEDDING_PROVIDER not in registered_embedders():
        messages.append(
            Error(
                f"KNOWLEDGE_EMBEDDING_PROVIDER {settings.KNOWLEDGE_EMBEDDING_PROVIDER!r} is not "
                "a registered embedder.",
                hint=f"Use one of: {', '.join(sorted(registered_embedders()))}.",
                id="knowledge.E001",
            )
        )
    elif settings.KNOWLEDGE_EMBEDDING_PROVIDER == "hashing" and not settings.DEBUG:
        messages.append(
            Warning(
                "KNOWLEDGE_EMBEDDING_PROVIDER is 'hashing', which is only meant for tests; "
                "document search quality will be poor.",
                id="knowledge.W001",
            )
        )
    if settings.KNOWLEDGE_EMBEDDING_DIMENSIONS < 1:
        messages.append(
            Error("KNOWLEDGE_EMBEDDING_DIMENSIONS must be positive.", id="knowledge.E002")
        )
    if not 0 <= settings.KNOWLEDGE_CHUNK_OVERLAP < settings.KNOWLEDGE_CHUNK_SIZE:
        messages.append(
            Error(
                "KNOWLEDGE_CHUNK_SIZE must be positive and larger than KNOWLEDGE_CHUNK_OVERLAP "
                "(which cannot be negative).",
                id="knowledge.E003",
            )
        )
    positive = (
        "KNOWLEDGE_MAX_DOCUMENT_CHARS",
        "KNOWLEDGE_PROCESSING_TIMEOUT_MINUTES",
        "KNOWLEDGE_DOSSIER_DOCUMENT_CHARS",
        "KNOWLEDGE_DOSSIER_MAX_DOCUMENT_CHARS",
    )
    for name in positive:
        if getattr(settings, name) < 1:
            messages.append(Error(f"{name} must be at least 1.", id="knowledge.E004"))
    if settings.KNOWLEDGE_DOSSIER_DELAY_SECONDS < 0:
        messages.append(
            Error("KNOWLEDGE_DOSSIER_DELAY_SECONDS cannot be negative.", id="knowledge.E005")
        )
    return messages
