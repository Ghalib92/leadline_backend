"""What the agents know about an organization: document chunks and the company dossier."""

from django.conf import settings
from django.db import models
from pgvector.django import HnswIndex, VectorField

from apps.common.models import UUIDTimestampedModel
from apps.organizations.models import Organization, OrganizationDocument

from .choices import DossierStatus


class DocumentChunk(UUIDTimestampedModel):
    """A passage of an uploaded document with its embedding, for retrieval."""

    # Denormalised from the document so every search filters on one indexed column.
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="document_chunks"
    )
    document = models.ForeignKey(
        OrganizationDocument, on_delete=models.CASCADE, related_name="chunks"
    )
    position = models.PositiveIntegerField()  # order within the document, from 0
    text = models.TextField()
    # Where the passage came from, e.g. {"page": 3}, {"sheet": "Budget"} or {"slide": 7}.
    metadata = models.JSONField(default=dict, blank=True)
    embedding = VectorField(dimensions=settings.KNOWLEDGE_EMBEDDING_DIMENSIONS)

    class Meta:
        ordering = ["document", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "position"], name="knowledge_chunk_unique_position"
            ),
        ]
        indexes = [
            models.Index(fields=["organization"], name="knowledge_chunk_org"),
            HnswIndex(
                name="knowledge_chunk_embedding",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return f"{self.document_id} #{self.position}"


class CompanyDossier(UUIDTimestampedModel):
    """The structured summary of an organization that every agent works from.

    Content always holds the last successful generation, so readers keep seeing it
    while a newer one is generating or after an attempt failed.
    """

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="dossier"
    )
    status = models.CharField(
        max_length=12, choices=DossierStatus.choices, default=DossierStatus.PENDING
    )
    content = models.JSONField(null=True, blank=True)  # apps.ai.schemas.dossier.Dossier
    version = models.PositiveIntegerField(default=0)  # bumped on every successful generation
    # Hash of the inputs, prompt and model behind `content`; unchanged inputs are not
    # regenerated.
    source_hash = models.CharField(max_length=64, blank=True, default="")
    prompt_id = models.CharField(max_length=60, blank=True, default="")  # e.g. "dossier/v1"
    model = models.CharField(max_length=120, blank=True, default="")
    generated_at = models.DateTimeField(null=True, blank=True)
    # Identifies the generation in progress; a stale worker must not overwrite a newer one.
    generation_started_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=DossierStatus.values),
                name="knowledge_dossier_valid_status",
            ),
        ]

    def __str__(self):
        return f"Dossier for {self.organization_id} (v{self.version}, {self.status})"
